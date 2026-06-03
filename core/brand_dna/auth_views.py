import base64
import hashlib
import secrets
import logging
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from .auth_forms import RegisterForm, LoginForm
from .models import AnalysisJob

logger = logging.getLogger(__name__)
User = get_user_model()

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


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            password = form.cleaned_data['password1']
            # set_password() inside create_user() uses PBKDF2-SHA256 + random salt
            user = User.objects.create_user(
                email=email,
                password=password,
                username=email,
            )
            login(request, user)
            return redirect('dashboard')
    else:
        form = RegisterForm()

    return render(request, 'brand_dna/auth/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email'].lower().strip()
            password = form.cleaned_data['password']
            # check_password() verifies PBKDF2 hash — nunca compara texto plano
            user = authenticate(request, email=email, password=password)
            if user is not None:
                login(request, user)
                next_url = request.GET.get('next', 'dashboard')
                return redirect(next_url)
            error = 'Correo o contraseña incorrectos.'
    else:
        form = LoginForm()

    return render(request, 'brand_dna/auth/login.html', {'form': form, 'error': error})


def logout_view(request):
    logout(request)
    return redirect('login')


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
    user, created = User.objects.get_or_create(
        email=email,
        defaults={'username': email, 'display_name': name},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])

    login(request, user)
    return redirect('dashboard')


@login_required
def dashboard_view(request):
    jobs = (
        AnalysisJob.objects
        .filter(user=request.user, deleted_at__isnull=True)
        .select_related('brand_dna')
        .order_by('-created_at')[:20]
    )
    return render(request, 'brand_dna/dashboard.html', {
        'jobs': jobs,
        'user': request.user,
    })
