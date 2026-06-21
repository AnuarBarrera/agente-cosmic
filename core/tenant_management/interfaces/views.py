import uuid
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.request import Request
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from ..application.commands import (
    RegisterTenantCommand,
    UpdateAIConfigurationCommand,
)
from ..application.services import TenantApplicationService
from ..services.auth_service import AuthService
from ..services.jwt_service import CustomJWTService
from ..domain.value_objects import SubscriptionPlan
from ..infrastructure.repositories import DjangoTenantRepository, DjangoSubscriptionRepository, DjangoPlanRepository
from .serializers import (
    TenantRegistrationSerializer,
    TenantSerializer,
    TenantAIConfigurationSerializer,
    UserRegistrationSerializer,
    MyTokenObtainPairSerializer,
    ChangePlanSerializer,
    PlanSerializer,
    SubscriptionSerializer,
)
from ..models import User, Plan, Subscription


class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer


class UserRegistrationView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request):
        serializer = UserRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        try:
            # Iniciar proceso de registro con verificación de email
            verification_token = AuthService.initiate_registration(
                email=validated_data['email'],
                tenant_name=validated_data['name'],
                username=validated_data['username'],
                password=validated_data['password']
            )

            return Response({
                'message': 'Registration initiated. Please check your email for verification link.',
                'email': validated_data['email']
            }, status=status.HTTP_201_CREATED)

        except ValueError as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)


class TenantViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    # In a real DI container, this would be injected.
    repo = DjangoTenantRepository()
    service = TenantApplicationService(repo)

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action == 'create':
            return [AllowAny()]
        return super().get_permissions()

    def create(self, request: Request) -> Response:
        serializer = TenantRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        command = RegisterTenantCommand(
            name=validated_data["name"],
            plan=SubscriptionPlan(validated_data["plan"]),
        )

        try:
            tenant_dto = self.service.register_tenant(command)
            response_serializer = TenantSerializer(tenant_dto)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request: Request, pk=None) -> Response:
        # Only allow users to retrieve their own tenant
        if request.user.tenant_id != uuid.UUID(pk):
            return Response(status=status.HTTP_403_FORBIDDEN)

        tenant_dto = self.service.get_tenant(pk)
        if tenant_dto:
            serializer = TenantSerializer(tenant_dto)
            return Response(serializer.data)
        return Response(status=status.HTTP_404_NOT_FOUND)

    def list(self, request: Request) -> Response:
        # Only list the user's own tenant
        tenant_dto = self.service.get_tenant(request.user.tenant_id)
        if tenant_dto:
            serializer = TenantSerializer([tenant_dto], many=True)
            return Response(serializer.data)
        return Response([])

    def destroy(self, request: Request, pk=None) -> Response:
        if request.user.tenant_id != pk:
            return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            self.service.suspend_tenant(pk)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['patch'], url_path='update-ai-config')
    def update_ai_config(self, request: Request, pk=None) -> Response:
        if str(request.user.tenant_id) != pk:
            return Response(status=status.HTTP_403_FORBIDDEN)

        serializer = TenantAIConfigurationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        validated_data = serializer.validated_data

        command = UpdateAIConfigurationCommand(
            tenant_id=pk,
            ai_settings=validated_data,
        )

        try:
            self.service.update_ai_configuration(command)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request: Request, pk=None) -> Response:
        # This method is now handled by update_ai_config
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=['post'], url_path='change-plan')
    def change_plan(self, request: Request, pk=None) -> Response:
        if request.user.tenant_id != uuid.UUID(pk):
            return Response(status=status.HTTP_403_FORBIDDEN)

        serializer = ChangePlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_plan_name = serializer.validated_data['plan_name']

        try:
            self.service.change_tenant_plan(uuid.UUID(pk), new_plan_name)
            return Response({"status": "plan changed successfully"}, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


    @action(detail=False, methods=['get'], url_path='my-tenant')
    def my_tenant(self, request: Request) -> Response:
        tenant_id = request.user.tenant_id
        if not tenant_id:
            return Response({"error": "Tenant ID not found for user."}, status=status.HTTP_404_NOT_FOUND)

        tenant_dto = self.service.get_tenant(tenant_id)
        if tenant_dto:
            serializer = TenantSerializer(tenant_dto)
            return Response(serializer.data)
        return Response(status=status.HTTP_404_NOT_FOUND)


class PlanViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Plan.objects.all()
    serializer_class = PlanSerializer
    permission_classes = [IsAuthenticated]


class SubscriptionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        This view should return a list of all the subscriptions
        for the currently authenticated user's tenant.
        """
        tenant_id = self.request.user.tenant_id
        return Subscription.objects.filter(tenant_id=tenant_id)

    @action(detail=False, methods=['get'], url_path='my-subscription')
    def my_subscription(self, request: Request) -> Response:
        tenant_id = request.user.tenant_id
        if not tenant_id:
            return Response({"error": "Tenant ID not found for user."}, status=status.HTTP_404_NOT_FOUND)

        # 1. Get subscription from its repository
        subscription_repo = DjangoSubscriptionRepository()
        subscription = subscription_repo.get_by_tenant_id(tenant_id)
        if not subscription:
            return Response({"error": "Subscription not found for tenant."}, status=status.HTTP_404_NOT_FOUND)

        # 2. Get usage and dashboard data from the application service
        service = TenantApplicationService(DjangoTenantRepository())
        usage_dto = service.get_tenant_usage(tenant_id)
        dashboard_data_dto = service.get_dashboard_data(tenant_id)
        
        # 3. Combine them for the serializer
        subscription.usage = usage_dto
        subscription.dashboard_data = dashboard_data_dto

        serializer = SubscriptionSerializer(subscription)
        return Response(serializer.data)


class EmailVerificationView(APIView):
    """Vista para verificar email y completar registro"""
    permission_classes = [AllowAny]

    def post(self, request: Request):
        token = request.data.get('token')
        if not token:
            return Response({
                'error': 'Token is required'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Verificar email y completar registro
            user = AuthService.verify_email_and_complete_registration(token)
            
            # Generar tokens JWT para el nuevo usuario
            refresh = RefreshToken.for_user(user)
            refresh['tenant_id'] = str(user.tenant.id)
            refresh['name'] = user.username

            return Response({
                'message': 'Email verified and registration completed successfully',
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user': {
                    'email': user.email,
                    'tenant_id': str(user.tenant.id),
                    'tenant_name': user.tenant.name
                }
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetRequestView(APIView):
    """Vista para solicitar recuperación de contraseña"""
    permission_classes = [AllowAny]

    def post(self, request: Request):
        email = request.data.get('email')
        if not email:
            return Response({
                'error': 'Email is required'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            AuthService.initiate_password_reset(email)
            return Response({
                'message': 'Password reset link sent to your email',
                'email': email
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetConfirmView(APIView):
    """Vista para confirmar reset de contraseña"""
    permission_classes = [AllowAny]

    def post(self, request: Request):
        token = request.data.get('token')
        new_password = request.data.get('new_password')

        if not token or not new_password:
            return Response({
                'error': 'Token and new_password are required'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = AuthService.reset_password(token, new_password)
            
            # Invalidate all existing sessions after password reset
            CustomJWTService.logout_user(user)
            
            return Response({
                'message': 'Password reset successfully',
                'email': user.email
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)


class LogoutView(APIView):
    """Vista para logout que invalida tokens JWT"""
    permission_classes = [IsAuthenticated]

    def post(self, request: Request):
        try:
            # Get JWT token from request headers
            auth_header = request.META.get('HTTP_AUTHORIZATION', '')
            if not auth_header.startswith('Bearer '):
                return Response({
                    'error': 'No valid token provided'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            raw_token = auth_header.split(' ')[1]
            
            try:
                from rest_framework_simplejwt.tokens import UntypedToken
                token = UntypedToken(raw_token)
                jti = token.get('jti')
                
                if jti:
                    CustomJWTService.logout_user(request.user, jti)
                else:
                    CustomJWTService.logout_user(request.user)
                
                return Response({
                    'message': 'Successfully logged out'
                }, status=status.HTTP_200_OK)
                
            except TokenError:
                return Response({
                    'error': 'Invalid token'
                }, status=status.HTTP_400_BAD_REQUEST)
            
        except Exception as e:
            return Response({
                'error': 'Logout failed'
            }, status=status.HTTP_400_BAD_REQUEST)


class TokenRefreshView(APIView):
    """Vista para refresh de tokens con rotación"""
    permission_classes = [AllowAny]

    def post(self, request: Request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response({
                'error': 'Refresh token is required'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            ip_address = self.get_client_ip(request)
            tokens = CustomJWTService.rotate_tokens(refresh_token, ip_address)
            
            return Response({
                'access': tokens['access'],
                'refresh': tokens['refresh'],
                'session_id': tokens['session_id']
            }, status=status.HTTP_200_OK)
            
        except TokenError as e:
            return Response({
                'error': 'Invalid refresh token'
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({
                'error': 'Token refresh failed'
            }, status=status.HTTP_400_BAD_REQUEST)
    
    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[-1].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


class UserSessionsView(APIView):
    """Vista para gestionar sesiones de usuario"""
    permission_classes = [IsAuthenticated]

    def get(self, request: Request):
        """Obtener sesiones activas del usuario"""
        sessions = CustomJWTService.get_user_sessions(request.user)
        session_data = []
        
        for session in sessions:
            session_data.append({
                'session_id': str(session.id),
                'ip_address': session.ip_address,
                'user_agent': session.user_agent[:100],  # Truncate for display
                'created_at': session.created_at.isoformat(),
                'last_activity': session.last_activity.isoformat(),
            })
        
        return Response({
            'sessions': session_data,
            'total': len(session_data)
        }, status=status.HTTP_200_OK)

    def delete(self, request: Request):
        """Cerrar sesión específica por session_id"""
        session_id = request.data.get('session_id')
        if not session_id:
            return Response({
                'error': 'session_id is required'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            from ..models import UserSession
            session = UserSession.objects.get(
                id=session_id,
                user=request.user,
                is_active=True
            )
            
            CustomJWTService.logout_user(request.user, session.session_token)
            
            return Response({
                'message': 'Session terminated successfully'
            }, status=status.HTTP_200_OK)
            
        except UserSession.DoesNotExist:
            return Response({
                'error': 'Session not found'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({
                'error': 'Failed to terminate session'
            }, status=status.HTTP_400_BAD_REQUEST)


class ChangePasswordView(APIView):
    """Vista para cambio de contraseña del usuario autenticado"""
    permission_classes = [IsAuthenticated]

    def post(self, request: Request):
        old_password = request.data.get('old_password')
        new_password = request.data.get('new_password')

        if not old_password or not new_password:
            return Response({
                'error': 'old_password and new_password are required'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            AuthService.change_password(request.user, old_password, new_password)
            
            # Invalidate all existing sessions after password change
            CustomJWTService.logout_user(request.user)
            
            return Response({
                'message': 'Password changed successfully. Please log in again.'
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({
                'error': 'Failed to change password'
            }, status=status.HTTP_400_BAD_REQUEST)