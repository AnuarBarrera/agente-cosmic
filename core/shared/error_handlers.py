"""
Secure Error Handling for DIALOGIX
Prevents information leakage in error responses while maintaining logging
"""

import logging
import traceback
import sys
from typing import Dict, Any, Optional
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import IntegrityError, DatabaseError
from rest_framework import status
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework.exceptions import (
    AuthenticationFailed,
    PermissionDenied as DRFPermissionDenied,
    NotFound,
    ValidationError as DRFValidationError,
    Throttled,
    ParseError,
    UnsupportedMediaType,
    MethodNotAllowed,
)
import sentry_sdk

logger = logging.getLogger('core.security')
error_logger = logging.getLogger('core.errors')

class SecureErrorHandler:
    """
    Centralized error handling with security-focused response sanitization
    """
    
    # Generic error messages to prevent information leakage
    GENERIC_MESSAGES = {
        'server_error': 'An internal server error occurred. Please try again later.',
        'database_error': 'A database error occurred. Please contact support if the problem persists.',
        'validation_error': 'The provided data is invalid.',
        'authentication_error': 'Authentication failed.',
        'permission_error': 'You do not have permission to perform this action.',
        'not_found': 'The requested resource was not found.',
        'rate_limited': 'Too many requests. Please slow down.',
        'bad_request': 'The request could not be processed.',
        'unsupported_media': 'Unsupported media type.',
        'method_not_allowed': 'Method not allowed for this endpoint.',
    }
    
    @classmethod
    def log_error(cls, error: Exception, request: HttpRequest = None, extra_context: Dict = None) -> str:
        """
        Log error details securely for debugging while generating safe error ID
        """
        import uuid
        error_id = str(uuid.uuid4())[:8]
        
        # Prepare logging context
        log_context = {
            'error_id': error_id,
            'error_type': type(error).__name__,
            'error_message': str(error),
            'traceback': traceback.format_exc(),
        }
        
        if request:
            log_context.update({
                'method': request.method,
                'path': request.path,
                'user': getattr(request.user, 'id', 'anonymous') if hasattr(request, 'user') else 'unknown',
                'ip': cls.get_client_ip(request),
                'user_agent': request.META.get('HTTP_USER_AGENT', 'unknown')[:200],
            })
        
        if extra_context:
            log_context.update(extra_context)
        
        # Log error with full details
        error_logger.error(
            f"Error {error_id}: {type(error).__name__}: {str(error)[:200]}",
            extra=log_context
        )
        
        # Send to Sentry if configured
        if hasattr(sentry_sdk, 'capture_exception'):
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("error_id", error_id)
                if request and hasattr(request, 'user'):
                    scope.user = {
                        "id": getattr(request.user, 'id', None),
                        "ip_address": cls.get_client_ip(request),
                    }
                sentry_sdk.capture_exception(error)
        
        return error_id
    
    @classmethod
    def get_client_ip(cls, request: HttpRequest) -> str:
        """
        Get client IP address from request
        """
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', 'unknown')
        return ip
    
    @classmethod
    def create_error_response(
        cls, 
        error: Exception, 
        request: HttpRequest = None, 
        status_code: int = 500,
        safe_message: str = None
    ) -> Dict[str, Any]:
        """
        Create a safe error response that doesn't leak sensitive information
        """
        error_id = cls.log_error(error, request)
        
        # Base response structure
        response_data = {
            'error': True,
            'error_id': error_id,
            'message': safe_message or cls.GENERIC_MESSAGES.get('server_error'),
            'status_code': status_code,
        }
        
        # In debug mode, include more details (development only)
        if settings.DEBUG and not getattr(settings, 'TESTING', False):
            response_data.update({
                'debug_info': {
                    'error_type': type(error).__name__,
                    'error_message': str(error)[:500],  # Limit length
                }
            })
        
        return response_data
    
    @classmethod
    def handle_validation_error(cls, error: ValidationError, request: HttpRequest = None) -> JsonResponse:
        """
        Handle Django ValidationError with secure response
        """
        error_id = cls.log_error(error, request)
        
        if hasattr(error, 'error_dict'):
            # Field-specific validation errors
            sanitized_errors = {}
            for field, field_errors in error.error_dict.items():
                sanitized_errors[field] = []
                for field_error in field_errors:
                    # Sanitize error messages to prevent information leakage
                    message = str(field_error.message)
                    if any(sensitive in message.lower() for sensitive in ['password', 'secret', 'key', 'token']):
                        message = "Invalid value provided"
                    sanitized_errors[field].append(message)
            
            return JsonResponse({
                'error': True,
                'error_id': error_id,
                'message': 'Validation failed',
                'field_errors': sanitized_errors,
                'status_code': 400,
            }, status=400)
        else:
            return JsonResponse(
                cls.create_error_response(error, request, 400, cls.GENERIC_MESSAGES['validation_error']),
                status=400
            )
    
    @classmethod
    def handle_database_error(cls, error: DatabaseError, request: HttpRequest = None) -> JsonResponse:
        """
        Handle database errors without leaking schema information
        """
        error_id = cls.log_error(error, request)
        
        # Never expose database schema or query details
        return JsonResponse(
            cls.create_error_response(error, request, 500, cls.GENERIC_MESSAGES['database_error']),
            status=500
        )


def custom_exception_handler(exc, context):
    """
    Custom DRF exception handler that prevents information leakage
    """
    request = context.get('request')
    
    # Handle specific exception types
    if isinstance(exc, AuthenticationFailed):
        error_id = SecureErrorHandler.log_error(exc, request)
        return Response({
            'error': True,
            'error_id': error_id,
            'message': SecureErrorHandler.GENERIC_MESSAGES['authentication_error'],
            'status_code': 401,
        }, status=status.HTTP_401_UNAUTHORIZED)
    
    elif isinstance(exc, (PermissionDenied, DRFPermissionDenied)):
        error_id = SecureErrorHandler.log_error(exc, request)
        return Response({
            'error': True,
            'error_id': error_id,
            'message': SecureErrorHandler.GENERIC_MESSAGES['permission_error'],
            'status_code': 403,
        }, status=status.HTTP_403_FORBIDDEN)
    
    elif isinstance(exc, NotFound):
        error_id = SecureErrorHandler.log_error(exc, request)
        return Response({
            'error': True,
            'error_id': error_id,
            'message': SecureErrorHandler.GENERIC_MESSAGES['not_found'],
            'status_code': 404,
        }, status=status.HTTP_404_NOT_FOUND)
    
    elif isinstance(exc, DRFValidationError):
        error_id = SecureErrorHandler.log_error(exc, request)
        
        # Sanitize validation error details
        if hasattr(exc, 'detail') and isinstance(exc.detail, dict):
            sanitized_details = {}
            for field, field_errors in exc.detail.items():
                sanitized_details[field] = []
                for error in field_errors:
                    error_msg = str(error)
                    # Remove potentially sensitive information
                    if any(sensitive in error_msg.lower() for sensitive in ['password', 'secret', 'key', 'token']):
                        error_msg = "Invalid value provided"
                    sanitized_details[field].append(error_msg)
            
            return Response({
                'error': True,
                'error_id': error_id,
                'message': 'Validation failed',
                'field_errors': sanitized_details,
                'status_code': 400,
            }, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({
            'error': True,
            'error_id': error_id,
            'message': SecureErrorHandler.GENERIC_MESSAGES['validation_error'],
            'status_code': 400,
        }, status=status.HTTP_400_BAD_REQUEST)
    
    elif isinstance(exc, Throttled):
        error_id = SecureErrorHandler.log_error(exc, request)
        return Response({
            'error': True,
            'error_id': error_id,
            'message': SecureErrorHandler.GENERIC_MESSAGES['rate_limited'],
            'retry_after': exc.wait,
            'status_code': 429,
        }, status=status.HTTP_429_TOO_MANY_REQUESTS)
    
    elif isinstance(exc, (ParseError, UnsupportedMediaType)):
        error_id = SecureErrorHandler.log_error(exc, request)
        message = (SecureErrorHandler.GENERIC_MESSAGES['unsupported_media'] 
                  if isinstance(exc, UnsupportedMediaType) 
                  else SecureErrorHandler.GENERIC_MESSAGES['bad_request'])
        
        return Response({
            'error': True,
            'error_id': error_id,
            'message': message,
            'status_code': 400,
        }, status=status.HTTP_400_BAD_REQUEST)
    
    elif isinstance(exc, MethodNotAllowed):
        error_id = SecureErrorHandler.log_error(exc, request)
        return Response({
            'error': True,
            'error_id': error_id,
            'message': SecureErrorHandler.GENERIC_MESSAGES['method_not_allowed'],
            'allowed_methods': list(exc.detail.keys()) if hasattr(exc, 'detail') else [],
            'status_code': 405,
        }, status=status.HTTP_405_METHOD_NOT_ALLOWED)
    
    elif isinstance(exc, (DatabaseError, IntegrityError)):
        return JsonResponse(
            SecureErrorHandler.create_error_response(exc, request, 500, 
                SecureErrorHandler.GENERIC_MESSAGES['database_error']),
            status=500
        )
    
    # Default to DRF's exception handler for other cases, but sanitize the response
    response = exception_handler(exc, context)
    
    if response is not None:
        error_id = SecureErrorHandler.log_error(exc, request)
        
        # Sanitize the response to prevent information leakage
        custom_response_data = {
            'error': True,
            'error_id': error_id,
            'message': SecureErrorHandler.GENERIC_MESSAGES.get('server_error'),
            'status_code': response.status_code,
        }
        
        # Only include debug info in development
        if settings.DEBUG and not getattr(settings, 'TESTING', False):
            custom_response_data['debug_info'] = {
                'original_error': str(exc)[:200],
                'error_type': type(exc).__name__,
            }
        
        response.data = custom_response_data
    
    return response


# Django view error handlers
def handler400(request, exception):
    """Custom 400 error handler"""
    error_id = SecureErrorHandler.log_error(exception, request)
    return JsonResponse(
        SecureErrorHandler.create_error_response(exception, request, 400, 
            SecureErrorHandler.GENERIC_MESSAGES['bad_request']),
        status=400
    )


def handler403(request, exception):
    """Custom 403 error handler"""
    error_id = SecureErrorHandler.log_error(exception, request)
    return JsonResponse(
        SecureErrorHandler.create_error_response(exception, request, 403, 
            SecureErrorHandler.GENERIC_MESSAGES['permission_error']),
        status=403
    )


def handler404(request, exception):
    """Custom 404 error handler"""
    error_id = SecureErrorHandler.log_error(exception, request)
    accept = request.META.get('HTTP_ACCEPT', '')
    if 'text/html' in accept and 'application/json' not in accept:
        from django.shortcuts import render
        return render(request, '404.html', status=404)
    return JsonResponse(
        SecureErrorHandler.create_error_response(exception, request, 404,
            SecureErrorHandler.GENERIC_MESSAGES['not_found']),
        status=404
    )


def handler500(request):
    """Custom 500 error handler"""
    exc_info = sys.exc_info()
    if exc_info[1]:
        error_id = SecureErrorHandler.log_error(exc_info[1], request)
    else:
        error_id = 'unknown'
    
    return JsonResponse(
        SecureErrorHandler.create_error_response(
            Exception("Internal server error"), request, 500, 
            SecureErrorHandler.GENERIC_MESSAGES['server_error']
        ),
        status=500
    )