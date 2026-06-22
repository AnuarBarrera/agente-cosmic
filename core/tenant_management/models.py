# Copyright 2024 DIALOGIX
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from django.contrib.auth.models import AbstractUser, UserManager as BaseUserManager
from django.db import models
import uuid
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
import secrets

def generate_verification_token():
    """Generate a secure token for email verification"""
    return secrets.token_urlsafe(32)

def generate_reset_token():
    """Generate a secure token for password reset"""
    return secrets.token_urlsafe(32)

class Plan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    max_daily_interactions = models.PositiveIntegerField(default=100)
    max_monthly_interactions = models.PositiveIntegerField(default=1000)
    max_calendars_per_week = models.PositiveIntegerField(default=2)
    max_post_regenerations = models.PositiveIntegerField(default=2)
    max_post_edits = models.PositiveIntegerField(default=2)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'plans'
        verbose_name = 'Plan'
        verbose_name_plural = 'Plans'


class TenantModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    # plan = models.CharField(max_length=50) # Reemplazado por el modelo Subscription
    status = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tenants'
        verbose_name = 'Tenant'
        verbose_name_plural = 'Tenants'

class Subscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(TenantModel, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    start_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=50, default='active') # e.g., active, canceled, past_due

    def __str__(self):
        return f"{self.tenant.name} - {self.plan.name}"

    class Meta:
        db_table = 'subscriptions'
        verbose_name = 'Subscription'
        verbose_name_plural = 'Subscriptions'

class UsageRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TenantModel, on_delete=models.CASCADE, related_name='usage_records')
    timestamp = models.DateTimeField(auto_now_add=True)
    interaction_count = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.tenant.name} - {self.timestamp.strftime('%Y-%m-%d %H:%M')} - {self.interaction_count}"

    class Meta:
        db_table = 'usage_records'
        verbose_name = 'Usage Record'
        verbose_name_plural = 'Usage Records'
        ordering = ['-timestamp']

class TenantConfigurationModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(TenantModel, on_delete=models.CASCADE, related_name='configuration')
    ai_settings = models.JSONField(default=dict)
    channel_settings = models.JSONField(default=dict)
    routing_rules = models.JSONField(default=list)

    class Meta:
        db_table = 'tenant_configurations'
        verbose_name = 'Tenant Configuration'
        verbose_name_plural = 'Tenant Configurations'

from django.utils.translation import gettext_lazy as _

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        
        # Para superusuarios, usar email como username para evitar conflictos
        if 'username' not in extra_fields:
            extra_fields['username'] = email
            
        return self.create_user(email, password, **extra_fields)

class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TenantModel, on_delete=models.CASCADE, null=True, blank=True)
    email = models.EmailField(_('email address'), unique=True)
    display_name = models.CharField(max_length=255, blank=True, null=True) # New field
    email_verified = models.BooleanField(default=False)  # Nuevo campo para verificación de email

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = [] # No other fields are required for authentication
    
    objects = UserManager()


class EmailVerificationToken(models.Model):
    """Token para verificación de email durante el registro"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    token = models.CharField(max_length=64, unique=True, default=generate_verification_token)
    tenant_name = models.CharField(max_length=255)
    user_data = models.JSONField(default=dict)  # Almacena datos temporales del usuario
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    
    def save(self, *args, **kwargs):
        if not self.expires_at:
            # Token válido por 24 horas
            self.expires_at = timezone.now() + timezone.timedelta(hours=24)
        super().save(*args, **kwargs)
    
    def is_expired(self):
        return timezone.now() > self.expires_at
    
    def is_valid(self):
        return not self.is_used and not self.is_expired()
    
    class Meta:
        db_table = 'email_verification_tokens'
        verbose_name = 'Email Verification Token'
        verbose_name_plural = 'Email Verification Tokens'


class PasswordResetToken(models.Model):
    """Token para recuperación de contraseña"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True, default=generate_reset_token)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    
    def save(self, *args, **kwargs):
        if not self.expires_at:
            # Token válido por 1 hora
            self.expires_at = timezone.now() + timezone.timedelta(hours=1)
        super().save(*args, **kwargs)
    
    def is_expired(self):
        return timezone.now() > self.expires_at
    
    def is_valid(self):
        return not self.is_used and not self.is_expired()
    
    class Meta:
        db_table = 'password_reset_tokens'
        verbose_name = 'Password Reset Token'
        verbose_name_plural = 'Password Reset Tokens'


class BlacklistedToken(models.Model):
    """JWT tokens that have been blacklisted/invalidated"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token_jti = models.CharField(max_length=255, unique=True)  # JWT ID claim
    blacklisted_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=100, choices=[
        ('logout', 'User logout'),
        ('password_reset', 'Password reset'),
        ('security_incident', 'Security incident'),
        ('token_rotation', 'Token rotation'),
        ('admin_action', 'Admin action'),
    ], default='logout')
    
    class Meta:
        db_table = 'blacklisted_tokens'
        verbose_name = 'Blacklisted Token'
        verbose_name_plural = 'Blacklisted Tokens'
        indexes = [
            models.Index(fields=['token_jti']),
        ]


class LoginAttempt(models.Model):
    """Track login attempts for security monitoring and account lockout"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True)
    success = models.BooleanField(default=False)
    failure_reason = models.CharField(max_length=100, blank=True)
    attempt_time = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'login_attempts'
        verbose_name = 'Login Attempt'
        verbose_name_plural = 'Login Attempts'
        indexes = [
            models.Index(fields=['email', 'attempt_time']),
            models.Index(fields=['ip_address', 'attempt_time']),
        ]


class UserSession(models.Model):
    """Track active user sessions for concurrent session management"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    session_token = models.CharField(max_length=255, unique=True)  # JWT JTI
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'user_sessions'
        verbose_name = 'User Session'
        verbose_name_plural = 'User Sessions'
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['session_token']),
        ]


class PasswordHistory(models.Model):
    """Track password history to prevent reuse"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_history')
    password_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'password_history'
        verbose_name = 'Password History'
        verbose_name_plural = 'Password Histories'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
        ]


class SecurityEvent(models.Model):
    """Track security events for monitoring and alerting"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    event_type = models.CharField(max_length=50, choices=[
        ('login_success', 'Successful Login'),
        ('login_failed', 'Failed Login'),
        ('password_reset', 'Password Reset'),
        ('account_locked', 'Account Locked'),
        ('suspicious_activity', 'Suspicious Activity'),
        ('token_blacklisted', 'Token Blacklisted'),
        ('session_expired', 'Session Expired'),
        ('permission_denied', 'Permission Denied'),
    ])
    description = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    additional_data = models.JSONField(default=dict, blank=True)
    severity = models.CharField(max_length=20, choices=[
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ], default='medium')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'security_events'
        verbose_name = 'Security Event'
        verbose_name_plural = 'Security Events'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['event_type', 'created_at']),
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['severity', 'created_at']),
        ]


_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'


class InvitationCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=13, unique=True)
    target_group = models.CharField(max_length=20, default='tester')
    max_uses = models.PositiveIntegerField(default=1)
    times_used = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_codes')
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'invitation_codes'
        verbose_name = 'Invitation Code'
        verbose_name_plural = 'Invitation Codes'

    def __str__(self):
        return f"{self.code} ({self.target_group})"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.generate_code()
        super().save(*args, **kwargs)

    @staticmethod
    def generate_code() -> str:
        suffix = ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        return f'COSMIC-{suffix}'

    def is_valid(self) -> bool:
        if not self.is_active:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        if self.max_uses > 0 and self.times_used >= self.max_uses:
            return False
        return True

    def redeem(self, user) -> bool:
        if not self.is_valid():
            return False
        from django.contrib.auth.models import Group
        target, _ = Group.objects.get_or_create(name=self.target_group)
        user.groups.clear()
        user.groups.add(target)
        self.times_used += 1
        self.save(update_fields=['times_used'])
        return True