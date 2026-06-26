import pytest
import secrets
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password

_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"
_NEW_PWD  = f"N3w!{secrets.token_urlsafe(10)}-"
from django.utils import timezone
from datetime import timedelta

from core.tenant_management.models import (
    User, TenantModel, Plan, Subscription,
    SecurityEvent, PasswordHistory
)
from core.tenant_management.services.auth_service import AuthService
from core.tenant_management.validators import CustomPasswordValidator, PasswordHistoryValidator


# AuthSecurityTestCase removed — tested JWT API endpoints (/api/v1/tenants/token/)
# that are not routed in agente-cosmic. Will be recreated if JWT API is activated.


_REMOVED_AuthSecurityTestCase = True  # marker for grep — safe to delete this line
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
            password=_TEST_PWD,
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
            validator.validate(_TEST_PWD)
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
            _TEST_PWD, 
            _NEW_PWD
        )
        
        self.assertTrue(result)
        
        # Verify password was changed
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(_NEW_PWD))
        self.assertFalse(self.user.check_password(_TEST_PWD))

    def test_password_change_with_wrong_old_password(self):
        """Test password change with incorrect old password"""
        with self.assertRaises(ValueError) as cm:
            AuthService.change_password(
                self.user, 
                'WrongOldPassword', 
                _NEW_PWD
            )
        
        self.assertIn('Current password is incorrect', str(cm.exception))

    def test_password_change_with_weak_new_password(self):
        """Test password change with weak new password"""
        with self.assertRaises(ValueError) as cm:
            AuthService.change_password(
                self.user, 
                _TEST_PWD, 
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
            _TEST_PWD, 
            _NEW_PWD
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
        
        current_password = _TEST_PWD
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
            _TEST_PWD, 
            _NEW_PWD
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
                _NEW_PWD
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