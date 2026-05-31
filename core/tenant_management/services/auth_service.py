import uuid
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from ..models import EmailVerificationToken, PasswordResetToken, TenantModel, Subscription, PasswordHistory, SecurityEvent
from ..domain.entities import PlanName
import logging

logger = logging.getLogger(__name__)
User = get_user_model()


class AuthService:
    
    @staticmethod
    def initiate_registration(email: str, tenant_name: str, username: str, password: str) -> EmailVerificationToken:
        """
        Inicia el proceso de registro creando un token de verificación de email
        en lugar de crear el usuario directamente.
        """
        # Verificar si el email ya existe
        if User.objects.filter(email=email).exists():
            raise ValueError("Email already registered")
        
        # Verificar si ya existe un token válido para este email
        existing_token = EmailVerificationToken.objects.filter(
            email=email, 
            is_used=False
        ).first()
        
        if existing_token and existing_token.is_valid():
            # Reutilizar token existente
            return existing_token
        
        # Crear nuevo token
        verification_token = EmailVerificationToken.objects.create(
            email=email,
            tenant_name=tenant_name,
            user_data={
                'username': username,
                'password': password,  # En producción, esto debería estar hasheado
            }
        )
        
        # Enviar email con magic URL
        AuthService._send_verification_email(verification_token)
        
        return verification_token
    
    @staticmethod
    def verify_email_and_complete_registration(token: str) -> User:
        """
        Verifica el token de email y completa el registro del usuario.
        """
        try:
            verification_token = EmailVerificationToken.objects.get(
                token=token,
                is_used=False
            )
        except EmailVerificationToken.DoesNotExist:
            raise ValueError("Invalid or expired verification token")
        
        if not verification_token.is_valid():
            raise ValueError("Token has expired")
        
        # Crear el tenant y usuario
        from ..application.services import TenantApplicationService
        from ..application.commands import RegisterTenantCommand
        from ..infrastructure.repositories import DjangoTenantRepository
        
        tenant_service = TenantApplicationService(DjangoTenantRepository())
        
        # Registrar el tenant con plan FREE por defecto
        from ..domain.value_objects import SubscriptionPlan
        register_command = RegisterTenantCommand(
            name=verification_token.tenant_name,
            plan=SubscriptionPlan.FREE
        )
        tenant_dto = tenant_service.register_tenant(register_command)
        
        # Obtener el tenant creado
        tenant = TenantModel.objects.get(id=tenant_dto.tenant_id)
        
        # Crear el usuario
        user = User.objects.create_user(
            username=verification_token.user_data['username'],
            email=verification_token.email,
            password=verification_token.user_data['password'],
            tenant=tenant,
            email_verified=True  # Marcar como verificado
        )
        
        # Marcar token como usado
        verification_token.is_used = True
        verification_token.save()
        
        logger.info(f"User registered and verified: {user.email} for tenant {tenant.name}")
        
        return user
    
    @staticmethod
    def initiate_password_reset(email: str) -> PasswordResetToken:
        """
        Inicia el proceso de recuperación de contraseña.
        """
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise ValueError("No user found with this email address")
        
        # Invalidar tokens existentes
        PasswordResetToken.objects.filter(
            user=user,
            is_used=False
        ).update(is_used=True)
        
        # Crear nuevo token
        reset_token = PasswordResetToken.objects.create(user=user)
        
        # Enviar email
        AuthService._send_password_reset_email(reset_token)
        
        return reset_token
    
    @staticmethod
    def reset_password(token: str, new_password: str) -> User:
        """
        Resetea la contraseña usando el token.
        """
        try:
            reset_token = PasswordResetToken.objects.get(
                token=token,
                is_used=False
            )
        except PasswordResetToken.DoesNotExist:
            raise ValueError("Invalid or expired reset token")
        
        if not reset_token.is_valid():
            raise ValueError("Token has expired")
        
        user = reset_token.user
        
        # Save current password to history before changing
        AuthService._save_password_to_history(user, user.password)
        
        # Cambiar contraseña
        user.set_password(new_password)
        user.save()
        
        # Marcar token como usado
        reset_token.is_used = True
        reset_token.save()
        
        # Invalidate all user sessions for security
        from .jwt_service import CustomJWTService
        CustomJWTService.logout_user(user)
        
        # Log security event
        SecurityEvent.objects.create(
            user=user,
            event_type='password_reset',
            description=f'Password reset completed for user {user.email}',
            severity='medium'
        )
        
        logger.info(f"Password reset for user: {user.email}")
        
        return user
    
    @staticmethod
    def change_password(user: User, old_password: str, new_password: str) -> bool:
        """
        Change user password with validation
        """
        # Verify old password
        if not user.check_password(old_password):
            SecurityEvent.objects.create(
                user=user,
                event_type='password_change_failed',
                description=f'Failed password change attempt - incorrect old password',
                severity='medium'
            )
            raise ValueError("Current password is incorrect")
        
        # Validate new password using Django validators
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        
        try:
            validate_password(new_password, user)
        except ValidationError as e:
            raise ValueError('; '.join(e.messages))
        
        # Save current password to history
        AuthService._save_password_to_history(user, user.password)
        
        # Set new password
        user.set_password(new_password)
        user.save()
        
        # Log security event
        SecurityEvent.objects.create(
            user=user,
            event_type='password_changed',
            description=f'Password changed successfully for user {user.email}',
            severity='low'
        )
        
        logger.info(f"Password changed for user: {user.email}")
        return True
    
    @staticmethod
    def _save_password_to_history(user: User, password_hash: str):
        """
        Save password to history and maintain only last 5 passwords
        """
        PasswordHistory.objects.create(
            user=user,
            password_hash=password_hash
        )
        
        # Keep only last 5 passwords
        history_to_delete = PasswordHistory.objects.filter(
            user=user
        ).order_by('-created_at')[5:]
        
        for history in history_to_delete:
            history.delete()
    
    @staticmethod
    def _send_verification_email(verification_token: EmailVerificationToken):
        """
        Envía el email de verificación con magic URL.
        """
        magic_url = f"{settings.FRONTEND_URL}/verify-email?token={verification_token.token}"

        subject = f"Verifica tu correo - Bienvenido a {verification_token.tenant_name}"

        html_message = f"""
        <h2>¡Bienvenido a {verification_token.tenant_name}!</h2>
        <p>Gracias por registrarte. Por favor haz clic en el enlace a continuación para verificar tu dirección de correo electrónico y completar tu registro:</p>
        <p><a href="{magic_url}" style="background-color: #007cba; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Verificar Correo Electrónico</a></p>
        <p>O copia y pega esta URL en tu navegador:</p>
        <p>{magic_url}</p>
        <p>Este enlace expirará en 24 horas.</p>
        <p>Si no solicitaste este registro, por favor ignora este correo.</p>
        """

        plain_message = f"""
        ¡Bienvenido a {verification_token.tenant_name}!

        Gracias por registrarte. Por favor visita la siguiente URL para verificar tu dirección de correo electrónico y completar tu registro:

        {magic_url}

        Este enlace expirará en 24 horas.

        Si no solicitaste este registro, por favor ignora este correo.
        """
        
        try:
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[verification_token.email],
                html_message=html_message,
                fail_silently=False,
            )
            logger.info(f"Verification email sent to {verification_token.email}")
        except Exception as e:
            logger.error(f"Failed to send verification email to {verification_token.email}: {e}")
            raise
    
    @staticmethod
    def _send_password_reset_email(reset_token: PasswordResetToken):
        """
        Envía el email de recuperación de contraseña.
        """
        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={reset_token.token}"

        subject = "Restablecer tu contraseña"

        html_message = f"""
        <h2>Solicitud de Restablecimiento de Contraseña</h2>
        <p>Solicitaste restablecer tu contraseña. Haz clic en el enlace a continuación para establecer una nueva contraseña:</p>
        <p><a href="{reset_url}" style="background-color: #007cba; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Restablecer Contraseña</a></p>
        <p>O copia y pega esta URL en tu navegador:</p>
        <p>{reset_url}</p>
        <p>Este enlace expirará en 1 hora.</p>
        <p>Si no solicitaste este restablecimiento, por favor ignora este correo.</p>
        """

        plain_message = f"""
        Solicitud de Restablecimiento de Contraseña

        Solicitaste restablecer tu contraseña. Por favor visita la siguiente URL para establecer una nueva contraseña:

        {reset_url}

        Este enlace expirará en 1 hora.

        Si no solicitaste este restablecimiento, por favor ignora este correo.
        """
        
        try:
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[reset_token.user.email],
                html_message=html_message,
                fail_silently=False,
            )
            logger.info(f"Password reset email sent to {reset_token.user.email}")
        except Exception as e:
            logger.error(f"Failed to send password reset email to {reset_token.user.email}: {e}")
            raise