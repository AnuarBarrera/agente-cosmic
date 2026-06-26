import pytest
import secrets
import uuid
from django.test import TestCase

_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"
_NEW_PWD  = f"N3w!{secrets.token_urlsafe(10)}-"
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from django.utils import timezone
from datetime import timedelta

from core.tenant_management.models import (
    User, TenantModel, Plan, Subscription, SecurityEvent,
    LoginAttempt, BlacklistedToken, PasswordHistory
)
from core.tenant_management.services.jwt_service import CustomJWTService
from core.tenant_management.services.auth_service import AuthService


@pytest.mark.django_db
class SecurityEventLoggingTestCase(APITestCase):
    
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

    def test_password_reset_security_event(self):
        """Test that password resets create security events"""
        # Initiate password reset
        reset_token = AuthService.initiate_password_reset(self.user.email)
        
        # Complete password reset
        AuthService.reset_password(reset_token.token, 'NewPassword123!')
        
        # Verify security event was created
        events = SecurityEvent.objects.filter(
            user=self.user,
            event_type='password_reset'
        )
        self.assertEqual(events.count(), 1)
        
        event = events.first()
        self.assertEqual(event.severity, 'medium')
        self.assertIn('password reset completed', event.description.lower())

    def test_password_change_security_event(self):
        """Test that password changes create security events"""
        # Change password
        AuthService.change_password(
            self.user, 
            _TEST_PWD, 
            _NEW_PWD
        )
        
        # Verify security event was created
        events = SecurityEvent.objects.filter(
            user=self.user,
            event_type='password_changed'
        )
        self.assertEqual(events.count(), 1)
        
        event = events.first()
        self.assertEqual(event.severity, 'low')
        self.assertIn('password changed successfully', event.description.lower())

    def test_failed_password_change_security_event(self):
        """Test that failed password changes create security events"""
        # Attempt password change with wrong old password
        try:
            AuthService.change_password(
                self.user, 
                'WrongOldPassword', 
                _NEW_PWD
            )
        except ValueError:
            pass  # Expected
        
        # Verify security event was created
        events = SecurityEvent.objects.filter(
            user=self.user,
            event_type='password_change_failed'
        )
        self.assertEqual(events.count(), 1)
        
        event = events.first()
        self.assertEqual(event.severity, 'medium')
        self.assertIn('incorrect old password', event.description.lower())

    def test_token_blacklisted_security_event(self):
        """Test that token blacklisting creates security events"""
        # Create and blacklist a token
        tokens = CustomJWTService.create_tokens_for_user(self.user)
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh_token = RefreshToken(tokens['refresh'])
        jti = refresh_token['jti']
        
        CustomJWTService.blacklist_token(jti, self.user, 'test_logout')
        
        # Verify security event was created
        events = SecurityEvent.objects.filter(
            user=self.user,
            event_type='token_blacklisted'
        )
        self.assertEqual(events.count(), 1)
        
        event = events.first()
        self.assertEqual(event.severity, 'low')
        self.assertIn('token blacklisted', event.description.lower())

    def test_security_event_data_structure(self):
        """Test that security events have proper data structure"""
        # Create a security event
        event = SecurityEvent.objects.create(
            user=self.user,
            event_type='test_event',
            description='Test security event',
            ip_address='192.168.1.1',
            user_agent='Test Agent',
            severity='medium',
            additional_data={'test_key': 'test_value'}
        )
        
        # Verify all fields are properly set
        self.assertEqual(event.user, self.user)
        self.assertEqual(event.event_type, 'test_event')
        self.assertEqual(event.description, 'Test security event')
        self.assertEqual(event.ip_address, '192.168.1.1')
        self.assertEqual(event.user_agent, 'Test Agent')
        self.assertEqual(event.severity, 'medium')
        self.assertEqual(event.additional_data, {'test_key': 'test_value'})
        self.assertIsNotNone(event.created_at)

    def test_security_event_filtering_by_user(self):
        """Test that security events can be filtered by user"""
        # Create another user
        user2 = User.objects.create_user(
            username='user2@example.com',
            email='user2@example.com',
            password=_TEST_PWD,
            tenant=self.tenant,
            email_verified=True
        )
        
        # Create events for both users
        SecurityEvent.objects.create(
            user=self.user,
            event_type='test_event_1',
            description='Event for user 1'
        )
        
        SecurityEvent.objects.create(
            user=user2,
            event_type='test_event_2',
            description='Event for user 2'
        )
        
        # Verify filtering works
        user1_events = SecurityEvent.objects.filter(user=self.user)
        user2_events = SecurityEvent.objects.filter(user=user2)
        
        self.assertEqual(user1_events.count(), 1)
        self.assertEqual(user2_events.count(), 1)
        self.assertEqual(user1_events.first().description, 'Event for user 1')
        self.assertEqual(user2_events.first().description, 'Event for user 2')

    def test_security_event_filtering_by_type(self):
        """Test that security events can be filtered by type"""
        # Create events of different types
        SecurityEvent.objects.create(
            user=self.user,
            event_type='login_success',
            description='Successful login'
        )
        
        SecurityEvent.objects.create(
            user=self.user,
            event_type='password_changed',
            description='Password changed'
        )
        
        SecurityEvent.objects.create(
            user=self.user,
            event_type='login_success',
            description='Another successful login'
        )
        
        # Verify filtering by type
        login_events = SecurityEvent.objects.filter(event_type='login_success')
        password_events = SecurityEvent.objects.filter(event_type='password_changed')
        
        self.assertEqual(login_events.count(), 2)
        self.assertEqual(password_events.count(), 1)

    def test_security_event_filtering_by_severity(self):
        """Test that security events can be filtered by severity"""
        # Create events with different severities
        SecurityEvent.objects.create(
            user=self.user,
            event_type='test_event',
            description='Low severity event',
            severity='low'
        )
        
        SecurityEvent.objects.create(
            user=self.user,
            event_type='test_event',
            description='High severity event',
            severity='high'
        )
        
        SecurityEvent.objects.create(
            user=self.user,
            event_type='test_event',
            description='Critical severity event',
            severity='critical'
        )
        
        # Verify filtering by severity
        low_events = SecurityEvent.objects.filter(severity='low')
        high_events = SecurityEvent.objects.filter(severity='high')
        critical_events = SecurityEvent.objects.filter(severity='critical')
        
        self.assertEqual(low_events.count(), 1)
        self.assertEqual(high_events.count(), 1)
        self.assertEqual(critical_events.count(), 1)

    def test_security_event_ordering(self):
        """Test that security events are ordered by creation time"""
        # Create events with slight time differences
        import time
        
        event1 = SecurityEvent.objects.create(
            user=self.user,
            event_type='test_event_1',
            description='First event'
        )
        
        time.sleep(0.01)  # Small delay
        
        event2 = SecurityEvent.objects.create(
            user=self.user,
            event_type='test_event_2',
            description='Second event'
        )
        
        # Verify ordering (most recent first)
        events = SecurityEvent.objects.all()
        self.assertEqual(events.first().id, event2.id)
        self.assertEqual(events.last().id, event1.id)

    def test_bulk_security_event_creation_performance(self):
        """Test that bulk security event creation performs well"""
        import time
        
        start_time = time.time()
        
        # Create many security events
        events_to_create = []
        for i in range(100):
            events_to_create.append(SecurityEvent(
                user=self.user,
                event_type='bulk_test',
                description=f'Bulk test event {i}',
                severity='low'
            ))
        
        # Bulk create
        SecurityEvent.objects.bulk_create(events_to_create)
        
        end_time = time.time()
        
        # Verify all events were created
        bulk_events = SecurityEvent.objects.filter(event_type='bulk_test')
        self.assertEqual(bulk_events.count(), 100)
        
        # Verify it completed in reasonable time (less than 1 second)
        self.assertLess(end_time - start_time, 1.0)

    def test_security_event_cleanup_old_events(self):
        """Test cleanup of old security events (for maintenance)"""
        # Create old security event
        old_event = SecurityEvent.objects.create(
            user=self.user,
            event_type='old_event',
            description='Old security event'
        )
        
        # Manually set old timestamp
        old_timestamp = timezone.now() - timedelta(days=91)  # > 90 days
        SecurityEvent.objects.filter(id=old_event.id).update(created_at=old_timestamp)
        
        # Create recent event
        recent_event = SecurityEvent.objects.create(
            user=self.user,
            event_type='recent_event',
            description='Recent security event'
        )
        
        # Simulate cleanup (delete events older than 90 days)
        cutoff_date = timezone.now() - timedelta(days=90)
        deleted_count = SecurityEvent.objects.filter(created_at__lt=cutoff_date).count()
        SecurityEvent.objects.filter(created_at__lt=cutoff_date).delete()
        
        # Verify cleanup worked
        self.assertEqual(deleted_count, 1)
        self.assertFalse(SecurityEvent.objects.filter(id=old_event.id).exists())
        self.assertTrue(SecurityEvent.objects.filter(id=recent_event.id).exists())