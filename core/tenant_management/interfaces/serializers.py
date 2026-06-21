from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from ..domain.entities import PlanName, Tenant
from ..domain.value_objects import SubscriptionPlan
from ..application.dtos import TenantDTO, TenantConfigurationDTO, UsageRecordDTO
from ..models import User, Plan, Subscription
from ..services.jwt_service import CustomJWTService


from django.contrib.auth import authenticate

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = 'email'

    def validate(self, attrs):
        email = attrs[self.username_field]
        password = attrs['password']
        
        # Get client info from context
        request = self.context.get('request')
        ip_address = self.get_client_ip(request) if request else '127.0.0.1'
        user_agent = request.META.get('HTTP_USER_AGENT', '') if request else ''
        
        # Check for account lockout
        lockout_info = CustomJWTService.check_account_lockout(email, ip_address)
        if lockout_info['is_locked']:
            CustomJWTService.record_login_attempt(
                email=email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                failure_reason='account_locked'
            )
            raise serializers.ValidationError('Account temporarily locked due to multiple failed login attempts. Please try again later.')
        
        # Authenticate user
        authenticate_kwargs = {
            self.username_field: email,
            'password': password,
        }
        user = authenticate(**authenticate_kwargs)

        if not user:
            # Record failed login attempt
            CustomJWTService.record_login_attempt(
                email=email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                failure_reason='invalid_credentials'
            )
            
            # Create security event for failed login
            from ..models import SecurityEvent
            SecurityEvent.objects.create(
                user=None,  # User not authenticated yet
                event_type='login_failed',
                description=f'Failed login attempt for email: {email}',
                ip_address=ip_address,
                user_agent=user_agent
            )
            
            raise serializers.ValidationError('No active account found with the given credentials')

        # Verificar que el usuario tenga un tenant asociado
        if not hasattr(user, 'tenant') or not user.tenant:
            CustomJWTService.record_login_attempt(
                email=email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                failure_reason='no_tenant'
            )
            raise serializers.ValidationError('User is not associated with a tenant')

        # Create tokens with enhanced security
        tokens = CustomJWTService.create_tokens_for_user(user, ip_address, user_agent)
        
        # Record successful login
        CustomJWTService.record_login_attempt(
            email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True
        )
        
        # Create security event for successful login
        from ..models import SecurityEvent
        SecurityEvent.objects.create(
            user=user,
            event_type='login_success',
            description=f'Successful login for user: {user.email}',
            ip_address=ip_address,
            user_agent=user_agent,
            severity='low'  # Successful logins are low severity
        )
        
        return {
            'refresh': tokens['refresh'],
            'access': tokens['access'],
            'name': user.username,
            'tenant_id': str(user.tenant.id),
            'session_id': tokens['session_id']
        }
    
    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[-1].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Add custom claims
        token['name'] = user.username
        token['tenant_id'] = str(user.tenant.id) if user.tenant else None

        return token


class UserRegistrationSerializer(serializers.ModelSerializer):
    name = serializers.CharField(write_only=True) # For tenant name

    class Meta:
        model = User
        fields = ('name', 'username', 'email', 'password') # Added 'username'
        extra_kwargs = {'password': {'write_only': True}}


class TenantRegistrationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    plan = serializers.ChoiceField(choices=[plan.value for plan in SubscriptionPlan])


class TenantSerializer(serializers.Serializer):
    tenant_id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(max_length=255)
    plan = serializers.CharField(max_length=50)
    status = serializers.CharField(max_length=50)
    configuration = serializers.JSONField(read_only=True)

    def to_representation(self, instance: TenantDTO):
        return {
            "tenant_id": str(instance.tenant_id),
            "name": instance.name,
            "plan": instance.plan,
            "status": instance.status,
            "configuration": instance.configuration,
        }

    def to_internal_value(self, data):
        # This serializer is for output, so we don't need to implement this
        raise NotImplementedError


class TenantAIConfigurationSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=["openai", "gemini"], required=True)
    api_key = serializers.CharField(max_length=255, required=False, allow_blank=True)
    model = serializers.CharField(max_length=100, required=True)

    def to_representation(self, instance):
        # This serializer is for input, so we don't need to implement this
        raise NotImplementedError

    def to_internal_value(self, data):
        # Basic validation is handled by field definitions.
        # For more complex validation, you can override this method.
        return super().to_internal_value(data)

class ChangePlanSerializer(serializers.Serializer):
    plan_name = serializers.ChoiceField(choices=[name.value for name in PlanName])


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ['id', 'name', 'max_daily_interactions', 'max_monthly_interactions', 'price']


class UsageRecordSerializer(serializers.Serializer):
    total_daily = serializers.IntegerField()
    total_monthly = serializers.IntegerField()

    def to_representation(self, instance: UsageRecordDTO):
        return {
            "total_daily": instance.total_daily,
            "total_monthly": instance.total_monthly,
        }


class DashboardDataSerializer(serializers.Serializer):
    total_conversations = serializers.IntegerField()
    resolved_cases = serializers.IntegerField()
    avg_response_time = serializers.CharField()
    automation_rate = serializers.FloatField()
    response_rate = serializers.CharField()
    conversations_by_channel = serializers.DictField()
    recent_activity = serializers.ListField()

    def to_representation(self, instance: "DashboardDataDTO"):
        return {
            "total_conversations": instance.total_conversations,
            "resolved_cases": instance.resolved_cases,
            "avg_response_time": instance.avg_response_time,
            "automation_rate": instance.automation_rate,
            "response_rate": instance.response_rate,
            "conversations_by_channel": instance.conversations_by_channel,
            "recent_activity": instance.recent_activity,
        }


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = PlanSerializer(read_only=True)
    usage = UsageRecordSerializer(read_only=True)
    dashboard_data = DashboardDataSerializer(read_only=True)

    class Meta:
        model = Subscription
        fields = ['id', 'plan', 'start_date', 'end_date', 'status', 'usage', 'dashboard_data']
