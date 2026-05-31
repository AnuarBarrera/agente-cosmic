from django.http import JsonResponse
from django.contrib.auth import get_user_model
import logging

User = get_user_model()
logger = logging.getLogger(__name__)


class TenantIsolationMiddleware:
    """
    Middleware to enforce tenant isolation by automatically setting tenant_id
    from the authenticated user and preventing tenant_id manipulation in requests
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Process the request before view
        if hasattr(request, 'user') and request.user.is_authenticated:
            logger.info(f"TenantIsolation: Processing request for user {request.user.email} to {request.path}")
            tenant_response = self._enforce_tenant_isolation(request)
            if tenant_response:
                logger.info(f"TenantIsolation: Blocking access for user {request.user.email}")
                return tenant_response
        
        response = self.get_response(request)
        return response
    
    def _enforce_tenant_isolation(self, request):
        """
        Enforce tenant isolation by:
        1. Setting the correct tenant_id from user
        2. Preventing tenant_id manipulation in request data
        3. Logging suspicious attempts
        """
        user = request.user
        
        # Force debugging - check if user has tenant
        has_tenant_attr = hasattr(user, 'tenant')
        tenant_value = user.tenant if has_tenant_attr else None
        logger.info(f"User {user.email}: has_tenant_attr={has_tenant_attr}, tenant_value={tenant_value}")
        
        if not hasattr(user, 'tenant') or not user.tenant:
            # Allow superusers to access admin without tenant requirement
            if user.is_superuser and request.path.startswith('/admin'):
                logger.info(f"Superuser {user.email} accessing admin without tenant - allowed")
                return None
            
            logger.warning(f"User {user.email} has no associated tenant - blocking access")
            # Return forbidden response for users without tenant
            response = JsonResponse({
                'error': 'Access forbidden',
                'message': 'User is not associated with any tenant'
            }, status=403)
            logger.info(f"Returning 403 response for user {user.email}")
            return response
        
        user_tenant_id = str(user.tenant.id)
        
        # Check for tenant_id manipulation in query params
        requested_tenant_id = request.GET.get('tenant_id')
        if requested_tenant_id and requested_tenant_id != user_tenant_id:
            logger.warning(
                f"User {user.email} (tenant {user_tenant_id}) attempted to access "
                f"tenant {requested_tenant_id} via query params"
            )
            # Record security event
            self._record_security_event(
                user, 
                'unauthorized_tenant_access_attempt',
                f'Attempted to access tenant {requested_tenant_id} via query params',
                'medium'
            )
        
        # Check for tenant_id manipulation in POST/PUT/PATCH data
        if request.method in ['POST', 'PUT', 'PATCH'] and hasattr(request, 'data'):
            if isinstance(request.data, dict):
                requested_tenant_id = request.data.get('tenant_id')
                if requested_tenant_id and str(requested_tenant_id) != user_tenant_id:
                    logger.warning(
                        f"User {user.email} (tenant {user_tenant_id}) attempted to "
                        f"access tenant {requested_tenant_id} via request data"
                    )
                    # Record security event
                    self._record_security_event(
                        user,
                        'unauthorized_tenant_access_attempt', 
                        f'Attempted to access tenant {requested_tenant_id} via request data',
                        'medium'
                    )
                
                # Force the correct tenant_id
                request.data['tenant_id'] = user_tenant_id
        
        # Set tenant_id attribute on request for easy access in views
        request.tenant_id = user_tenant_id
    
    def _record_security_event(self, user, event_type, description, severity):
        """Record a security event"""
        try:
            from core.tenant_management.models import SecurityEvent
            
            SecurityEvent.objects.create(
                user=user,
                event_type=event_type,
                description=description,
                severity=severity,
                additional_data={
                    'middleware': 'TenantIsolationMiddleware'
                }
            )
        except Exception as e:
            logger.error(f"Failed to record security event: {e}")


def tenant_required(view_func):
    """
    Decorator to ensure the user has a valid tenant before accessing the view
    """
    def wrapper(request, *args, **kwargs):
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        
        user = request.user
        if not hasattr(user, 'tenant') or not user.tenant:
            # Allow superusers to proceed without tenant
            if user.is_superuser:
                logger.info(f"Superuser {user.email} proceeding without tenant requirement")
                return view_func(request, *args, **kwargs)
            
            logger.warning(f"User {user.email} has no associated tenant")
            return JsonResponse({'error': 'User has no associated tenant'}, status=403)
        
        return view_func(request, *args, **kwargs)
    
    return wrapper


def enforce_tenant_isolation(view_func):
    """
    Decorator to enforce tenant isolation in views by checking tenant_id parameters
    """
    def wrapper(request, *args, **kwargs):
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        
        user = request.user
        if not hasattr(user, 'tenant') or not user.tenant:
            # Allow superusers to proceed without tenant
            if user.is_superuser:
                logger.info(f"Superuser {user.email} proceeding without tenant requirement")
                return view_func(request, *args, **kwargs)
            return JsonResponse({'error': 'User has no associated tenant'}, status=403)
        
        user_tenant_id = str(user.tenant.id)
        
        # Check tenant_id in URL parameters
        url_tenant_id = kwargs.get('tenant_id') or kwargs.get('pk')
        if url_tenant_id and str(url_tenant_id) != user_tenant_id:
            logger.warning(
                f"User {user.email} (tenant {user_tenant_id}) attempted to access "
                f"tenant {url_tenant_id} via URL parameter"
            )
            return JsonResponse({'error': 'Forbidden: Cannot access other tenant data'}, status=403)
        
        return view_func(request, *args, **kwargs)
    
    return wrapper


class TenantQuerySetMixin:
    """
    Mixin to automatically filter querysets by tenant for ViewSets
    """
    
    def get_queryset(self):
        """
        Override get_queryset to automatically filter by tenant
        """
        queryset = super().get_queryset()
        
        if hasattr(self.request, 'user') and self.request.user.is_authenticated:
            user = self.request.user
            if not hasattr(user, 'tenant') or not user.tenant:
                # Allow superusers to see all data
                if user.is_superuser:
                    logger.info(f"Superuser {user.email} accessing all data without tenant filtering")
                    return queryset
                
                # User has no tenant - this should have been caught by middleware
                # but if we get here, we need to raise an error
                from django.http import JsonResponse
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied('User is not associated with any tenant')
                
            # Filter queryset by tenant if the model has tenant field
            model = queryset.model
            if hasattr(model, 'tenant'):
                return queryset.filter(tenant=user.tenant)
            elif 'tenant_id' in [field.name for field in model._meta.fields]:
                return queryset.filter(tenant_id=user.tenant.id)
        
        # Return empty queryset if no tenant or not authenticated
        return queryset.none()
    
    def perform_create(self, serializer):
        """
        Override perform_create to automatically set tenant
        """
        if hasattr(self.request, 'user') and self.request.user.is_authenticated:
            user = self.request.user
            if hasattr(user, 'tenant') and user.tenant:
                # Set tenant on the instance being created
                model = serializer.Meta.model
                if hasattr(model, 'tenant'):
                    serializer.save(tenant=user.tenant)
                elif 'tenant_id' in [field.name for field in model._meta.fields]:
                    serializer.save(tenant_id=user.tenant.id)
                else:
                    serializer.save()
            else:
                raise PermissionError("User has no associated tenant")
        else:
            raise PermissionError("Authentication required")
    
    def perform_update(self, serializer):
        """
        Override perform_update to ensure tenant consistency
        """
        instance = serializer.instance
        user = self.request.user
        
        # Verify the instance belongs to the user's tenant
        model = instance._meta.model
        if hasattr(instance, 'tenant'):
            if instance.tenant != user.tenant:
                raise PermissionError("Cannot update other tenant's data")
        elif 'tenant_id' in [field.name for field in model._meta.fields]:
            if instance.tenant_id != user.tenant.id:
                raise PermissionError("Cannot update other tenant's data")
        
        serializer.save()
    
    def perform_destroy(self, instance):
        """
        Override perform_destroy to ensure tenant consistency
        """
        user = self.request.user
        
        # Verify the instance belongs to the user's tenant
        model = instance._meta.model
        if hasattr(instance, 'tenant'):
            if instance.tenant != user.tenant:
                raise PermissionError("Cannot delete other tenant's data")
        elif 'tenant_id' in [field.name for field in model._meta.fields]:
            if instance.tenant_id != user.tenant.id:
                raise PermissionError("Cannot delete other tenant's data")
        
        instance.delete()