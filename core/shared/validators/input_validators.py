"""
Input Validation and Sanitization for DIALOGIX
Security-focused validators to prevent injection attacks and data corruption
"""

import re
import html
import bleach
from typing import Any, Dict, List, Optional, Union
from django.core.exceptions import ValidationError
from django.utils.html import strip_tags
from django.core.validators import validate_email, URLValidator
from rest_framework import serializers
import logging

logger = logging.getLogger('core.security')

class SecurityInputValidator:
    """
    Centralized input validation and sanitization class
    Prevents XSS, SQL injection, and other security vulnerabilities
    """
    
    # Dangerous patterns that should be blocked
    DANGEROUS_PATTERNS = [
        r'<script[^>]*>.*?</script>',  # Script tags
        r'javascript:',  # JavaScript URLs
        r'vbscript:',   # VBScript URLs
        r'onload\s*=',  # Event handlers
        r'onerror\s*=',
        r'onclick\s*=',
        r'onmouseover\s*=',
        r'<iframe[^>]*>',  # iframes
        r'<object[^>]*>',  # objects
        r'<embed[^>]*>',   # embeds
        r'<link[^>]*>',    # links
        r'<meta[^>]*>',    # meta tags
        r'expression\s*\(',  # CSS expressions
        r'url\s*\(',       # CSS url()
        r'@import',        # CSS imports
        r'<\?php',         # PHP tags
        r'<%',             # ASP tags
        r'\${',            # Template injection
        r'{{',             # Template injection
        r'exec\s*\(',      # Command execution
        r'eval\s*\(',      # Code evaluation
        r'system\s*\(',    # System calls
        r'shell_exec\s*\(',# Shell execution
    ]
    
    # Allowed HTML tags for rich text (very restrictive)
    ALLOWED_HTML_TAGS = [
        'p', 'br', 'strong', 'b', 'em', 'i', 'u', 
        'ul', 'ol', 'li', 'blockquote'
    ]
    
    # Allowed HTML attributes
    ALLOWED_HTML_ATTRIBUTES = {
        '*': ['class'],  # Only allow class attribute on any tag
    }
    
    @classmethod
    def sanitize_string(cls, value: str, allow_html: bool = False) -> str:
        """
        Sanitize string input to prevent XSS and injection attacks
        """
        if not isinstance(value, str):
            return str(value)
        
        # Remove null bytes
        value = value.replace('\x00', '')
        
        # Check for dangerous patterns
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, value, re.IGNORECASE):
                logger.warning(f"Dangerous pattern detected in input: {pattern[:50]}...")
                raise ValidationError(f"Input contains potentially dangerous content")
        
        if allow_html:
            # Allow specific HTML tags but sanitize
            value = bleach.clean(
                value,
                tags=cls.ALLOWED_HTML_TAGS,
                attributes=cls.ALLOWED_HTML_ATTRIBUTES,
                strip=True
            )
        else:
            # Strip all HTML tags
            value = strip_tags(value)
            # HTML escape remaining content
            value = html.escape(value)
        
        return value.strip()
    
    @classmethod
    def validate_length(cls, value: str, min_length: int = 0, max_length: int = 10000) -> str:
        """
        Validate string length constraints
        """
        if len(value) < min_length:
            raise ValidationError(f"Input too short. Minimum length: {min_length}")
        
        if len(value) > max_length:
            raise ValidationError(f"Input too long. Maximum length: {max_length}")
        
        return value
    
    @classmethod
    def validate_email_input(cls, email: str) -> str:
        """
        Validate and sanitize email input
        """
        email = cls.sanitize_string(email, allow_html=False)
        
        try:
            validate_email(email)
        except ValidationError:
            raise ValidationError("Invalid email format")
        
        # Additional email security checks
        if len(email) > 254:  # RFC 5321 limit
            raise ValidationError("Email address too long")
        
        # Check for dangerous characters in email
        dangerous_chars = ['<', '>', '"', '\\', '\n', '\r', '\t']
        if any(char in email for char in dangerous_chars):
            raise ValidationError("Email contains invalid characters")
        
        return email.lower()  # Normalize to lowercase
    
    @classmethod
    def validate_url_input(cls, url: str) -> str:
        """
        Validate and sanitize URL input
        """
        url = cls.sanitize_string(url, allow_html=False)
        
        # Check URL format
        validator = URLValidator()
        try:
            validator(url)
        except ValidationError:
            raise ValidationError("Invalid URL format")
        
        # Security: Only allow specific protocols
        allowed_schemes = ['http', 'https']
        if not any(url.startswith(f'{scheme}://') for scheme in allowed_schemes):
            raise ValidationError("URL must use HTTP or HTTPS protocol")
        
        return url
    
    @classmethod
    def validate_phone_input(cls, phone: str) -> str:
        """
        Validate and sanitize phone number input
        """
        phone = cls.sanitize_string(phone, allow_html=False)
        
        # Remove all non-digit characters except + at the beginning
        phone_clean = re.sub(r'[^\d+]', '', phone)
        
        # Validate phone number format (international format)
        if not re.match(r'^\+?[1-9]\d{6,14}$', phone_clean):
            raise ValidationError("Invalid phone number format")
        
        return phone_clean
    
    @classmethod
    def validate_json_input(cls, json_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and sanitize JSON input
        """
        if not isinstance(json_data, dict):
            raise ValidationError("Input must be a valid JSON object")
        
        # Recursively sanitize all string values in JSON
        def sanitize_json_recursive(obj):
            if isinstance(obj, dict):
                return {key: sanitize_json_recursive(value) for key, value in obj.items()}
            elif isinstance(obj, list):
                return [sanitize_json_recursive(item) for item in obj]
            elif isinstance(obj, str):
                return cls.sanitize_string(obj, allow_html=False)
            else:
                return obj
        
        return sanitize_json_recursive(json_data)
    
    @classmethod
    def validate_alphanumeric(cls, value: str, allow_spaces: bool = True) -> str:
        """
        Validate that input contains only alphanumeric characters
        """
        value = cls.sanitize_string(value, allow_html=False)
        
        pattern = r'^[a-zA-Z0-9\s]+$' if allow_spaces else r'^[a-zA-Z0-9]+$'
        if not re.match(pattern, value):
            raise ValidationError("Input must contain only alphanumeric characters")
        
        return value
    
    @classmethod
    def validate_slug(cls, value: str) -> str:
        """
        Validate slug format (URL-safe identifier)
        """
        value = cls.sanitize_string(value, allow_html=False)
        
        if not re.match(r'^[a-z0-9-_]+$', value.lower()):
            raise ValidationError("Slug must contain only lowercase letters, numbers, hyphens, and underscores")
        
        return value.lower()


class SecureSerializerMixin:
    """
    Mixin for Django REST Framework serializers to add automatic input validation
    """
    
    def validate(self, data):
        """
        Apply security validation to all fields
        """
        # Apply parent validation first
        data = super().validate(data)
        
        # Apply security validation to all string fields
        for field_name, value in data.items():
            if isinstance(value, str):
                # Get field-specific validation rules
                field = self.fields.get(field_name)
                if field:
                    max_length = getattr(field, 'max_length', 10000)
                    allow_html = getattr(field, 'allow_html', False)
                    
                    # Apply validation
                    try:
                        data[field_name] = SecurityInputValidator.sanitize_string(
                            value, allow_html=allow_html
                        )
                        data[field_name] = SecurityInputValidator.validate_length(
                            data[field_name], max_length=max_length
                        )
                    except ValidationError as e:
                        raise serializers.ValidationError({field_name: str(e)})
        
        return data


# Custom DRF fields with built-in security validation
class SecureCharField(serializers.CharField):
    """
    CharField with built-in security validation
    """
    
    def __init__(self, allow_html=False, **kwargs):
        self.allow_html = allow_html
        super().__init__(**kwargs)
    
    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        return SecurityInputValidator.sanitize_string(data, allow_html=self.allow_html)


class SecureEmailField(serializers.EmailField):
    """
    EmailField with enhanced security validation
    """
    
    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        return SecurityInputValidator.validate_email_input(data)


class SecureURLField(serializers.URLField):
    """
    URLField with security validation
    """
    
    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        return SecurityInputValidator.validate_url_input(data)


class SecureSlugField(serializers.SlugField):
    """
    SlugField with enhanced security validation
    """
    
    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        return SecurityInputValidator.validate_slug(data)