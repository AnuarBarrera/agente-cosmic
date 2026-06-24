from django.http import JsonResponse
from django.contrib.auth import get_user_model
import logging

User = get_user_model()
logger = logging.getLogger(__name__)


class TenantIsolationMiddleware:
    PUBLIC_PATH_PREFIXES = (
        '/auth/', '/health/', '/admin/', '/static/', '/media/',
        '/metrics', '/favicon',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._is_public_path(request.path):
            return self.get_response(request)

        if hasattr(request, 'user') and request.user.is_authenticated:
            tenant_response = self._enforce_tenant_isolation(request)
            if tenant_response:
                return tenant_response

        return self.get_response(request)

    def _is_public_path(self, path):
        if path == '/':
            return True
        for prefix in self.PUBLIC_PATH_PREFIXES:
            if path.startswith(prefix):
                return True
        return False

    def _enforce_tenant_isolation(self, request):
        user = request.user

        if not hasattr(user, 'tenant') or not user.tenant:
            if user.is_superuser:
                return None
            return JsonResponse({
                'error': 'Access forbidden',
                'message': 'User is not associated with any tenant',
            }, status=403)

        request.tenant_id = str(user.tenant.id)
        return None


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