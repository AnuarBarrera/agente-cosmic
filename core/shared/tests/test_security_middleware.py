import pytest
import time
import uuid
from unittest.mock import patch, MagicMock
from django.test import TestCase, RequestFactory
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta

from core.tenant_management.models import User, TenantModel, Plan, Subscription, UserSession
from core.shared.middleware.tenant_rate_limiting import TenantRateLimitingMiddleware, APIThrottlingMiddleware
from core.shared.middleware.session_timeout import SessionTimeoutMiddleware, SessionCleanupMiddleware


@pytest.mark.django_db  
class RateLimitingMiddlewareTestCase(TestCase):
    
    def setUp(self):
        """Set up test data"""
        self.factory = RequestFactory()
        
        # Create plans with different limits
        self.free_plan = Plan.objects.create(
            name="FREE", 
            max_daily_interactions=100,
            max_monthly_interactions=1000
        )
        self.premium_plan = Plan.objects.create(
            name="PREMIUM", 
            max_daily_interactions=1000,
            max_monthly_interactions=50000
        )
        
        # Create tenants with different plans
        self.free_tenant = TenantModel.objects.create(name="Free Tenant", status="active")
        self.free_subscription = Subscription.objects.create(
            tenant=self.free_tenant,
            plan=self.free_plan,
            status="active"
        )
        
        self.premium_tenant = TenantModel.objects.create(name="Premium Tenant", status="active")
        self.premium_subscription = Subscription.objects.create(
            tenant=self.premium_tenant,
            plan=self.premium_plan,
            status="active"
        )
        
        # Create users
        self.free_user = User.objects.create_user(
            username='freeuser@example.com',
            email='freeuser@example.com',
            password='TestPassword123!',
            tenant=self.free_tenant,
            email_verified=True
        )
        
        self.premium_user = User.objects.create_user(
            username='premiumuser@example.com',
            email='premiumuser@example.com',
            password='TestPassword123!',
            tenant=self.premium_tenant,
            email_verified=True
        )
        
        self.middleware = TenantRateLimitingMiddleware(lambda request: MagicMock())

    def tearDown(self):
        """Clear cache after each test"""
        cache.clear()

    def test_rate_limiting_for_free_plan(self):
        """Test rate limiting enforcement for FREE plan"""
        request = self.factory.get('/api/test/')
        request.user = self.free_user
        
        # Make requests up to the limit
        for i in range(60):  # FREE plan limit: 60 requests per minute
            response = self.middleware(request)
            self.assertNotEqual(response.status_code, 429)
        
        # Next request should be rate limited
        response = self.middleware(request)
        self.assertEqual(response.status_code, 429)
        self.assertIn('Rate limit exceeded', response.content.decode())

    def test_rate_limiting_for_premium_plan(self):
        """Test higher limits for PREMIUM plan"""
        request = self.factory.get('/api/test/')
        request.user = self.premium_user
        
        # Make more requests than FREE plan allows
        for i in range(100):  # More than FREE limit but within PREMIUM limit
            response = self.middleware(request)
            self.assertNotEqual(response.status_code, 429)

    def test_unauthenticated_rate_limiting(self):
        """Test rate limiting for unauthenticated requests"""
        request = self.factory.get('/api/test/')
        request.user = MagicMock()
        request.user.is_authenticated = False
        request.META = {'REMOTE_ADDR': '192.168.1.1'}
        
        # Make requests up to IP limit (30 per minute)
        for i in range(30):
            response = self.middleware(request)
            self.assertNotEqual(response.status_code, 429)
        
        # Next request should be rate limited
        response = self.middleware(request)
        self.assertEqual(response.status_code, 429)

    def test_whitelisted_paths_not_rate_limited(self):
        """Test that whitelisted paths are not rate limited"""
        request = self.factory.get('/health/')
        request.user = self.free_user
        
        # Make many requests to whitelisted path
        for i in range(100):
            response = self.middleware(request)
            self.assertNotEqual(response.status_code, 429)

    def test_rate_limit_reset_after_time_window(self):
        """Test that rate limits reset after time window"""
        request = self.factory.get('/api/test/')
        request.user = self.free_user
        
        # Exhaust rate limit
        for i in range(60):
            self.middleware(request)
        
        # Should be rate limited
        response = self.middleware(request)
        self.assertEqual(response.status_code, 429)
        
        # Mock time advancement to next minute
        with patch('time.time') as mock_time:
            mock_time.return_value = time.time() + 61  # Advance by more than 1 minute
            
            # Should be able to make requests again
            response = self.middleware(request)
            self.assertNotEqual(response.status_code, 429)

    def test_different_time_windows(self):
        """Test that different time windows (minute, hour, day) work independently"""
        request = self.factory.get('/api/test/')
        request.user = self.free_user
        
        # Fill up minute limit but stay under hour limit
        with patch('core.shared.middleware.tenant_rate_limiting.cache') as mock_cache:
            # Mock cache to simulate minute limit reached but hour limit not reached
            def cache_get(key, default=0):
                if 'requests_per_minute' in key:
                    return 60  # At limit
                elif 'requests_per_hour' in key:
                    return 500  # Well under limit
                return default
            
            mock_cache.get.side_effect = cache_get
            mock_cache.set.return_value = None
            mock_cache.get_or_set.return_value = None
            
            response = self.middleware(request)
            self.assertEqual(response.status_code, 429)

    def test_rate_limit_by_tenant_id(self):
        """Test that rate limits are applied per tenant"""
        request1 = self.factory.get('/api/test/')
        request1.user = self.free_user
        
        request2 = self.factory.get('/api/test/')
        request2.user = self.premium_user
        
        # Exhaust rate limit for free tenant
        for i in range(60):
            self.middleware(request1)
        
        # Free tenant should be rate limited
        response = self.middleware(request1)
        self.assertEqual(response.status_code, 429)
        
        # Premium tenant should still work
        response = self.middleware(request2)
        self.assertNotEqual(response.status_code, 429)


@pytest.mark.django_db
class APIThrottlingMiddlewareTestCase(TestCase):
    
    def setUp(self):
        """Set up test data"""
        self.factory = RequestFactory()
        
        self.plan = Plan.objects.create(name="FREE", max_daily_interactions=100)
        self.tenant = TenantModel.objects.create(name="Test Tenant", status="active")
        self.subscription = Subscription.objects.create(
            tenant=self.tenant,
            plan=self.plan,
            status="active"
        )
        self.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='TestPassword123!',
            tenant=self.tenant,
            email_verified=True
        )
        
        self.middleware = APIThrottlingMiddleware(lambda request: MagicMock())

    def tearDown(self):
        """Clear cache after each test"""
        cache.clear()

    def test_ai_endpoint_throttling(self):
        """Test specialized throttling for AI endpoints"""
        request = self.factory.post('/api/v1/ai/process')
        request.user = self.user
        
        # Make requests up to AI endpoint limit for FREE plan (10 per minute)
        for i in range(10):
            response = self.middleware(request)
            self.assertNotEqual(response.status_code, 429)
        
        # Next request should be throttled
        response = self.middleware(request)
        self.assertEqual(response.status_code, 429)
        self.assertIn('API endpoint rate limit exceeded', response.content.decode())

    def test_webhook_endpoint_throttling(self):
        """Test specialized throttling for webhook endpoints"""
        request = self.factory.post('/api/v1/channels/webhook/receive')
        request.user = self.user
        
        # Webhook endpoints have higher limits than AI endpoints
        # FREE plan: 50 per minute for webhooks
        for i in range(50):
            response = self.middleware(request)
            self.assertNotEqual(response.status_code, 429)
        
        # Next request should be throttled
        response = self.middleware(request)
        self.assertEqual(response.status_code, 429)

    def test_non_specialized_endpoint_not_throttled(self):
        """Test that non-specialized endpoints are not affected"""
        request = self.factory.get('/api/v1/tenants/')
        request.user = self.user
        
        # This should not trigger specialized throttling
        response = self.middleware(request)
        self.assertNotEqual(response.status_code, 429)


@pytest.mark.django_db
class SessionTimeoutMiddlewareTestCase(TestCase):
    
    def setUp(self):
        """Set up test data"""
        self.factory = RequestFactory()
        
        self.plan = Plan.objects.create(name="FREE", max_daily_interactions=100)
        self.tenant = TenantModel.objects.create(name="Test Tenant", status="active")
        self.subscription = Subscription.objects.create(
            tenant=self.tenant,
            plan=self.plan,
            status="active"
        )
        self.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='TestPassword123!',
            tenant=self.tenant,
            email_verified=True
        )
        
        self.middleware = SessionTimeoutMiddleware(lambda request: MagicMock())
        # Configure shorter timeouts for testing
        self.middleware.timeout_seconds = 3600  # 1 hour
        self.middleware.inactivity_timeout = 1800  # 30 minutes

    def test_session_timeout_check(self):
        """Test session timeout functionality"""
        # Create a session
        session = UserSession.objects.create(
            user=self.user,
            session_token='test-jti',
            ip_address='192.168.1.1',
            user_agent='test-agent',
            is_active=True
        )
        
        request = self.factory.get('/api/test/')
        request.user = self.user
        request.META = {'HTTP_AUTHORIZATION': 'Bearer test-token'}
        
        # Mock the JWT token parsing
        with patch('rest_framework_simplejwt.tokens.UntypedToken') as mock_token:
            mock_token_instance = MagicMock()
            mock_token_instance.get.return_value = 'test-jti'
            mock_token.return_value = mock_token_instance
            
            # Simulate session that's about to timeout
            old_time = timezone.now() - timedelta(minutes=35)  # 35 minutes ago
            session.last_activity = old_time
            session.save()
            
            # Process request
            self.middleware(request)
            
            # Session should still be active (under 30-minute inactivity limit)
            session.refresh_from_db()
            self.assertTrue(session.is_active)

    def test_session_inactivity_timeout(self):
        """Test inactivity timeout enforcement"""
        # Create an old inactive session - use a much longer timeout to be sure
        old_time = timezone.now() - timedelta(minutes=35)  # 35 minutes ago
        session = UserSession.objects.create(
            user=self.user,
            session_token='test-jti',
            ip_address='192.168.1.1',
            user_agent='test-agent',
            is_active=True
        )
        # Force update last_activity to bypass auto_now
        UserSession.objects.filter(id=session.id).update(last_activity=old_time)
        
        request = self.factory.get('/api/test/')
        request.user = self.user
        request.META = {'HTTP_AUTHORIZATION': 'Bearer test-token'}
        
        # Set a specific timeout for this test
        original_timeout = self.middleware.inactivity_timeout
        self.middleware.inactivity_timeout = 1800  # 30 minutes
        
        with patch('rest_framework_simplejwt.tokens.UntypedToken') as mock_token:
            mock_token_instance = MagicMock()
            mock_token_instance.get.return_value = 'test-jti'
            mock_token.return_value = mock_token_instance
            
            # Mock the JWT service to track if blacklist is called
            with patch('core.tenant_management.services.jwt_service.CustomJWTService.blacklist_token') as mock_blacklist:
                # Process the request
                self.middleware(request)
                
                # Check that session was deactivated and token was blacklisted
                session.refresh_from_db()
                self.assertFalse(session.is_active)
                mock_blacklist.assert_called_once_with('test-jti', self.user, 'inactivity_timeout')
        
        # Restore original timeout
        self.middleware.inactivity_timeout = original_timeout

    def test_absolute_session_timeout(self):
        """Test absolute session timeout (regardless of activity)"""
        # Create an old session (created more than 1 hour ago)
        old_time = timezone.now() - timedelta(hours=2)
        session = UserSession.objects.create(
            user=self.user,
            session_token='test-jti',
            ip_address='192.168.1.1',
            user_agent='test-agent',
            is_active=True,
            last_activity=timezone.now()  # Recent activity
        )
        # Update created_at manually since it's auto_now_add
        UserSession.objects.filter(id=session.id).update(created_at=old_time)
        
        request = self.factory.get('/api/test/')
        request.user = self.user
        request.META = {'HTTP_AUTHORIZATION': 'Bearer test-token'}
        
        with patch('rest_framework_simplejwt.tokens.UntypedToken') as mock_token:
            mock_token_instance = MagicMock()
            mock_token_instance.get.return_value = 'test-jti'
            mock_token.return_value = mock_token_instance
            
            with patch('core.tenant_management.services.jwt_service.CustomJWTService.blacklist_token') as mock_blacklist:
                self.middleware(request)
                
                # Should have blacklisted the token due to absolute timeout
                mock_blacklist.assert_called_with('test-jti', self.user, 'session_timeout')


@pytest.mark.django_db
class SessionCleanupMiddlewareTestCase(TestCase):
    
    def setUp(self):
        """Set up test data"""
        self.factory = RequestFactory()
        
        self.plan = Plan.objects.create(name="FREE", max_daily_interactions=100)
        self.tenant = TenantModel.objects.create(name="Test Tenant", status="active")
        self.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='TestPassword123!',
            tenant=self.tenant,
            email_verified=True
        )
        
        self.middleware = SessionCleanupMiddleware(lambda request: MagicMock())

    def test_periodic_cleanup_timing(self):
        """Test that cleanup only runs periodically"""
        request = self.factory.get('/api/test/')
        
        # First call should not trigger cleanup (just set last_cleanup time)
        with patch.object(self.middleware, '_cleanup_expired_sessions') as mock_cleanup:
            self.middleware(request)
            mock_cleanup.assert_not_called()
        
        # Immediate second call should not trigger cleanup
        with patch.object(self.middleware, '_cleanup_expired_sessions') as mock_cleanup:
            self.middleware(request)
            mock_cleanup.assert_not_called()
        
        # Mock time advancement to trigger cleanup
        current_time = time.time()
        with patch('time.time') as mock_time:
            mock_time.return_value = current_time + 301  # 5+ minutes later
            
            with patch.object(self.middleware, '_cleanup_expired_sessions') as mock_cleanup:
                self.middleware(request)
                mock_cleanup.assert_called_once()

    def test_expired_session_cleanup(self):
        """Test cleanup of expired sessions"""
        # Create old inactive session using direct SQL to avoid auto_now issues
        old_time = timezone.now() - timedelta(hours=25)
        
        from django.db import connection
        old_session_id = uuid.uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_sessions 
                (id, user_id, session_token, ip_address, user_agent, is_active, last_activity, created_at) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    old_session_id,
                    self.user.id,
                    'old-jti',
                    '192.168.1.1',
                    'test-agent',
                    False,
                    old_time,
                    old_time
                ]
            )
        
        # Create recent inactive session
        recent_session = UserSession.objects.create(
            user=self.user,
            session_token='recent-jti',
            ip_address='192.168.1.1',
            user_agent='test-agent',
            is_active=False,
            last_activity=timezone.now() - timedelta(hours=1)
        )
        
        # Run cleanup
        self.middleware._cleanup_expired_sessions()
        
        # Old session should be deleted, recent one should remain
        self.assertFalse(UserSession.objects.filter(id=old_session_id).exists())
        self.assertTrue(UserSession.objects.filter(id=recent_session.id).exists())

    def test_cleanup_handles_exceptions(self):
        """Test that cleanup handles exceptions gracefully"""
        with patch('core.tenant_management.services.jwt_service.CustomJWTService.clean_expired_tokens') as mock_clean:
            mock_clean.side_effect = Exception("Test exception")
            
            # Should not raise exception
            try:
                self.middleware._cleanup_expired_sessions()
            except Exception as e:
                self.fail(f"Cleanup should handle exceptions gracefully, but raised: {e}")

    def test_login_attempt_cleanup(self):
        """Test cleanup of old login attempts"""
        from core.tenant_management.models import LoginAttempt
        
        # Create old login attempt
        old_time = timezone.now() - timedelta(days=31)
        old_attempt = LoginAttempt.objects.create(
            email='test@example.com',
            ip_address='192.168.1.1',
            success=False,
            failure_reason='test'
        )
        # Force update the attempt_time field
        LoginAttempt.objects.filter(id=old_attempt.id).update(attempt_time=old_time)
        
        # Create recent login attempt
        recent_attempt = LoginAttempt.objects.create(
            email='test@example.com',
            ip_address='192.168.1.1',
            success=False,
            failure_reason='test',
            attempt_time=timezone.now() - timedelta(days=1)
        )
        
        # Run cleanup
        self.middleware._cleanup_expired_sessions()
        
        # Old attempt should be deleted, recent one should remain
        self.assertFalse(LoginAttempt.objects.filter(id=old_attempt.id).exists())
        self.assertTrue(LoginAttempt.objects.filter(id=recent_attempt.id).exists())