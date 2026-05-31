import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class CustomPasswordValidator:
    """
    Enhanced password validator that requires:
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    - No more than 2 consecutive identical characters
    """
    
    def validate(self, password, user=None):
        errors = []
        
        # Check for uppercase letter
        if not re.search(r'[A-Z]', password):
            errors.append(_("Password must contain at least one uppercase letter."))
        
        # Check for lowercase letter
        if not re.search(r'[a-z]', password):
            errors.append(_("Password must contain at least one lowercase letter."))
        
        # Check for digit
        if not re.search(r'\d', password):
            errors.append(_("Password must contain at least one digit."))
        
        # Check for special character
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            errors.append(_("Password must contain at least one special character (!@#$%^&*(),.?\":{}|<>)."))
        
        # Check for no more than 2 consecutive identical characters
        if re.search(r'(.)\1{2,}', password):
            errors.append(_("Password must not contain more than 2 consecutive identical characters."))
        
        # Check for common weak patterns (only as standalone words or full matches)
        weak_patterns = [
            r'^123456$',
            r'^password$',
            r'^qwerty$',
            r'^111111$',
            r'^000000$',
            r'^123123$',
            r'^admin$',
        ]
        
        lower_password = password.lower()
        for pattern in weak_patterns:
            if re.match(pattern, lower_password):
                errors.append(_("Password contains a common weak pattern and is not allowed."))
                break
        
        if errors:
            raise ValidationError(errors)
    
    def get_help_text(self):
        return _(
            "Your password must contain at least one uppercase letter, one lowercase letter, "
            "one digit, one special character (!@#$%^&*(),.?\":{}|<>), and must not contain "
            "more than 2 consecutive identical characters."
        )


class PasswordHistoryValidator:
    """
    Validator to prevent reuse of recent passwords
    """
    
    def __init__(self, password_history_count=5):
        self.password_history_count = password_history_count
    
    def validate(self, password, user=None):
        if user and hasattr(user, 'password_history'):
            # Check against recent passwords
            from django.contrib.auth.hashers import check_password
            recent_passwords = user.password_history.order_by('-created_at')[:self.password_history_count]
            
            for old_password in recent_passwords:
                if check_password(password, old_password.password_hash):
                    raise ValidationError(
                        _("You cannot reuse any of your last %(count)d passwords.") % {
                            'count': self.password_history_count
                        }
                    )
    
    def get_help_text(self):
        return _(
            "You cannot reuse any of your last %(count)d passwords." % {
                'count': self.password_history_count
            }
        )