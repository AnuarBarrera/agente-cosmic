import time
from collections import defaultdict
from django.http import JsonResponse
from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import get_user_model
import logging

User = get_user_model()
logger = logging.getLogger(__name__)


class TenantRateLimitingMiddleware:
    """
    Advanced rate limiting middleware that applies different limits based on tenant subscription plans
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        
        # Default rate limits (requests per minute)
        self.rate_limits = {
            'FREE': {
                'requests_per_minute': 60,
                'requests_per_hour': 1000,
                'requests_per_day': 5000,
            },
            'BASIC': {
                'requests_per_minute': 120,
                'requests_per_hour': 3000,
                'requests_per_day': 20000,
            },
            'PREMIUM': {
                'requests_per_minute': 300,
                'requests_per_hour': 10000,
                'requests_per_day': 100000,
            },
            'ENTERPRISE': {
                'requests_per_minute': 1000,
                'requests_per_hour': 50000,
                'requests_per_day': 500000,
            }
        }
        
        # Whitelist paths that shouldn't be rate limited
        self.whitelist_paths = [
            '/health/',
            '/api/v1/auth/token/',
            '/api/v1/auth/logout/',
            # Stripe manda estos webhooks sin sesion de usuario, desde un pool de
            # IPs compartido entre TODOS sus clientes (no solo nosotros) — sin
            # este whitelist, un pago real podria confirmarse en Stripe pero
            # nunca activarse aqui si el limite de 30 req/min por IP se agota
            # por trafico ajeno o reintentos de Stripe.
            '/stripe/webhook/',
        ]
    
    def __call__(self, request):
        # Skip rate limiting for whitelisted paths
        if any(request.path.startswith(path) for path in self.whitelist_paths):
            return self.get_response(request)
        
        # Check rate limit for authenticated users
        if hasattr(request, 'user') and request.user.is_authenticated:
            user = request.user
            
            # Check if user has a tenant
            if hasattr(user, 'tenant') and user.tenant:
                # Check tenant rate limits
                rate_limit_result = self._check_tenant_rate_limit(user.tenant, request)
                
                if rate_limit_result['limited']:
                    return JsonResponse({
                        'error': 'Rate limit exceeded',
                        'detail': rate_limit_result['message'],
                        'retry_after': rate_limit_result['retry_after']
                    }, status=429)
                
                # Record the request
                self._record_request(user.tenant, request)
        else:
            # Apply basic rate limiting for unauthenticated requests
            ip_address = self._get_client_ip(request)
            if self._check_ip_rate_limit(ip_address):
                return JsonResponse({
                    'error': 'Rate limit exceeded for unauthenticated requests'
                }, status=429)
        
        response = self.get_response(request)
        return response
    
    def _check_tenant_rate_limit(self, tenant, request):
        """
        Check if tenant has exceeded their rate limits
        """
        try:
            # Get tenant's subscription plan
            plan_name = self._get_tenant_plan(tenant)
            limits = self.rate_limits.get(plan_name, self.rate_limits['FREE'])
            
            tenant_id = str(tenant.id)
            current_time = int(time.time())
            
            # Check different time windows
            for period, limit in limits.items():
                window_seconds = self._get_window_seconds(period)
                cache_key = f"rate_limit_{tenant_id}_{period}_{current_time // window_seconds}"
                
                current_count = cache.get(cache_key, 0)
                
                if current_count >= limit:
                    retry_after = window_seconds - (current_time % window_seconds)
                    return {
                        'limited': True,
                        'message': f'Tenant rate limit exceeded: {period}. Limit: {limit}',
                        'retry_after': retry_after
                    }
            
            return {'limited': False}
            
        except Exception as e:
            logger.error(f"Error checking tenant rate limit: {e}")
            # Fail open - don't block requests if there's an error
            return {'limited': False}
    
    def _record_request(self, tenant, request):
        """
        Record a request for rate limiting tracking
        """
        try:
            tenant_id = str(tenant.id)
            current_time = int(time.time())
            
            # Record in different time windows
            for period in ['requests_per_minute', 'requests_per_hour', 'requests_per_day']:
                window_seconds = self._get_window_seconds(period)
                cache_key = f"rate_limit_{tenant_id}_{period}_{current_time // window_seconds}"
                
                # Increment counter with appropriate expiry
                try:
                    cache.incr(cache_key)
                except ValueError:
                    cache.set(cache_key, 1, timeout=window_seconds)
                except Exception as e:
                    logger.warning(f"Failed to update rate limit counter: {e}")
                    
        except Exception as e:
            logger.error(f"Error recording request: {e}")
    
    def _check_ip_rate_limit(self, ip_address):
        """
        Basic IP-based rate limiting for unauthenticated requests
        """
        try:
            current_time = int(time.time())
            minute_window = current_time // 60
            cache_key = f"ip_rate_limit_{ip_address}_{minute_window}"
            
            current_count = cache.get(cache_key, 0)
            limit = 30  # 30 requests per minute for unauthenticated
            
            if current_count >= limit:
                return True
            
            # Increment counter
            try:
                cache.incr(cache_key)
            except ValueError:
                cache.set(cache_key, 1, timeout=60)
            return False
            
        except Exception as e:
            logger.error(f"Error checking IP rate limit: {e}")
            return False
    
    def _get_tenant_plan(self, tenant):
        """
        Get the tenant's subscription plan
        """
        try:
            if hasattr(tenant, 'subscription') and tenant.subscription:
                return tenant.subscription.plan.name
            return 'FREE'  # Default to free plan
        except Exception as e:
            logger.warning(f"Could not determine tenant plan: {e}")
            return 'FREE'
    
    def _get_window_seconds(self, period):
        """
        Convert period string to seconds
        """
        if 'minute' in period:
            return 60
        elif 'hour' in period:
            return 3600
        elif 'day' in period:
            return 86400
        return 60
    
    def _get_client_ip(self, request):
        real_ip = request.META.get('HTTP_X_REAL_IP')
        if real_ip:
            return real_ip
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')


class APIThrottlingMiddleware:
    """
    Specialized throttling for API endpoints based on endpoint type
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        
        # Special limits for different endpoint types
        self.endpoint_limits = {
            '/api/v1/ai/': {
                'FREE': {'requests_per_minute': 10, 'requests_per_hour': 100},
                'BASIC': {'requests_per_minute': 30, 'requests_per_hour': 500},
                'PREMIUM': {'requests_per_minute': 100, 'requests_per_hour': 2000},
                'ENTERPRISE': {'requests_per_minute': 500, 'requests_per_hour': 10000},
            },
            '/api/v1/channels/webhook/': {
                'FREE': {'requests_per_minute': 50, 'requests_per_hour': 1000},
                'BASIC': {'requests_per_minute': 200, 'requests_per_hour': 5000},
                'PREMIUM': {'requests_per_minute': 1000, 'requests_per_hour': 20000},
                'ENTERPRISE': {'requests_per_minute': 5000, 'requests_per_hour': 100000},
            }
        }
    
    def __call__(self, request):
        # Apply specialized throttling for specific endpoints
        if hasattr(request, 'user') and request.user.is_authenticated:
            user = request.user
            
            if hasattr(user, 'tenant') and user.tenant:
                # Check for specialized endpoint limits
                for endpoint_pattern, limits in self.endpoint_limits.items():
                    if request.path.startswith(endpoint_pattern):
                        plan_name = self._get_tenant_plan(user.tenant)
                        endpoint_limits = limits.get(plan_name, limits['FREE'])
                        
                        if self._check_endpoint_limit(user.tenant, endpoint_pattern, endpoint_limits):
                            return JsonResponse({
                                'error': 'API endpoint rate limit exceeded',
                                'endpoint': endpoint_pattern
                            }, status=429)
        
        response = self.get_response(request)
        return response
    
    def _check_endpoint_limit(self, tenant, endpoint, limits):
        """
        Check endpoint-specific rate limits
        """
        try:
            tenant_id = str(tenant.id)
            current_time = int(time.time())
            
            for period, limit in limits.items():
                window_seconds = 60 if 'minute' in period else 3600
                cache_key = f"endpoint_limit_{tenant_id}_{endpoint}_{period}_{current_time // window_seconds}"
                
                current_count = cache.get(cache_key, 0)
                
                if current_count >= limit:
                    return True
                
                # Increment counter
                try:
                    cache.incr(cache_key)
                except ValueError:
                    cache.set(cache_key, 1, timeout=window_seconds)
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking endpoint limit: {e}")
            return False
    
    def _get_tenant_plan(self, tenant):
        """Get tenant's subscription plan"""
        try:
            if hasattr(tenant, 'subscription') and tenant.subscription:
                return tenant.subscription.plan.name
            return 'FREE'
        except Exception:
            return 'FREE'