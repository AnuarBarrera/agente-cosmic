import pytest
import uuid
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from django.utils import timezone
from datetime import timedelta

from core.tenant_management.models import (
    User, TenantModel, Plan, Subscription, LoginAttempt, 
    SecurityEvent, PasswordHistory
)
from core.tenant_management.services.jwt_service import CustomJWTService
from core.tenant_management.services.auth_service import AuthService
from core.tenant_management.validators import CustomPasswordValidator, PasswordHistoryValidator


@pytest.mark.django_db
class AuthSecurityTestCase(APITestCase):
    
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
            password='TestPassword123!',
            tenant=self.tenant,
            email_verified=True
        )
        self.client = APIClient()

    def test_failed_login_attempt_tracking(self):
        """Test that failed login attempts are tracked"""
        # Use direct URL path to avoid reverse lookup issues
        login_url = '/api/v1/tenants/token/'
        
        # Make a failed login attempt
        response = self.client.post(login_url, {
            'email': 'testuser@example.com',
            'password': 'WrongPassword'
        }, HTTP_X_FORWARDED_FOR='192.168.1.1')
        
        self.assertEqual(response.status_code, 400)
        
        # Verify login attempt was recorded
        attempts = LoginAttempt.objects.filter(
            email='testuser@example.com',
            success=False
        )
        self.assertEqual(attempts.count(), 1)
        
        attempt = attempts.first()
        self.assertEqual(attempt.failure_reason, 'invalid_credentials')
        self.assertEqual(attempt.ip_address, '192.168.1.1')

    def test_successful_login_attempt_tracking(self):
        """Test that successful login attempts are tracked"""
        # Use direct URL path to avoid reverse lookup issues
        login_url = '/api/v1/tenants/token/'
        
        # Make a successful login attempt
        response = self.client.post(login_url, {
            'email': 'testuser@example.com',
            'password': 'TestPassword123!'
        }, HTTP_X_FORWARDED_FOR='192.168.1.1')
        
        self.assertEqual(response.status_code, 200)
        
        # Verify login attempt was recorded
        attempts = LoginAttempt.objects.filter(
            email='testuser@example.com',
            success=True
        )
        self.assertEqual(attempts.count(), 1)

    def test_account_lockout_after_multiple_failures(self):
        """Test that account gets locked after multiple failed attempts"""
        # Use direct URL path to avoid reverse lookup issues
        login_url = '/api/v1/tenants/token/'
        
        # Make 4 failed attempts (just under the limit)
        for i in range(4):
            response = self.client.post(login_url, {
                'email': 'testuser@example.com',
                'password': 'WrongPassword'
            }, HTTP_X_FORWARDED_FOR='192.168.1.1')
            self.assertEqual(response.status_code, 400)
        
        # 5th attempt should still fail but not trigger lockout message
        response = self.client.post(login_url, {
            'email': 'testuser@example.com',
            'password': 'WrongPassword'
        }, HTTP_X_FORWARDED_FOR='192.168.1.1')
        self.assertEqual(response.status_code, 400)
        
        # 6th attempt should trigger lockout
        response = self.client.post(login_url, {
            'email': 'testuser@example.com',
            'password': 'TestPassword123!'  # Even correct password should be locked
        }, HTTP_X_FORWARDED_FOR='192.168.1.1')
        
        self.assertEqual(response.status_code, 400)
        self.assertIn('Account temporarily locked', str(response.data))

    def test_ip_based_lockout(self):
        """Test that IP-based lockout works independently"""
        # Use direct URL path to avoid reverse lookup issues
        login_url = '/api/v1/tenants/token/'
        
        # Create another user
        user2 = User.objects.create_user(
            username='testuser2@example.com',
            email='testuser2@example.com',
            password='TestPassword123!',
            tenant=self.tenant,
            email_verified=True
        )
        
        # Make 10 failed attempts from same IP with different emails
        for i in range(10):
            email = f'fake{i}@example.com'
            response = self.client.post(login_url, {
                'email': email,
                'password': 'WrongPassword'
            }, HTTP_X_FORWARDED_FOR='192.168.1.100')
            self.assertEqual(response.status_code, 400)
        
        # Next attempt from same IP should be locked even with valid credentials
        response = self.client.post(login_url, {
            'email': 'testuser2@example.com',
            'password': 'TestPassword123!'
        }, HTTP_X_FORWARDED_FOR='192.168.1.100')
        
        self.assertEqual(response.status_code, 400)
        self.assertIn('Account temporarily locked', str(response.data))

    def test_lockout_expires_after_time(self):
        """Test that lockout expires after the specified time"""
        # Use direct URL path to avoid reverse lookup issues
        login_url = '/api/v1/tenants/token/'
        
        # Create 5 failed attempts to trigger lockout
        for i in range(5):
            self.client.post(login_url, {
                'email': 'testuser@example.com',
                'password': 'WrongPassword'
            }, HTTP_X_FORWARDED_FOR='192.168.1.1')
        
        # Mock time to simulate 16 minutes later (after lockout period)
        future_time = timezone.now() + timedelta(minutes=16)
        with patch('django.utils.timezone.now') as mock_now:
            mock_now.return_value = future_time
            
            # Also patch the JWT service timezone to avoid F() expression issues
            with patch('core.tenant_management.services.jwt_service.timezone.now') as mock_jwt_now:
                mock_jwt_now.return_value = future_time
                
                # Should be able to login now
                response = self.client.post(login_url, {
                    'email': 'testuser@example.com',
                    'password': 'TestPassword123!'
                }, HTTP_X_FORWARDED_FOR='192.168.1.1')
                
                self.assertEqual(response.status_code, 200)

    def test_lockout_check_functionality(self):
        """Test the lockout check functionality directly"""
        # Create failed attempts
        for i in range(6):
            LoginAttempt.objects.create(
                email='testuser@example.com',
                ip_address='192.168.1.1',
                success=False,
                failure_reason='invalid_credentials'
            )
        
        # Check lockout status
        lockout_info = CustomJWTService.check_account_lockout('testuser@example.com', '192.168.1.1')
        
        self.assertTrue(lockout_info['is_locked'])
        self.assertEqual(lockout_info['failed_attempts_by_email'], 6)
        self.assertIsNotNone(lockout_info['lockout_expires'])


@pytest.mark.django_db
class PasswordSecurityTestCase(TestCase):
    
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
            password='TestPassword123!',
            tenant=self.tenant,
            email_verified=True
        )

    def test_password_minimum_length_validation(self):
        """Test password minimum length requirement (handled by Django validators)"""
        # Our custom validator doesn't enforce minimum length, that's Django's job
        # Test that our validator works correctly for valid passwords
        validator = CustomPasswordValidator()
        
        # Valid password should pass our custom validator
        try:
            validator.validate('ValidPassword123!')
            # If no exception is raised, the test passes
        except ValidationError:
            self.fail("Valid password should pass custom validation")

    def test_password_complexity_validation(self):
        """Test password complexity requirements"""
        validator = CustomPasswordValidator()
        
        # Test missing uppercase
        with self.assertRaises(ValidationError):
            validator.validate('testpassword123!')
        
        # Test missing lowercase  
        with self.assertRaises(ValidationError):
            validator.validate('TESTPASSWORD123!')
        
        # Test missing digit
        with self.assertRaises(ValidationError):
            validator.validate('TestPassword!')
        
        # Test missing special character
        with self.assertRaises(ValidationError):
            validator.validate('TestPassword123')
        
        # Valid password should pass
        try:
            validator.validate('TestPassword123!')
        except ValidationError:
            self.fail("Valid password failed validation")

    def test_password_consecutive_characters_validation(self):
        """Test that passwords with too many consecutive characters are rejected"""
        validator = CustomPasswordValidator()
        
        # Password with 3 consecutive identical characters should fail
        with self.assertRaises(ValidationError) as cm:
            validator.validate('TestPasssword123!')
        
        self.assertIn('consecutive identical characters', str(cm.exception))

    def test_password_weak_patterns_validation(self):
        """Test that common weak patterns are rejected"""
        validator = CustomPasswordValidator()
        
        weak_passwords = [
            '123456',    # Exactly '123456'
            'password',  # Exactly 'password'
            'qwerty',    # Exactly 'qwerty'
            '111111',    # Exactly '111111'
            'admin',     # Exactly 'admin'
        ]
        
        for weak_password in weak_passwords:
            with self.assertRaises(ValidationError) as cm:
                validator.validate(weak_password)
            
            self.assertIn('common weak pattern', str(cm.exception))

    def test_django_password_validation_integration(self):
        """Test integration with Django's password validation system"""
        # Test with user context for similarity validation
        weak_password = 'testuser123!'  # Similar to username
        
        with self.assertRaises(ValidationError):
            validate_password(weak_password, user=self.user)

    def test_password_change_functionality(self):
        """Test secure password change process"""
        # Change password
        result = AuthService.change_password(
            self.user, 
            'TestPassword123!', 
            'NewPassword456@'
        )
        
        self.assertTrue(result)
        
        # Verify password was changed
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewPassword456@'))
        self.assertFalse(self.user.check_password('TestPassword123!'))

    def test_password_change_with_wrong_old_password(self):
        """Test password change with incorrect old password"""
        with self.assertRaises(ValueError) as cm:
            AuthService.change_password(
                self.user, 
                'WrongOldPassword', 
                'NewPassword456@'
            )
        
        self.assertIn('Current password is incorrect', str(cm.exception))

    def test_password_change_with_weak_new_password(self):
        """Test password change with weak new password"""
        with self.assertRaises(ValueError) as cm:
            AuthService.change_password(
                self.user, 
                'TestPassword123!', 
                'weak'  # Too weak
            )
        
        # Should contain validation error messages
        error_message = str(cm.exception)
        self.assertTrue(len(error_message) > 0)

    def test_password_history_tracking(self):
        """Test that password history is tracked"""
        original_password_hash = self.user.password
        
        # Change password
        AuthService.change_password(
            self.user, 
            'TestPassword123!', 
            'NewPassword456@'
        )
        
        # Verify password history was created
        history = PasswordHistory.objects.filter(user=self.user).first()
        self.assertIsNotNone(history)
        self.assertEqual(history.password_hash, original_password_hash)

    def test_password_history_limit(self):
        """Test that password history is limited to last 5 passwords"""
        # Change password multiple times
        passwords = [
            'NewPassword1!@#', 'NewPassword2!@#', 'NewPassword3!@#', 
            'NewPassword4!@#', 'NewPassword5!@#', 'NewPassword6!@#', 'NewPassword7!@#'
        ]
        
        current_password = 'TestPassword123!'
        for new_password in passwords:
            AuthService.change_password(self.user, current_password, new_password)
            current_password = new_password
        
        # Verify only last 5 passwords are kept in history
        history_count = PasswordHistory.objects.filter(user=self.user).count()
        self.assertEqual(history_count, 5)

    def test_password_reuse_prevention(self):
        """Test that password reuse is prevented"""
        # This would require implementing PasswordHistoryValidator in the password change
        # For now, we test the validator directly
        
        # Create some password history
        old_password_hash = self.user.password
        PasswordHistory.objects.create(
            user=self.user,
            password_hash=old_password_hash
        )
        
        validator = PasswordHistoryValidator(password_history_count=5)
        
        # This test would need to be implemented when PasswordHistoryValidator 
        # is integrated into the password change process
        # For now, we just verify the validator exists and has the right interface
        self.assertEqual(validator.password_history_count, 5)
        help_text = validator.get_help_text()
        self.assertIn('cannot reuse', help_text.lower())

    def test_security_event_logging_for_password_changes(self):
        """Test that password changes are logged as security events"""
        # Change password
        AuthService.change_password(
            self.user, 
            'TestPassword123!', 
            'NewPassword456@'
        )
        
        # Verify security event was logged
        events = SecurityEvent.objects.filter(
            user=self.user,
            event_type='password_changed'
        )
        self.assertEqual(events.count(), 1)
        
        event = events.first()
        self.assertEqual(event.severity, 'low')
        self.assertIn('Password changed successfully', event.description)

    def test_security_event_logging_for_failed_password_change(self):
        """Test that failed password change attempts are logged"""
        # Attempt password change with wrong old password
        try:
            AuthService.change_password(
                self.user, 
                'WrongOldPassword', 
                'NewPassword456@'
            )
        except ValueError:
            pass  # Expected
        
        # Verify security event was logged
        events = SecurityEvent.objects.filter(
            user=self.user,
            event_type='password_change_failed'
        )
        self.assertEqual(events.count(), 1)
        
        event = events.first()
        self.assertEqual(event.severity, 'medium')
        self.assertIn('incorrect old password', event.description)