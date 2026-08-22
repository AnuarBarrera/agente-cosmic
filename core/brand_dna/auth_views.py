import base64
import hashlib
import secrets
import logging
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import send_mail
from django.shortcuts import render, redirect
from django.utils.html import escape
from .auth_forms import RegisterForm, LoginForm
from .models import AnalysisJob
from core.shared.metrics import (
    LOGIN_ATTEMPTS, REGISTRATIONS, EMAIL_VERIFICATIONS,
    INVITATION_CODES_REDEEMED, EMAILS_SENT,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def provision_tenant(user):
    from core.tenant_management.models import TenantModel, Plan, Subscription

    if user.tenant is not None:
        return user.tenant

    tenant = TenantModel.objects.create(name=user.email, status='active')
    free_plan = Plan.objects.filter(name='User').first()
    if free_plan:
        Subscription.objects.create(tenant=tenant, plan=free_plan)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    return tenant


_EMAIL_ACTION_MAX = 3
_EMAIL_ACTION_WINDOW = 900


def _get_client_ip(request) -> str:
    # X-Real-IP es inyectado por Nginx con $remote_addr — no puede ser falsificado por el cliente
    real_ip = request.META.get('HTTP_X_REAL_IP', '').strip()
    if real_ip:
        return real_ip
    return request.META.get('REMOTE_ADDR', '')


def _check_email_rate_limit(request, action: str) -> bool:
    ip = _get_client_ip(request)
    key = f'{action}_rate:{ip}'
    try:
        attempts = cache.incr(key)
    except ValueError:
        cache.set(key, 1, _EMAIL_ACTION_WINDOW)
        attempts = 1
    if attempts > _EMAIL_ACTION_MAX:
        return False
    return True


def notify_admin_new_user(user, invitation_code=None):
    try:
        admin_email = settings.ADMIN_NOTIFICATION_EMAIL
        group_name = user.groups.first().name if user.groups.exists() else 'user'
        code_info = f'<p><strong>Codigo usado:</strong> {escape(str(invitation_code))}</p>' if invitation_code else ''
        admin_url = f'{settings.COSMIC_BASE_URL}/admin/tenant_management/user/{user.pk}/change/'
        send_mail(
            f'[Agente Cosmic] Nuevo usuario verificado — {user.email}',
            f'Nuevo usuario: {user.email} (rol: {group_name})',
            settings.DEFAULT_FROM_EMAIL,
            [admin_email],
            html_message=(
                f'<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">'
                f'<h2 style="color:#e94560;">Nuevo usuario en Agente Cosmic</h2>'
                f'<p><strong>Email:</strong> {user.email}</p>'
                f'<p><strong>Rol:</strong> {group_name}</p>'
                f'{code_info}'
                f'<p><strong>Fecha:</strong> {user.date_joined.strftime("%Y-%m-%d %H:%M")}</p>'
                f'<a href="{admin_url}" style="display:inline-block;padding:12px 24px;'
                f'background:#e94560;color:#fff;text-decoration:none;border-radius:8px;'
                f'font-weight:600;margin-top:12px;">Ver en Admin</a></div>'
            ),
            fail_silently=True,
        )
    except Exception as e:
        logger.error(f'Admin notification failed: {e}')

_GOOGLE_SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
]

def _pkce_pair():
    """Genera code_verifier y code_challenge (S256) para PKCE."""
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b'=').decode()
    return verifier, challenge


_GOOGLE_CLIENT_CONFIG = lambda: {
    'web': {
        'client_id': settings.GOOGLE_CLIENT_ID,
        'client_secret': settings.GOOGLE_CLIENT_SECRET,
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'redirect_uris': [settings.GOOGLE_OAUTH_REDIRECT_URI],
    }
}


def _is_registration_open():
    from django.conf import settings as s
    limit = getattr(s, 'MAX_REGISTERED_USERS', 30)
    return User.objects.count() < limit


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if not _is_registration_open():
        return render(request, 'brand_dna/auth/register.html', {
            'form': None,
            'registration_closed': True,
        })

    if request.method == 'POST':
        if not _check_email_rate_limit(request, 'register'):
            return render(request, 'brand_dna/auth/register.html', {
                'form': RegisterForm(),
                'error': 'Demasiados intentos. Intenta de nuevo en 15 minutos.',
            })
        form = RegisterForm(request.POST)
        if form.is_valid():
            if not _is_registration_open():
                return render(request, 'brand_dna/auth/register.html', {
                    'form': None,
                    'registration_closed': True,
                })
            email = form.cleaned_data['email']
            password = form.cleaned_data['password1']
            invitation_code = form.cleaned_data.get('invitation_code', '').strip()

            from django.contrib.auth.hashers import make_password
            from core.tenant_management.models import EmailVerificationToken
            token = EmailVerificationToken.objects.create(
                email=email,
                tenant_name='',
                user_data={
                    'password': make_password(password),
                    'invitation_code': invitation_code,
                },
            )

            verify_url = f"{settings.COSMIC_BASE_URL}/auth/verify/{token.token}/"
            send_mail(
                'Verifica tu correo — Agente Cosmic',
                f'Haz clic en este enlace para verificar tu correo: {verify_url}',
                settings.DEFAULT_FROM_EMAIL,
                [email],
                html_message=(
                    f'<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">'
                    f'<h2 style="color:#e94560;">Agente Cosmic</h2>'
                    f'<p>Haz clic en el boton para verificar tu correo y activar tu cuenta:</p>'
                    f'<a href="{verify_url}" style="display:inline-block;padding:14px 28px;'
                    f'background:#e94560;color:#fff;text-decoration:none;border-radius:8px;'
                    f'font-weight:600;">Verificar mi correo</a>'
                    f'<p style="color:#888;font-size:0.85rem;margin-top:24px;">'
                    f'Este enlace expira en 24 horas.</p></div>'
                ),
                fail_silently=False,
            )

            REGISTRATIONS.labels(method='email').inc()
            EMAILS_SENT.labels(type='verification').inc()

            return render(request, 'brand_dna/auth/verify_pending.html', {'email': email})
    else:
        form = RegisterForm()

    return render(request, 'brand_dna/auth/register.html', {'form': form})


def verify_email_view(request, token):
    from core.tenant_management.models import EmailVerificationToken, InvitationCode
    from django.contrib.auth.models import Group

    try:
        verification = EmailVerificationToken.objects.get(token=token)
    except EmailVerificationToken.DoesNotExist:
        return redirect('login')

    if not verification.is_valid():
        EMAIL_VERIFICATIONS.labels(result='expired').inc()
        return redirect('login')

    email = verification.email
    user_data = verification.user_data

    deactivated_user = User.objects.filter(email=email, is_active=False).first()
    if deactivated_user:
        return render(request, 'brand_dna/auth/reactivate.html', {
            'email': email,
            'token': token,
        })

    user = User.objects.create_user(
        email=email,
        password=None,
        username=email,
    )
    user.password = user_data['password']
    user.email_verified = True
    user.save(update_fields=['password', 'email_verified'])

    provision_tenant(user)

    invitation_code_str = user_data.get('invitation_code', '')
    redeemed = False
    if invitation_code_str:
        try:
            code_obj = InvitationCode.objects.get(code=invitation_code_str)
            redeemed = code_obj.redeem(user)
        except InvitationCode.DoesNotExist:
            pass

    if not redeemed:
        user_group, _ = Group.objects.get_or_create(name='user')
        user.groups.add(user_group)

    verification.is_used = True
    verification.save(update_fields=['is_used'])
    EMAIL_VERIFICATIONS.labels(result='completed').inc()

    notify_admin_new_user(user, invitation_code=invitation_code_str or None)

    return redirect('login')


_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 300


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email'].lower().strip()
            ip = _get_client_ip(request)
            cache_key = f'login_attempts:{ip}:{email}'
            try:
                attempts = cache.incr(cache_key)
            except ValueError:
                cache.set(cache_key, 1, _LOGIN_LOCKOUT_SECONDS)
                attempts = 1
            if attempts > _LOGIN_MAX_ATTEMPTS:
                LOGIN_ATTEMPTS.labels(result='locked').inc()
                error = 'Demasiados intentos. Intenta de nuevo en 5 minutos.'
            else:
                password = form.cleaned_data['password']
                user = authenticate(request, email=email, password=password)
                if user is not None:
                    cache.delete(cache_key)
                    LOGIN_ATTEMPTS.labels(result='success').inc()
                    login(request, user)
                    next_url = request.GET.get('next', 'dashboard')
                    return redirect(next_url)
                LOGIN_ATTEMPTS.labels(result='failed').inc()
                deactivated = User.objects.filter(email=email, is_active=False).first()
                if deactivated:
                    error = 'Tu cuenta está desactivada. Regístrate de nuevo con este correo para reactivarla.'
                else:
                    error = 'Correo o contraseña incorrectos.'
    else:
        form = LoginForm()

    return render(request, 'brand_dna/auth/login.html', {'form': form, 'error': error})


def logout_view(request):
    logout(request)
    return redirect('login')


def forgot_password_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    sent = False
    error = None
    if request.method == 'POST':
        if not _check_email_rate_limit(request, 'forgot_password'):
            sent = True
            return render(request, 'brand_dna/auth/forgot_password.html', {
                'sent': sent, 'error': error,
            })
        email = request.POST.get('email', '').lower().strip()
        if email:
            from core.tenant_management.services.auth_service import AuthService
            try:
                AuthService.initiate_password_reset(email)
                EMAILS_SENT.labels(type='password_reset').inc()
            except ValueError:
                pass
            sent = True

    return render(request, 'brand_dna/auth/forgot_password.html', {
        'sent': sent,
        'error': error,
    })


def reset_password_view(request, token):
    if request.user.is_authenticated:
        return redirect('dashboard')

    from core.tenant_management.models import PasswordResetToken
    try:
        reset_token = PasswordResetToken.objects.get(token=token, is_used=False)
    except PasswordResetToken.DoesNotExist:
        return render(request, 'brand_dna/auth/reset_password.html', {
            'invalid_token': True,
        })

    if not reset_token.is_valid():
        return render(request, 'brand_dna/auth/reset_password.html', {
            'invalid_token': True,
        })

    error = None
    if request.method == 'POST':
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')
        if not password1 or not password2:
            error = 'Ambos campos son obligatorios.'
        elif password1 != password2:
            error = 'Las contraseñas no coinciden.'
        else:
            from core.tenant_management.services.auth_service import AuthService
            try:
                user = AuthService.reset_password(token, password1)
                send_mail(
                    'Tu contraseña fue restablecida — Agente Cosmic',
                    'Tu contraseña de Agente Cosmic fue restablecida exitosamente. Si no realizaste este cambio, contacta soporte de inmediato.',
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    html_message=(
                        '<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">'
                        '<h2 style="color:#e94560;">Agente Cosmic</h2>'
                        '<p>Tu contraseña fue restablecida exitosamente.</p>'
                        '<p style="color:#888;font-size:0.85rem;margin-top:16px;">'
                        'Si no realizaste este cambio, contacta soporte de inmediato a '
                        '<a href="mailto:contacto@agentecosmic.com" style="color:#e94560;">contacto@agentecosmic.com</a></p></div>'
                    ),
                    fail_silently=True,
                )
                return render(request, 'brand_dna/auth/reset_password.html', {
                    'success': True,
                })
            except ValueError as e:
                error = str(e)

    return render(request, 'brand_dna/auth/reset_password.html', {
        'token': token,
        'error': error,
    })


def google_login_view(request):
    from google_auth_oauthlib.flow import Flow

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _pkce_pair()
    request.session['google_oauth_state'] = state
    request.session['google_oauth_code_verifier'] = code_verifier

    flow = Flow.from_client_config(_GOOGLE_CLIENT_CONFIG(), scopes=_GOOGLE_SCOPES)
    flow.redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI

    auth_url, _ = flow.authorization_url(
        state=state,
        access_type='online',
        prompt='select_account',
        code_challenge=code_challenge,
        code_challenge_method='S256',
    )
    return redirect(auth_url)


def google_callback_view(request):
    from google_auth_oauthlib.flow import Flow
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    # Verificar state — protección CSRF
    state = request.GET.get('state', '')
    if not state or state != request.session.get('google_oauth_state'):
        logger.warning('Google OAuth: state inválido — posible CSRF')
        return redirect('login')

    code = request.GET.get('code')
    if not code:
        return redirect('login')

    code_verifier = request.session.pop('google_oauth_code_verifier', None)

    try:
        flow = Flow.from_client_config(
            _GOOGLE_CLIENT_CONFIG(), scopes=_GOOGLE_SCOPES, state=state
        )
        flow.redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI
        # Incluye code_verifier para completar el handshake PKCE
        flow.fetch_token(code=code, code_verifier=code_verifier)
        credentials = flow.credentials

        id_info = id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
    except Exception as e:
        logger.error(f'Google OAuth callback error: {e}')
        return redirect('login')

    email = id_info.get('email', '').lower().strip()
    name = id_info.get('name', '')
    if not email:
        return redirect('login')

    # Buscar o crear usuario — contraseña no utilizable para cuentas OAuth
    existing = User.objects.filter(email=email).first()
    if existing is None and not _is_registration_open():
        logger.warning(f'Google OAuth: registro bloqueado — límite de usuarios alcanzado ({email})')
        return redirect('login')

    user, created = User.objects.get_or_create(
        email=email,
        defaults={'username': email, 'display_name': name},
    )
    if created:
        user.set_unusable_password()
        user.email_verified = True
        user.save(update_fields=['password', 'email_verified'])
        provision_tenant(user)
        from django.contrib.auth.models import Group
        user_group, _ = Group.objects.get_or_create(name='user')
        user.groups.add(user_group)
        notify_admin_new_user(user)
        REGISTRATIONS.labels(method='google_oauth').inc()

    if not created and not user.is_active:
        user.is_active = True
        user.deactivated_at = None
        user.save(update_fields=['is_active', 'deactivated_at'])
        if user.tenant:
            user.tenant.status = 'active'
            user.tenant.save(update_fields=['status'])
            try:
                sub = user.tenant.subscription
                sub.status = 'active'
                sub.save(update_fields=['status'])
            except Exception:
                pass

    login(request, user)
    return redirect('dashboard')


@login_required
def dashboard_view(request):
    from datetime import timedelta
    from django.utils import timezone
    from core.brand_dna.rate_limits import get_user_plan, get_payment_url
    jobs = list(
        AnalysisJob.objects
        .filter(user=request.user, deleted_at__isnull=True)
        .select_related('brand_dna__calendar')
        .order_by('-created_at')[:20]
    )
    has_processing = any(j.status in ('pending', 'processing') for j in jobs)
    has_next_week_generating = False
    for job in jobs:
        brand_dna = getattr(job, 'brand_dna', None)
        if brand_dna:
            job.display_name = brand_dna.business_name
        elif job.business_description:
            job.display_name = job.business_description.split('\n')[0][:60]
        elif job.business_url:
            job.display_name = job.business_url
        else:
            job.display_name = 'Análisis pendiente'
        calendar = getattr(brand_dna, 'calendar', None) if brand_dna else None
        job.week_generating = bool(calendar and calendar.next_week_generating)
        if job.week_generating:
            has_next_week_generating = True
    # deleted_at__isnull=True -- mismo fix que can_create_calendar (rate_limits.py):
    # un calendario borrado por el usuario no debe contar contra su cupo para
    # siempre. HALLAZGO 2026-08-21.
    used_total = AnalysisJob.objects.filter(user=request.user, deleted_at__isnull=True).count()
    plan = get_user_plan(request.user)
    subscription = getattr(getattr(request.user, 'tenant', None), 'subscription', None)
    early_cta = bool(subscription and subscription.status == 'trialing' and not has_processing)
    payment_url = ''
    if early_cta:
        payment_url = get_payment_url(request.user)
    return render(request, 'brand_dna/dashboard.html', {
        'jobs': jobs,
        'user': request.user,
        'used_total': used_total,
        'max_calendars': plan.max_calendars_per_week,
        'has_processing': has_processing,
        'has_next_week_generating': has_next_week_generating,
        'early_cta': early_cta,
        'payment_url': payment_url,
        # Banner interno "contacta soporte" al alcanzar el limite -- pensado
        # solo para cuentas internas (Tester/Admin), nunca para planes reales
        # de negocio (User, Fundador, futuros). HALLAZGO 2026-08-22: antes se
        # gateaba por el grupo de Django del usuario (legado, no se actualiza
        # al migrar de plan) -- ahora usa Subscription.plan.name real, la
        # misma fuente de verdad que get_user_plan().
        'is_internal_plan': plan.name in ('Tester', 'Admin'),
    })


_CODE_RATE_LIMIT = 5
_CODE_RATE_WINDOW = 3600  # 1 hora


@login_required
def apply_code_view(request):
    if request.method != 'POST':
        return redirect('dashboard')
    from core.tenant_management.models import InvitationCode
    rate_key = f'invite_code_attempts:{request.user.id}'
    try:
        attempts = cache.incr(rate_key)
    except ValueError:
        cache.set(rate_key, 1, _CODE_RATE_WINDOW)
        attempts = 1
    if attempts > _CODE_RATE_LIMIT:
        logger.warning(f"Rate limit alcanzado en apply_code para {request.user.email}")
        return redirect('dashboard')
    code_str = request.POST.get('code', '').strip().upper()
    try:
        code_obj = InvitationCode.objects.get(code=code_str)
        if code_obj.redeem(request.user):
            INVITATION_CODES_REDEEMED.inc()
            cache.delete(rate_key)
            logger.info(f"Codigo {code_str} aplicado por {request.user.email}")
        else:
            logger.warning(f"Codigo invalido {code_str} intentado por {request.user.email}")
    except InvitationCode.DoesNotExist:
        logger.warning(f"Codigo inexistente {code_str} intentado por {request.user.email}")
    return redirect('dashboard')


@login_required
def deactivate_account_view(request):
    if request.method != 'POST':
        return redirect('dashboard')

    if request.POST.get('confirmation', '') != 'ELIMINAR':
        return redirect('dashboard')

    from django.utils import timezone as tz
    user = request.user
    user.is_active = False
    user.deactivated_at = tz.now()
    user.save(update_fields=['is_active', 'deactivated_at'])

    # Invalida todos los tokens JWT y sesiones activas en BD
    try:
        from core.tenant_management.services.jwt_service import CustomJWTService
        CustomJWTService.logout_user(user)
    except Exception:
        pass

    if user.tenant:
        user.tenant.status = 'deactivated'
        user.tenant.save(update_fields=['status'])
        try:
            sub = user.tenant.subscription
            if sub.stripe_subscription_id and sub.status not in ('canceled', 'trial_expired'):
                import stripe
                try:
                    stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=True)
                except Exception:
                    pass
            sub.status = 'canceled'
            sub.save(update_fields=['status'])
        except Exception:
            pass

    logout(request)
    return redirect('/auth/login/?reason=deactivated')


def reactivate_account_view(request, token):
    from core.tenant_management.models import EmailVerificationToken

    try:
        verification = EmailVerificationToken.objects.get(token=token)
    except EmailVerificationToken.DoesNotExist:
        return redirect('login')

    if not verification.is_valid():
        return redirect('login')

    email = verification.email
    user = User.objects.filter(email=email, is_active=False).first()
    if not user:
        return redirect('login')

    if request.method != 'POST':
        return render(request, 'brand_dna/auth/reactivate.html', {
            'email': email,
            'token': token,
        })

    user.is_active = True
    user.deactivated_at = None
    new_password = verification.user_data.get('password')
    if new_password:
        user.password = new_password
        user.save(update_fields=['is_active', 'deactivated_at', 'password'])
    else:
        user.save(update_fields=['is_active', 'deactivated_at'])

    if user.tenant:
        user.tenant.status = 'active'
        user.tenant.save(update_fields=['status'])
        try:
            sub = user.tenant.subscription
            sub.status = 'active'
            sub.save(update_fields=['status'])
        except Exception:
            pass

    verification.is_used = True
    verification.save(update_fields=['is_used'])

    login(request, user)
    return redirect('dashboard')
