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
from .auth_forms import RegisterForm, LoginForm
from .models import AnalysisJob

logger = logging.getLogger(__name__)
User = get_user_model()


def notify_admin_new_user(user, invitation_code=None):
    try:
        admin_email = settings.ADMIN_NOTIFICATION_EMAIL
        group_name = user.groups.first().name if user.groups.exists() else 'user'
        code_info = f'<p><strong>Codigo usado:</strong> {invitation_code}</p>' if invitation_code else ''
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
        return redirect('login')

    email = verification.email
    user_data = verification.user_data

    user = User.objects.create_user(
        email=email,
        password=None,
        username=email,
    )
    user.password = user_data['password']
    user.email_verified = True
    user.save(update_fields=['password', 'email_verified'])

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
            cache_key = f'login_attempts:{email}'
            attempts = cache.get(cache_key, 0)
            if attempts >= _LOGIN_MAX_ATTEMPTS:
                error = 'Demasiados intentos. Intenta de nuevo en 5 minutos.'
            else:
                password = form.cleaned_data['password']
                user = authenticate(request, email=email, password=password)
                if user is not None:
                    cache.delete(cache_key)
                    login(request, user)
                    next_url = request.GET.get('next', 'dashboard')
                    return redirect(next_url)
                cache.set(cache_key, attempts + 1, _LOGIN_LOCKOUT_SECONDS)
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
        email = request.POST.get('email', '').lower().strip()
        if email:
            from core.tenant_management.services.auth_service import AuthService
            try:
                AuthService.initiate_password_reset(email)
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
                        '<a href="mailto:contacto.neia@gmail.com" style="color:#e94560;">contacto.neia@gmail.com</a></p></div>'
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
        from django.contrib.auth.models import Group
        user_group, _ = Group.objects.get_or_create(name='user')
        user.groups.add(user_group)
        notify_admin_new_user(user)

    login(request, user)
    return redirect('dashboard')


@login_required
def dashboard_view(request):
    from datetime import timedelta
    from django.utils import timezone
    from core.brand_dna.rate_limits import get_user_plan
    jobs = (
        AnalysisJob.objects
        .filter(user=request.user, deleted_at__isnull=True)
        .select_related('brand_dna')
        .order_by('-created_at')[:20]
    )
    used_total = AnalysisJob.objects.filter(user=request.user).count()
    plan = get_user_plan(request.user)
    return render(request, 'brand_dna/dashboard.html', {
        'jobs': jobs,
        'user': request.user,
        'used_total': used_total,
        'max_calendars': plan.max_calendars_per_week,
    })


@login_required
def apply_code_view(request):
    if request.method != 'POST':
        return redirect('dashboard')
    from core.tenant_management.models import InvitationCode
    code_str = request.POST.get('code', '').strip().upper()
    try:
        code_obj = InvitationCode.objects.get(code=code_str)
        if code_obj.redeem(request.user):
            logger.info(f"Codigo {code_str} aplicado por {request.user.email}")
        else:
            logger.warning(f"Codigo invalido {code_str} intentado por {request.user.email}")
    except InvitationCode.DoesNotExist:
        logger.warning(f"Codigo inexistente {code_str} intentado por {request.user.email}")
    return redirect('dashboard')
