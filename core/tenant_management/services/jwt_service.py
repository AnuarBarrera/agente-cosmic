import uuid
from datetime import timedelta
from typing import Optional, Dict, Any
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken as SimpleJWTBlacklistedToken
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import UntypedToken
from django.conf import settings
import jwt
import logging

from ..models import BlacklistedToken, UserSession, LoginAttempt

logger = logging.getLogger(__name__)
User = get_user_model()


class CustomJWTService:
    """
    Enhanced JWT service with token rotation, blacklisting, and session management
    """
    
    MAX_CONCURRENT_SESSIONS = 3  # Maximum concurrent sessions per user
    
    @classmethod
    def create_tokens_for_user(
        cls, 
        user: User, 
        ip_address: str = None, 
        user_agent: str = None
    ) -> Dict[str, str]:
        """
        Create JWT tokens for a user with session tracking
        """
        try:
            # Create refresh token with custom claims
            refresh = RefreshToken.for_user(user)
            refresh['tenant_id'] = str(user.tenant.id) if user.tenant else None
            refresh['name'] = user.username
            refresh['user_id'] = str(user.id)
            
            # Generate unique JTI for session tracking
            jti = str(uuid.uuid4())
            refresh['jti'] = jti
            
            # Check and enforce concurrent session limits
            cls._enforce_concurrent_session_limit(user)
            
            # Create session record
            session = UserSession.objects.create(
                user=user,
                session_token=jti,
                ip_address=ip_address or '127.0.0.1',
                user_agent=user_agent or '',
            )
            
            logger.info(f"Created new session for user {user.email}: {jti}")
            
            return {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'session_id': str(session.id)
            }
            
        except Exception as e:
            logger.error(f"Failed to create tokens for user {user.email}: {e}")
            raise
    
    @classmethod
    def rotate_tokens(cls, refresh_token: str, ip_address: str = None) -> Dict[str, str]:
        """
        Rotate JWT tokens (blacklist old, create new)
        """
        try:
            # Validate the refresh token
            token = RefreshToken(refresh_token)

            # Check if token is blacklisted
            if cls.is_token_blacklisted(token['jti']):
                raise TokenError("Token is blacklisted")

            user = User.objects.get(id=token['user_id'])
            
            # Blacklist old token
            cls.blacklist_token(token['jti'], user, 'token_rotation')
            
            # Create new tokens
            return cls.create_tokens_for_user(user, ip_address)
            
        except (TokenError, User.DoesNotExist) as e:
            logger.error(f"Failed to rotate tokens: {e}")
            raise TokenError("Invalid refresh token")
    
    @classmethod
    def blacklist_token(cls, jti: str, user: User, reason: str = 'logout') -> bool:
        """
        Blacklist a JWT token by its JTI
        """
        try:
            # Add to our custom blacklist
            BlacklistedToken.objects.get_or_create(
                token_jti=jti,
                defaults={
                    'user': user,
                    'reason': reason
                }
            )
            
            # Deactivate session
            UserSession.objects.filter(
                session_token=jti,
                user=user,
                is_active=True
            ).update(is_active=False)
            
            # Create security event
            from ..models import SecurityEvent
            SecurityEvent.objects.create(
                user=user,
                event_type='token_blacklisted',
                description=f'Token blacklisted - reason: {reason}',
                severity='low'
            )
            
            logger.info(f"Blacklisted token {jti} for user {user.email}, reason: {reason}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to blacklist token {jti}: {e}")
            return False
    
    @classmethod
    def is_token_blacklisted(cls, jti: str) -> bool:
        """
        Check if a JWT token is blacklisted
        """
        return BlacklistedToken.objects.filter(token_jti=jti).exists()
    
    @classmethod
    def logout_user(cls, user: User, jti: str = None) -> bool:
        """
        Logout user by blacklisting their tokens
        """
        try:
            if jti:
                # Logout specific session
                cls.blacklist_token(jti, user, 'logout')
            else:
                # Logout all sessions
                active_sessions = UserSession.objects.filter(
                    user=user,
                    is_active=True
                )
                
                for session in active_sessions:
                    cls.blacklist_token(session.session_token, user, 'logout')
            
            logger.info(f"Logged out user {user.email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to logout user {user.email}: {e}")
            return False
    
    @classmethod
    def _enforce_concurrent_session_limit(cls, user: User):
        """
        Enforce maximum concurrent sessions per user
        """
        active_sessions = UserSession.objects.filter(
            user=user,
            is_active=True
        ).order_by('created_at')
        
        if active_sessions.count() >= cls.MAX_CONCURRENT_SESSIONS:
            # Deactivate oldest sessions
            sessions_to_deactivate = active_sessions[:active_sessions.count() - cls.MAX_CONCURRENT_SESSIONS + 1]
            
            for session in sessions_to_deactivate:
                cls.blacklist_token(session.session_token, user, 'session_limit_exceeded')
    
    @classmethod
    def clean_expired_tokens(cls) -> int:
        """
        Clean up expired blacklisted tokens (maintenance task)
        """
        try:
            # JWT tokens typically have a max lifetime, we'll keep blacklist for 7 days
            cutoff_date = timezone.now() - timedelta(days=7)
            
            expired_count = BlacklistedToken.objects.filter(
                blacklisted_at__lt=cutoff_date
            ).count()
            
            BlacklistedToken.objects.filter(
                blacklisted_at__lt=cutoff_date
            ).delete()
            
            # Also clean old inactive sessions
            UserSession.objects.filter(
                last_activity__lt=cutoff_date,
                is_active=False
            ).delete()
            
            logger.info(f"Cleaned {expired_count} expired blacklisted tokens")
            return expired_count
            
        except Exception as e:
            logger.error(f"Failed to clean expired tokens: {e}")
            return 0
    
    @classmethod
    def get_user_sessions(cls, user: User) -> list:
        """
        Get active sessions for a user
        """
        return UserSession.objects.filter(
            user=user,
            is_active=True
        ).order_by('-last_activity')
    
    @classmethod
    def record_login_attempt(
        cls, 
        email: str, 
        ip_address: str, 
        user_agent: str = '', 
        success: bool = False, 
        failure_reason: str = ''
    ):
        """
        Record login attempt for security monitoring
        """
        try:
            LoginAttempt.objects.create(
                email=email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=success,
                failure_reason=failure_reason
            )
        except Exception as e:
            logger.error(f"Failed to record login attempt for {email}: {e}")
    
    @classmethod
    def check_account_lockout(cls, email: str, ip_address: str = None) -> Dict[str, Any]:
        """
        Check if account should be locked out due to failed attempts
        """
        try:
            # Check failed attempts in last 15 minutes
            time_threshold = timezone.now() - timedelta(minutes=15)
            
            # Count failed attempts by email
            failed_attempts_by_email = LoginAttempt.objects.filter(
                email=email,
                success=False,
                attempt_time__gte=time_threshold
            ).count()
            
            # Count failed attempts by IP (if provided)
            failed_attempts_by_ip = 0
            if ip_address:
                failed_attempts_by_ip = LoginAttempt.objects.filter(
                    ip_address=ip_address,
                    success=False,
                    attempt_time__gte=time_threshold
                ).count()
            
            # Lockout thresholds
            EMAIL_LOCKOUT_THRESHOLD = 5
            IP_LOCKOUT_THRESHOLD = 10
            
            is_locked = (
                failed_attempts_by_email >= EMAIL_LOCKOUT_THRESHOLD or
                failed_attempts_by_ip >= IP_LOCKOUT_THRESHOLD
            )
            
            return {
                'is_locked': is_locked,
                'failed_attempts_by_email': failed_attempts_by_email,
                'failed_attempts_by_ip': failed_attempts_by_ip,
                'lockout_expires': time_threshold + timedelta(minutes=15) if is_locked else None
            }
            
        except Exception as e:
            logger.error(f"Failed to check account lockout for {email}: {e}")
            return {'is_locked': False, 'failed_attempts_by_email': 0, 'failed_attempts_by_ip': 0}


class CustomJWTAuthentication(JWTAuthentication):
    """
    Custom JWT authentication that checks token blacklist
    """
    
    def get_validated_token(self, raw_token):
        """
        Validates the token and checks if it's blacklisted
        """
        try:
            # First validate token structure and signature
            validated_token = super().get_validated_token(raw_token)
            
            # Check if token is blacklisted
            jti = validated_token.get('jti')
            if jti and CustomJWTService.is_token_blacklisted(jti):
                raise TokenError('Token is blacklisted')
            
            # Update session activity
            if jti:
                UserSession.objects.filter(
                    session_token=jti,
                    is_active=True
                ).update(last_activity=timezone.now())
            
            return validated_token
            
        except TokenError as e:
            logger.debug(f"Token validation failed: {e}")
            raise