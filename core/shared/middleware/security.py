"""
Security middleware for DIALOGIX
Implements HTTPS redirects and security headers
"""

from django.http import HttpResponsePermanentRedirect, HttpResponseBadRequest
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin
import logging

logger = logging.getLogger('django.security')


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Middleware to add security headers to all responses
    """
    
    def process_response(self, request, response):
        # Content Security Policy
        csp_policy = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://api.gemini.com https://generativelanguage.googleapis.com; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )
        
        # Security Headers
        response['Content-Security-Policy'] = csp_policy
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['X-XSS-Protection'] = '1; mode=block'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = (
            'geolocation=(), microphone=(), camera=(), '
            'payment=(), usb=(), magnetometer=(), gyroscope=()'
        )
        
        # Only add HSTS in production with HTTPS
        if not settings.DEBUG and request.is_secure():
            response['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'

        # Prevent Cloudflare and browsers from caching dynamic HTML pages
        # 'private' tells Cloudflare explicitly this response must not be cached at edge
        content_type = response.get('Content-Type', '')
        if 'text/html' in content_type:
            response['Cache-Control'] = 'private, no-store, no-cache, must-revalidate, max-age=0'
            response['Pragma'] = 'no-cache'
            response['Surrogate-Control'] = 'no-store'
            response['Vary'] = 'Cookie'

        return response


class HTTPSRedirectMiddleware(MiddlewareMixin):
    """
    Middleware to redirect HTTP to HTTPS in production
    Only applies when DEBUG=False
    """
    
    def process_request(self, request):
        # Skip HTTPS redirect during tests
        import sys
        if 'pytest' in sys.modules or 'test' in sys.argv:
            return None
            
        # Only redirect in production (DEBUG=False) and if not already HTTPS
        if not settings.DEBUG and not request.is_secure():
            # Prometheus scrapes /metrics directly on port 9091 without proxy
            if request.path == '/metrics':
                return None
            # Check if we're behind a proxy that handles HTTPS
            if request.META.get('HTTP_X_FORWARDED_PROTO') != 'https':
                # Build the HTTPS URL
                https_url = f"https://{request.get_host()}{request.get_full_path()}"
                return HttpResponsePermanentRedirect(https_url)
        
        return None


class HostHeaderValidationMiddleware(MiddlewareMixin):
    """
    Middleware to validate Host header and prevent Host Header Attacks
    """

    def process_request(self, request):
        """
        Validate the Host header against ALLOWED_HOSTS with CloudFlare protection
        """
        # Get the host from the request
        host = request.get_host()

        # Get allowed hosts from settings
        allowed_hosts = getattr(settings, 'ALLOWED_HOSTS', [])

        # If ALLOWED_HOSTS contains '*', allow any host (not recommended for production)
        if '*' in allowed_hosts:
            return None

        # Normalize host for comparison (remove port if present)
        host_without_port = host.split(':')[0] if ':' in host else host

        # Special validation for IP access - only allow from CloudFlare
        if host_without_port == '35.184.114.83':
            if not self.is_cloudflare_ip(request):
                logger.warning(
                    f"Direct IP access blocked: Host '{host}' accessed from non-CloudFlare IP. "
                    f"Client IP: {self.get_client_ip(request)}, "
                    f"User-Agent: {request.META.get('HTTP_USER_AGENT', 'Unknown')}"
                )
                return HttpResponseBadRequest(
                    "Direct IP access not allowed.",
                    content_type="text/plain"
                )

        # Check if host is in allowed hosts (exact match or wildcard)
        is_allowed = False
        for allowed_host in allowed_hosts:
            if allowed_host.startswith('.'):
                # Wildcard subdomain match (e.g., .example.com)
                if host_without_port.endswith(allowed_host) or host_without_port == allowed_host[1:]:
                    is_allowed = True
                    break
            elif allowed_host == host_without_port:
                # Exact match
                is_allowed = True
                break

        if not is_allowed:
            # Log the security incident
            logger.warning(
                f"Host header attack detected: Host '{host}' not in ALLOWED_HOSTS. "
                f"IP: {self.get_client_ip(request)}, "
                f"User-Agent: {request.META.get('HTTP_USER_AGENT', 'Unknown')}"
            )

            # Return 400 Bad Request for invalid host
            return HttpResponseBadRequest(
                "Invalid Host header. This could be a security attack.",
                content_type="text/plain"
            )

        return None

    def is_cloudflare_ip(self, request):
        """Check if request comes from CloudFlare IP ranges"""
        client_ip = self.get_client_ip(request)

        # CloudFlare IPv4 ranges (principales)
        cloudflare_ranges = [
            '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22',
            '103.31.4.0/22', '141.101.64.0/18', '108.162.192.0/18',
            '190.93.240.0/20', '188.114.96.0/20', '197.234.240.0/22',
            '198.41.128.0/17', '162.158.0.0/15', '172.64.0.0/13',
            '131.0.72.0/22', '104.16.0.0/13', '104.24.0.0/14'
        ]

        try:
            import ipaddress
            client_ip_obj = ipaddress.ip_address(client_ip)

            for cidr in cloudflare_ranges:
                if client_ip_obj in ipaddress.ip_network(cidr):
                    return True

        except (ValueError, ipaddress.AddressValueError):
            return False

        return False

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[-1].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', 'Unknown')
        return ip


class RateLimitingMiddleware(MiddlewareMixin):
    """
    Basic rate limiting middleware
    In production, this should be replaced with Redis-based rate limiting
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        # Simple in-memory rate limiting (NOT for production use)
        self.request_counts = {}
        super().__init__(get_response)
    
    def __call__(self, request):
        # Skip rate limiting in development
        if settings.DEBUG:
            return self.get_response(request)
        
        # Get client IP
        client_ip = self.get_client_ip(request)
        
        # Simple rate limiting logic (implement proper Redis-based solution for production)
        current_time = int(__import__('time').time())
        minute_key = f"{client_ip}:{current_time // 60}"
        
        # Allow up to 60 requests per minute per IP
        if minute_key in self.request_counts:
            self.request_counts[minute_key] += 1
            if self.request_counts[minute_key] > 60:
                from django.http import HttpResponseTooManyRequests
                return HttpResponseTooManyRequests("Rate limit exceeded")
        else:
            self.request_counts[minute_key] = 1
        
        # Clean old entries (keep only last 2 minutes)
        self.cleanup_old_entries(current_time)
        
        response = self.get_response(request)
        return response
    
    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[-1].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
    
    def cleanup_old_entries(self, current_time):
        """Remove rate limit entries older than 2 minutes"""
        current_minute = current_time // 60
        keys_to_remove = []
        
        for key in self.request_counts:
            key_minute = int(key.split(':')[1])
            if current_minute - key_minute > 2:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del self.request_counts[key]