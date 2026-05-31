import time
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import get_user_model

User = get_user_model()


class SessionTimeoutMiddleware:
    """
    Middleware to handle session timeouts and cleanup inactive sessions
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout_seconds = getattr(settings, 'SESSION_TIMEOUT_SECONDS', 3600)
        self.inactivity_timeout = getattr(settings, 'SESSION_INACTIVITY_TIMEOUT', 1800)
    
    def __call__(self, request):
        # Process the request
        response = self.get_response(request)
        
        # Check for JWT authentication and session timeout
        if hasattr(request, 'user') and request.user.is_authenticated:
            import logging
            logger = logging.getLogger(__name__)
            logger.debug(f"SessionTimeoutMiddleware: Processing authenticated user {request.user.email}")
            self._check_session_timeout(request)
        
        return response
    
    def _check_session_timeout(self, request):
        """
        Check if the current session has timed out
        """
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            # Get JWT token from request headers
            auth_header = request.META.get('HTTP_AUTHORIZATION', '')
            logger.debug(f"SessionTimeoutMiddleware: Auth header: {auth_header[:50]}..." if auth_header else "No auth header")
            
            if not auth_header.startswith('Bearer '):
                logger.debug("SessionTimeoutMiddleware: No Bearer token found")
                return
            
            raw_token = auth_header.split(' ')[1]
            logger.debug(f"SessionTimeoutMiddleware: Processing token for timeout check")
            
            # Parse token to get JTI
            from rest_framework_simplejwt.tokens import UntypedToken
            from rest_framework_simplejwt.exceptions import TokenError
            
            try:
                token = UntypedToken(raw_token)
                jti = token.get('jti')
                
                if jti:
                    self._check_user_session_timeout(request.user, jti)
                    
            except TokenError:
                pass  # Token invalid, authentication will handle this
                
        except Exception:
            pass  # Don't break the request flow for timeout checks
    
    def _check_user_session_timeout(self, user, jti):
        """
        Check specific user session for timeout and cleanup if needed
        """
        try:
            from core.tenant_management.models import UserSession
            from core.tenant_management.services.jwt_service import CustomJWTService
            import logging
            logger = logging.getLogger(__name__)
            
            session = UserSession.objects.filter(
                user=user,
                session_token=jti,
                is_active=True
            ).first()
            
            logger.debug(f"Session lookup for user {user.email} with jti {jti}: {'Found' if session else 'Not found'}")
            
            if session:
                now = timezone.now()
                
                # Store original last_activity before any updates
                original_last_activity = session.last_activity
                
                # Check for absolute session timeout
                session_age = (now - session.created_at).total_seconds()
                if session_age > self.timeout_seconds:
                    CustomJWTService.blacklist_token(jti, user, 'session_timeout')
                    session.is_active = False
                    session.save(update_fields=['is_active'])
                    return
                
                # Check for inactivity timeout using original last_activity
                inactivity_time = (now - original_last_activity).total_seconds()
                print(f"DEBUG: Inactivity time: {inactivity_time}s, limit: {self.inactivity_timeout}s")
                logger.debug(f"Inactivity time: {inactivity_time}s, limit: {self.inactivity_timeout}s")
                if inactivity_time > self.inactivity_timeout:
                    print(f"DEBUG: Session {jti} timed out - deactivating")
                    logger.info(f"Session {jti} for user {user.email} timed out due to inactivity")
                    CustomJWTService.blacklist_token(jti, user, 'inactivity_timeout')
                    session.is_active = False
                    session.save(update_fields=['is_active'])
                    return
                
                # Update last activity only if session is still active
                # Use update() to bypass auto_now and preserve the manual update
                from core.tenant_management.models import UserSession
                UserSession.objects.filter(id=session.id).update(last_activity=now)
                
        except Exception:
            pass  # Don't break the request flow


class SessionCleanupMiddleware:
    """
    Middleware to periodically clean up expired sessions and tokens
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.last_cleanup = time.time()
        self.cleanup_interval = 300  # 5 minutes
    
    def __call__(self, request):
        # Periodic cleanup (every 5 minutes)
        current_time = time.time()
        if current_time - self.last_cleanup > self.cleanup_interval:
            self._cleanup_expired_sessions()
            self.last_cleanup = current_time
        
        response = self.get_response(request)
        return response
    
    def _cleanup_expired_sessions(self):
        """
        Clean up expired sessions and blacklisted tokens
        """
        try:
            from core.tenant_management.services.jwt_service import CustomJWTService
            from core.tenant_management.models import UserSession, LoginAttempt
            
            # Clean expired tokens
            CustomJWTService.clean_expired_tokens()
            
            # Clean old inactive sessions (older than 24 hours)
            cutoff_date = timezone.now() - timedelta(hours=24)
            
            # Debug: count sessions that match criteria
            import logging
            logger = logging.getLogger(__name__)
            # Debug: Show all sessions first
            all_sessions = UserSession.objects.all().values('id', 'last_activity', 'is_active')
            print(f"DEBUG: All sessions: {list(all_sessions)}")
            print(f"DEBUG: Cutoff date: {cutoff_date}")
            
            session_count = UserSession.objects.filter(
                last_activity__lt=cutoff_date,
                is_active=False
            ).count()
            logger.debug(f"Found {session_count} sessions to delete with last_activity < {cutoff_date}")
            print(f"DEBUG: Found {session_count} sessions to delete")
            
            sessions_deleted, _ = UserSession.objects.filter(
                last_activity__lt=cutoff_date,
                is_active=False
            ).delete()
            
            # Clean old login attempts (older than 30 days)
            login_cutoff = timezone.now() - timedelta(days=30)
            
            # Debug: count login attempts that match criteria
            attempt_count = LoginAttempt.objects.filter(
                attempt_time__lt=login_cutoff
            ).count()
            logger.debug(f"Found {attempt_count} login attempts to delete with attempt_time < {login_cutoff}")
            
            attempts_deleted, _ = LoginAttempt.objects.filter(
                attempt_time__lt=login_cutoff
            ).delete()
            
            logger.info(f"Cleaned {sessions_deleted} expired sessions and {attempts_deleted} old login attempts")
            
        except Exception as e:
            # Log the error but don't break the request
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to cleanup expired sessions: {e}")