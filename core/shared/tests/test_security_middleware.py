import pytest
import secrets
import time
import uuid
from unittest.mock import patch, MagicMock

_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"
from django.test import TestCase, RequestFactory
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta

from core.tenant_management.models import User, TenantModel, Plan, Subscription, UserSession
from core.shared.middleware.tenant_rate_limiting import TenantRateLimitingMiddleware, APIThrottlingMiddleware
from core.shared.middleware.session_timeout import SessionTimeoutMiddleware


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
            password=_TEST_PWD,
            tenant=self.free_tenant,
            email_verified=True
        )
        
        self.premium_user = User.objects.create_user(
            username='premiumuser@example.com',
            email='premiumuser@example.com',
            password=_TEST_PWD,
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
            password=_TEST_PWD,
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


# HALLAZGO 79 (hallazgos.txt): par de tests deliberadamente FUERA de la clase de
# arriba (que tiene su propio tearDown con cache.clear() y por eso nunca mostraba el
# bug) — reproducen la fuga de contador de rate-limit por IP entre tests sin relacion,
# protegidos por el fixture autouse _clear_cache_between_tests (core/conftest.py).
# Si ese fixture se rompe o se quita, test_b falla porque hereda el conteo de test_a.

@pytest.mark.django_db
def test_a_ip_rate_limit_exhausted_here_should_not_leak_to_next_test():
    factory = RequestFactory()
    request = factory.get('/api/test/')
    request.user = MagicMock()
    request.user.is_authenticated = False
    request.META = {'REMOTE_ADDR': '203.0.113.77'}
    middleware = TenantRateLimitingMiddleware(lambda r: MagicMock())
    for _ in range(30):
        response = middleware(request)
        assert response.status_code != 429
    response = middleware(request)
    assert response.status_code == 429


@pytest.mark.django_db
def test_b_fresh_ip_rate_limit_not_affected_by_previous_test():
    factory = RequestFactory()
    request = factory.get('/api/test/')
    request.user = MagicMock()
    request.user.is_authenticated = False
    request.META = {'REMOTE_ADDR': '203.0.113.77'}
    middleware = TenantRateLimitingMiddleware(lambda r: MagicMock())
    response = middleware(request)
    assert response.status_code != 429
