"""
Output Data Sanitization for DIALOGIX
Sanitizes outbound data to prevent information leakage and XSS attacks
"""

import re
import html
import bleach
from typing import Any, Dict, List, Union, Optional
from django.conf import settings
from django.utils.html import strip_tags
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
import logging

logger = logging.getLogger('core.security')

class OutputSanitizer:
    """
    Centralized output sanitization to prevent data leakage and XSS
    """
    
    # Sensitive field patterns that should be masked or removed
    SENSITIVE_FIELD_PATTERNS = [
        r'.*password.*',
        r'.*secret.*',
        r'.*key.*',
        r'.*token.*',
        r'.*api_key.*',
        r'.*private.*',
        r'.*credential.*',
        r'.*auth.*',
        r'.*session.*',
        r'.*csrf.*',
        r'.*ssn.*',
        r'.*social_security.*',
        r'.*credit_card.*',
        r'.*bank_account.*',
        r'.*routing_number.*',
    ]
    
    # PII field patterns that need special handling
    PII_FIELD_PATTERNS = [
        r'.*email.*',
        r'.*phone.*',
        r'.*address.*',
        r'.*birth.*',
        r'.*dob.*',
        r'.*ip_address.*',
        r'.*location.*',
        r'.*geolocation.*',
    ]
    
    # Allowed HTML tags for rich content (very restrictive)
    ALLOWED_HTML_TAGS = [
        'p', 'br', 'strong', 'b', 'em', 'i', 'u', 'ul', 'ol', 'li'
    ]
    
    # Allowed HTML attributes
    ALLOWED_HTML_ATTRIBUTES = {
        '*': ['class']  # Only allow class attribute
    }
    
    @classmethod
    def sanitize_dict(cls, data: Dict[str, Any], user=None, context=None) -> Dict[str, Any]:
        """
        Recursively sanitize dictionary data
        """
        if not isinstance(data, dict):
            return data
        
        sanitized = {}
        
        for key, value in data.items():
            # Check if field should be removed or masked
            if cls._is_sensitive_field(key):
                if settings.DEBUG and not getattr(settings, 'TESTING', False):
                    sanitized[key] = "[REDACTED_SENSITIVE]"
                # In production, completely remove sensitive fields
                continue
            
            elif cls._is_pii_field(key):
                # Handle PII based on user permissions and context
                sanitized[key] = cls._sanitize_pii(value, key, user, context)
            
            else:
                # Recursively sanitize nested data
                sanitized[key] = cls._sanitize_value(value, user, context)
        
        return sanitized
    
    @classmethod
    def sanitize_list(cls, data: List[Any], user=None, context=None) -> List[Any]:
        """
        Sanitize list data
        """
        if not isinstance(data, list):
            return data
        
        return [cls._sanitize_value(item, user, context) for item in data]
    
    @classmethod
    def sanitize_string(cls, text: str, allow_html: bool = False) -> str:
        """
        Sanitize string content to prevent XSS
        """
        if not isinstance(text, str):
            return str(text) if text is not None else ""
        
        # Remove null bytes
        text = text.replace('\x00', '')
        
        if allow_html:
            # Allow specific HTML tags but sanitize
            text = bleach.clean(
                text,
                tags=cls.ALLOWED_HTML_TAGS,
                attributes=cls.ALLOWED_HTML_ATTRIBUTES,
                strip=True
            )
        else:
            # Strip all HTML tags and escape content
            text = strip_tags(text)
            text = html.escape(text)
        
        return text
    
    @classmethod
    def _sanitize_value(cls, value: Any, user=None, context=None) -> Any:
        """
        Sanitize individual values based on type
        """
        if isinstance(value, dict):
            return cls.sanitize_dict(value, user, context)
        elif isinstance(value, list):
            return cls.sanitize_list(value, user, context)
        elif isinstance(value, str):
            return cls.sanitize_string(value)
        else:
            return value
    
    @classmethod
    def _is_sensitive_field(cls, field_name: str) -> bool:
        """
        Check if field contains sensitive data that should be removed
        """
        field_lower = field_name.lower()
        return any(
            re.match(pattern, field_lower, re.IGNORECASE) 
            for pattern in cls.SENSITIVE_FIELD_PATTERNS
        )
    
    @classmethod
    def _is_pii_field(cls, field_name: str) -> bool:
        """
        Check if field contains PII that needs special handling
        """
        field_lower = field_name.lower()
        return any(
            re.match(pattern, field_lower, re.IGNORECASE) 
            for pattern in cls.PII_FIELD_PATTERNS
        )
    
    @classmethod
    def _sanitize_pii(cls, value: Any, field_name: str, user=None, context=None) -> Any:
        """
        Sanitize PII based on permissions and context
        """
        if not isinstance(value, str):
            return value
        
        field_lower = field_name.lower()
        
        # Email sanitization
        if 'email' in field_lower:
            return cls._mask_email(value, user, context)
        
        # Phone sanitization
        elif 'phone' in field_lower:
            return cls._mask_phone(value, user, context)
        
        # IP address sanitization
        elif 'ip' in field_lower:
            return cls._mask_ip(value, user, context)
        
        # Address sanitization
        elif 'address' in field_lower:
            return cls._mask_address(value, user, context)
        
        else:
            # Generic PII masking
            return cls._mask_generic_pii(value)
    
    @classmethod
    def _mask_email(cls, email: str, user=None, context=None) -> str:
        """
        Mask email address for privacy
        """
        if not email or '@' not in email:
            return email
        
        # Check if user can see full email (own email or admin)
        if cls._can_see_full_pii(user, context):
            return email
        
        # Mask email: john.doe@example.com -> j****e@example.com
        local, domain = email.rsplit('@', 1)
        if len(local) <= 2:
            masked_local = local[0] + '*'
        else:
            masked_local = local[0] + '*' * (len(local) - 2) + local[-1]
        
        return f"{masked_local}@{domain}"
    
    @classmethod
    def _mask_phone(cls, phone: str, user=None, context=None) -> str:
        """
        Mask phone number for privacy
        """
        if not phone:
            return phone
        
        if cls._can_see_full_pii(user, context):
            return phone
        
        # Mask phone: +1234567890 -> +1***567890 (show country code and last 4)
        if len(phone) > 6:
            return phone[:3] + '*' * (len(phone) - 6) + phone[-3:]
        else:
            return '*' * len(phone)
    
    @classmethod
    def _mask_ip(cls, ip: str, user=None, context=None) -> str:
        """
        Mask IP address for privacy
        """
        if not ip:
            return ip
        
        if cls._can_see_full_pii(user, context):
            return ip
        
        # Mask IPv4: 192.168.1.100 -> 192.168.*.100
        if '.' in ip and len(ip.split('.')) == 4:
            parts = ip.split('.')
            return f"{parts[0]}.{parts[1]}.*.{parts[3]}"
        
        # Mask IPv6 (simplified)
        elif ':' in ip:
            return ip[:9] + '***'
        
        return ip
    
    @classmethod
    def _mask_address(cls, address: str, user=None, context=None) -> str:
        """
        Mask address for privacy
        """
        if not address:
            return address
        
        if cls._can_see_full_pii(user, context):
            return address
        
        # Keep only city/state/country info, mask street details
        words = address.split()
        if len(words) > 3:
            return f"[STREET MASKED] {' '.join(words[-3:])}"
        
        return "[ADDRESS MASKED]"
    
    @classmethod
    def _mask_generic_pii(cls, value: str) -> str:
        """
        Generic PII masking
        """
        if len(value) <= 4:
            return '*' * len(value)
        
        return value[:2] + '*' * (len(value) - 4) + value[-2:]
    
    @classmethod
    def _can_see_full_pii(cls, user=None, context=None) -> bool:
        """
        Check if user has permission to see full PII
        """
        # No user context - mask everything
        if not user or not hasattr(user, 'is_authenticated') or not user.is_authenticated:
            return False
        
        # Admin users can see full PII
        if hasattr(user, 'is_staff') and user.is_staff:
            return True
        
        # Check context for ownership or special permissions
        if context:
            # If viewing own data
            if context.get('viewing_own_data'):
                return True
            
            # If user has specific permission for this context
            if context.get('has_pii_permission'):
                return True
        
        return False


class SanitizedResponse(Response):
    """
    Custom Response class that automatically sanitizes data
    """
    
    def __init__(self, data=None, status=None, template_name=None, 
                 headers=None, exception=False, content_type=None, 
                 user=None, context=None):
        
        # Sanitize data before creating response
        if data is not None:
            data = OutputSanitizer._sanitize_value(data, user, context)
        
        super().__init__(data, status, template_name, headers, exception, content_type)


class SanitizedJSONRenderer(JSONRenderer):
    """
    Custom JSON renderer that sanitizes data before serialization
    """
    
    def render(self, data, accepted_media_type=None, renderer_context=None):
        """
        Render data to JSON with sanitization
        """
        if data is not None:
            request = renderer_context.get('request') if renderer_context else None
            user = getattr(request, 'user', None) if request else None
            
            # Get sanitization context
            view = renderer_context.get('view') if renderer_context else None
            context = {
                'viewing_own_data': getattr(view, 'is_viewing_own_data', False),
                'has_pii_permission': getattr(view, 'has_pii_permission', False),
            }
            
            # Sanitize data
            data = OutputSanitizer._sanitize_value(data, user, context)
        
        return super().render(data, accepted_media_type, renderer_context)


class SanitizedViewMixin:
    """
    Mixin for views to automatically sanitize response data
    """
    
    def finalize_response(self, request, response, *args, **kwargs):
        """
        Sanitize response data before returning
        """
        response = super().finalize_response(request, response, *args, **kwargs)
        
        if hasattr(response, 'data') and response.data is not None:
            context = {
                'viewing_own_data': getattr(self, 'is_viewing_own_data', False),
                'has_pii_permission': getattr(self, 'has_pii_permission', False),
            }
            
            response.data = OutputSanitizer._sanitize_value(
                response.data, 
                request.user, 
                context
            )
        
        return response
    
    def get_queryset(self):
        """
        Override to set context for own data viewing
        """
        queryset = super().get_queryset()
        
        # Check if user is viewing their own data
        if hasattr(self.request, 'user') and self.request.user.is_authenticated:
            # This will be overridden in specific views based on their logic
            self.is_viewing_own_data = False
            
        return queryset