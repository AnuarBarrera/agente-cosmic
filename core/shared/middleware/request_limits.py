"""
Request Limits and Timeout Middleware
Security middleware to prevent DoS attacks and resource exhaustion
"""

import time
import json
import logging
from typing import Optional
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger('core.security')

class RequestSizeLimitMiddleware(MiddlewareMixin):
    """
    Middleware to limit request size to prevent memory exhaustion attacks
    """
    
    # Default size limits in bytes
    DEFAULT_MAX_REQUEST_SIZE = 10 * 1024 * 1024  # 10MB
    DEFAULT_MAX_JSON_SIZE = 5 * 1024 * 1024      # 5MB
    DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024     # 50MB
    
    def __init__(self, get_response):
        self.get_response = get_response
        
        # Get size limits from settings
        self.max_request_size = getattr(
            settings, 
            'MAX_REQUEST_SIZE', 
            self.DEFAULT_MAX_REQUEST_SIZE
        )
        self.max_json_size = getattr(
            settings, 
            'MAX_JSON_SIZE', 
            self.DEFAULT_MAX_JSON_SIZE
        )
        self.max_file_size = getattr(
            settings, 
            'MAX_FILE_SIZE', 
            self.DEFAULT_MAX_FILE_SIZE
        )
        
        super().__init__(get_response)
    
    def process_request(self, request: HttpRequest) -> Optional[HttpResponse]:
        """
        Check request size before processing
        """
        content_length = request.META.get('CONTENT_LENGTH')
        
        if content_length:
            try:
                content_length = int(content_length)
            except (ValueError, TypeError):
                logger.warning(f"Invalid Content-Length header: {content_length}")
                return JsonResponse(
                    {'error': 'Invalid Content-Length header'}, 
                    status=400
                )
            
            # Check overall request size
            if content_length > self.max_request_size:
                logger.warning(
                    f"Request size {content_length} exceeds limit {self.max_request_size}"
                )
                return JsonResponse(
                    {
                        'error': 'Request size too large',
                        'max_size': self.max_request_size
                    }, 
                    status=413  # Request Entity Too Large
                )
            
            # Special handling for JSON requests
            content_type = request.META.get('CONTENT_TYPE', '')
            if 'application/json' in content_type and content_length > self.max_json_size:
                logger.warning(
                    f"JSON request size {content_length} exceeds JSON limit {self.max_json_size}"
                )
                return JsonResponse(
                    {
                        'error': 'JSON payload too large',
                        'max_size': self.max_json_size
                    }, 
                    status=413
                )
            
            # Special handling for file uploads
            if 'multipart/form-data' in content_type and content_length > self.max_file_size:
                logger.warning(
                    f"File upload size {content_length} exceeds file limit {self.max_file_size}"
                )
                return JsonResponse(
                    {
                        'error': 'File upload too large',
                        'max_size': self.max_file_size
                    }, 
                    status=413
                )
        
        return None


class RequestTimeoutMiddleware(MiddlewareMixin):
    """
    Middleware to enforce request timeout limits
    """
    
    DEFAULT_TIMEOUT = 30  # 30 seconds
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout = getattr(settings, 'REQUEST_TIMEOUT', self.DEFAULT_TIMEOUT)
        super().__init__(get_response)
    
    def process_request(self, request: HttpRequest) -> None:
        """
        Set request start time for timeout tracking
        """
        request._start_time = time.time()
    
    def process_response(self, request: HttpRequest, response: HttpResponse) -> HttpResponse:
        """
        Check if request processing exceeded timeout
        """
        if hasattr(request, '_start_time'):
            processing_time = time.time() - request._start_time
            
            if processing_time > self.timeout:
                logger.warning(
                    f"Request processing time {processing_time:.2f}s exceeded timeout {self.timeout}s"
                )
                # Log but don't interrupt already processed response
            
            # Add processing time to response headers for monitoring
            response['X-Processing-Time'] = f"{processing_time:.3f}"
        
        return response


class RequestBodyValidationMiddleware(MiddlewareMixin):
    """
    Middleware to validate and sanitize request body content
    """
    
    MAX_NESTED_LEVELS = 10  # Prevent deeply nested JSON DoS
    MAX_ARRAY_ITEMS = 1000  # Prevent large array DoS
    
    def __init__(self, get_response):
        self.get_response = get_response
        super().__init__(get_response)
    
    def process_request(self, request: HttpRequest) -> Optional[HttpResponse]:
        """
        Validate request body structure
        """
        content_type = request.META.get('CONTENT_TYPE', '')
        
        # Only validate JSON for methods that should have a body (POST, PUT, PATCH)
        if (
            'application/json' in content_type 
            and hasattr(request, 'body') 
            and request.method in ['POST', 'PUT', 'PATCH']
            and len(request.body) > 0
        ):
            try:
                # Parse JSON body
                json_data = json.loads(request.body.decode('utf-8'))
                
                # Validate JSON structure
                if not self._validate_json_structure(json_data):
                    logger.warning("Request contains invalid JSON structure")
                    return JsonResponse(
                        {'error': 'Invalid JSON structure'}, 
                        status=400
                    )
                
            except json.JSONDecodeError:
                logger.warning("Request contains invalid JSON")
                return JsonResponse(
                    {'error': 'Invalid JSON format'}, 
                    status=400
                )
            except UnicodeDecodeError:
                logger.warning("Request contains invalid UTF-8 encoding")
                return JsonResponse(
                    {'error': 'Invalid character encoding'}, 
                    status=400
                )
        
        return None
    
    def _validate_json_structure(self, obj, level=0):
        """
        Recursively validate JSON structure to prevent DoS attacks
        """
        if level > self.MAX_NESTED_LEVELS:
            return False
        
        if isinstance(obj, dict):
            if len(obj) > self.MAX_ARRAY_ITEMS:
                return False
            
            for key, value in obj.items():
                if not isinstance(key, str) or len(key) > 1000:
                    return False
                
                if not self._validate_json_structure(value, level + 1):
                    return False
        
        elif isinstance(obj, list):
            if len(obj) > self.MAX_ARRAY_ITEMS:
                return False
            
            for item in obj:
                if not self._validate_json_structure(item, level + 1):
                    return False
        
        elif isinstance(obj, str):
            # Check string length
            if len(obj) > 100000:  # 100KB max string
                return False
            
            # Check for null bytes and other dangerous characters
            if '\x00' in obj:
                return False
        
        return True


class SecurityHeadersEnforcementMiddleware(MiddlewareMixin):
    """
    Middleware to enforce security headers on all responses
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        super().__init__(get_response)
    
    def process_response(self, request: HttpRequest, response: HttpResponse) -> HttpResponse:
        """
        Add security headers to response
        """
        # Content Security Policy
        if not response.get('Content-Security-Policy'):
            csp_policy = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
            response['Content-Security-Policy'] = csp_policy
        
        # X-Content-Type-Options
        if not response.get('X-Content-Type-Options'):
            response['X-Content-Type-Options'] = 'nosniff'
        
        # X-Frame-Options
        if not response.get('X-Frame-Options'):
            response['X-Frame-Options'] = 'DENY'
        
        # X-XSS-Protection
        if not response.get('X-XSS-Protection'):
            response['X-XSS-Protection'] = '1; mode=block'
        
        # Referrer Policy
        if not response.get('Referrer-Policy'):
            response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        # Permissions Policy
        if not response.get('Permissions-Policy'):
            response['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        
        return response