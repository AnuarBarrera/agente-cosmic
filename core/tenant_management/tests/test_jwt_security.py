import pytest
import secrets
import uuid
from unittest.mock import patch

_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from core.tenant_management.models import User, TenantModel, Plan, Subscription, BlacklistedToken, UserSession
from core.tenant_management.services.jwt_service import CustomJWTService


@pytest.mark.django_db
class JWTSecurityTestCase(APITestCase):
    
    def setUp(self):
        """Set up test data"""
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
        self.client = APIClient()

    def test_jwt_token_creation_and_session_tracking(self):
        """Test that JWT tokens are created with session tracking"""
        tokens = CustomJWTService.create_tokens_for_user(
            self.user, 
            ip_address='127.0.0.1',
            user_agent='test-agent'
        )
        
        self.assertIn('access', tokens)
        self.assertIn('refresh', tokens)
        self.assertIn('session_id', tokens)
        
        # Verify session was created
        session = UserSession.objects.filter(user=self.user, is_active=True).first()
        self.assertIsNotNone(session)
        self.assertEqual(session.ip_address, '127.0.0.1')
        self.assertEqual(session.user_agent, 'test-agent')

    def test_jwt_token_blacklisting(self):
        """Test JWT token blacklisting functionality"""
        # Create tokens
        tokens = CustomJWTService.create_tokens_for_user(self.user)
        refresh_token = RefreshToken(tokens['refresh'])
        jti = refresh_token['jti']
        
        # Verify token is valid initially
        self.assertFalse(CustomJWTService.is_token_blacklisted(jti))
        
        # Blacklist the token
        CustomJWTService.blacklist_token(jti, self.user, 'test_logout')
        
        # Verify token is now blacklisted
        self.assertTrue(CustomJWTService.is_token_blacklisted(jti))
        
        # Verify blacklisted token record was created
        blacklisted = BlacklistedToken.objects.filter(token_jti=jti).first()
        self.assertIsNotNone(blacklisted)
        self.assertEqual(blacklisted.user, self.user)
        self.assertEqual(blacklisted.reason, 'test_logout')

    def test_token_rotation(self):
        """Test JWT token rotation functionality"""
        # Create initial tokens
        tokens = CustomJWTService.create_tokens_for_user(self.user)
        old_refresh_token = tokens['refresh']
        old_refresh_obj = RefreshToken(old_refresh_token)
        old_jti = old_refresh_obj['jti']
        
        # Rotate tokens
        new_tokens = CustomJWTService.rotate_tokens(old_refresh_token)
        
        self.assertIn('access', new_tokens)
        self.assertIn('refresh', new_tokens)
        self.assertNotEqual(old_refresh_token, new_tokens['refresh'])
        
        # Verify old token is blacklisted
        self.assertTrue(CustomJWTService.is_token_blacklisted(old_jti))

    def test_concurrent_session_limits(self):
        """Test that concurrent session limits are enforced"""
        sessions = []
        
        # Create maximum allowed sessions (3)
        for i in range(CustomJWTService.MAX_CONCURRENT_SESSIONS):
            tokens = CustomJWTService.create_tokens_for_user(
                self.user, 
                ip_address=f'192.168.1.{i+1}'
            )
            sessions.append(tokens)
        
        # Verify all sessions are active
        active_sessions = UserSession.objects.filter(user=self.user, is_active=True)
        self.assertEqual(active_sessions.count(), CustomJWTService.MAX_CONCURRENT_SESSIONS)
        
        # Create one more session (should deactivate oldest)
        new_tokens = CustomJWTService.create_tokens_for_user(
            self.user, 
            ip_address='192.168.1.10'
        )
        
        # Verify still only max sessions are active
        active_sessions = UserSession.objects.filter(user=self.user, is_active=True)
        self.assertEqual(active_sessions.count(), CustomJWTService.MAX_CONCURRENT_SESSIONS)

    def test_logout_all_sessions(self):
        """Test logout functionality that invalidates all sessions"""
        # Create multiple sessions
        for i in range(2):
            CustomJWTService.create_tokens_for_user(
                self.user, 
                ip_address=f'192.168.1.{i+1}'
            )
        
        # Verify sessions exist
        active_sessions = UserSession.objects.filter(user=self.user, is_active=True)
        self.assertEqual(active_sessions.count(), 2)
        
        # Logout all sessions
        CustomJWTService.logout_user(self.user)
        
        # Verify all sessions are deactivated
        active_sessions = UserSession.objects.filter(user=self.user, is_active=True)
        self.assertEqual(active_sessions.count(), 0)

    def test_clean_expired_tokens(self):
        """Test cleanup of expired blacklisted tokens"""
        # Create a blacklisted token
        CustomJWTService.blacklist_token('test-jti', self.user, 'test')
        
        # Verify it exists
        self.assertEqual(BlacklistedToken.objects.count(), 1)
        
        # Mock datetime to simulate passage of time
        from django.utils import timezone
        from datetime import timedelta
        
        future_time = timezone.now() + timedelta(days=8)
        with patch('django.utils.timezone.now') as mock_now:
            # Set current time to 8 days in the future
            mock_now.return_value = future_time
            
            # Clean expired tokens
            cleaned_count = CustomJWTService.clean_expired_tokens()
            
            # Verify cleanup occurred
            self.assertEqual(cleaned_count, 1)
            self.assertEqual(BlacklistedToken.objects.count(), 0)

    def test_password_reset_invalidates_sessions(self):
        """Test that password reset invalidates all user sessions"""
        # Create some sessions
        tokens1 = CustomJWTService.create_tokens_for_user(self.user, '192.168.1.1')
        tokens2 = CustomJWTService.create_tokens_for_user(self.user, '192.168.1.2')
        
        # Verify sessions exist
        active_sessions = UserSession.objects.filter(user=self.user, is_active=True)
        self.assertEqual(active_sessions.count(), 2)
        
        # Simulate password reset
        from core.tenant_management.services.auth_service import AuthService
        from core.tenant_management.models import PasswordResetToken
        
        reset_token = AuthService.initiate_password_reset(self.user.email)
        AuthService.reset_password(reset_token.token, 'NewPassword123!')
        
        # Verify all sessions are deactivated
        active_sessions = UserSession.objects.filter(user=self.user, is_active=True)
        self.assertEqual(active_sessions.count(), 0)