import pytest
import secrets
from unittest.mock import patch
from django.test import override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db

# Contraseñas generadas dinámicamente — no hardcodeadas en el código fuente
# (misma convención que test_auth_views.py y el resto de la suite; un literal
# aquí dispara los escáneres de secretos aunque el valor sea de prueba).
_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"


@pytest.fixture
def full_setup():
    job = AnalysisJob.objects.create(email='cliente@ejemplo.com', business_url='https://tuwebmx.com')
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    calendar = ContentCalendar.objects.create(brand_dna=dna)
    posts = []
    for i in range(1, 8):
        posts.append(ContentPost.objects.create(
            calendar=calendar, day_number=i,
            caption=f'Post del dia {i}',
            image_url=f'https://storage.googleapis.com/bucket/img{i}.jpg',
            suggested_time='19:00',
            hashtags=['#disenoweb'],
            scheduled_at=timezone.now() + timedelta(days=i),
        ))
    return job, dna, calendar, posts


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_initial_email_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_initial(job=job, brand_dna=dna)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_initial_email_subject_contains_business_name(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_initial(job=job, brand_dna=dna)
    subject = mock_send.call_args[0][0]
    assert 'Tu Web MX' in subject


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_daily_email_marks_post_sent(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    post = posts[0]
    with patch('core.content_pipeline.email_sender.send_mail'):
        sender = EmailSender()
        sender.send_daily(post=post)
    post.refresh_from_db()
    assert post.status == ContentPost.STATUS_SENT
    assert post.sent_at is not None


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_uses_real_date_not_day_number(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    post = posts[0]
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_daily(post=post)
    subject = mock_send.call_args[0][0]
    plain = mock_send.call_args[0][1]
    assert 'Día 1' not in subject
    assert str(post.scheduled_at.day) in subject
    assert 'No se te olvide publicar' in plain


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_daily_is_idempotent_for_already_sent_post(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    post = posts[0]
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_daily(post=post)
        sender.send_daily(post=post)
    assert mock_send.call_count == 1
    post.refresh_from_db()
    assert post.status == ContentPost.STATUS_SENT


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_send_trial_expired_email_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    from core.tenant_management.models import TenantModel, Subscription, Plan
    job, dna, calendar, posts = full_setup
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username=job.email, email=job.email, password=_TEST_PWD)
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=plan, status='trial_expired')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job.user = user
    job.save(update_fields=['user'])

    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_trial_expired(job=job, brand_dna=dna)

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    html = call_kwargs[1]['html_message']
    # Ya no va a Stripe: entra al calendario por magic link, donde el usuario
    # ve su contenido y puede agregar fotos antes de pagar.
    assert 'buy.stripe.com' not in html
    assert f'/calendar/{job.id}/' in html or '/auth/entrar/' in html


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_payment_failed_email_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    from core.tenant_management.models import TenantModel, Subscription, Plan
    job, dna, calendar, posts = full_setup
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username=job.email, email=job.email, password=_TEST_PWD)
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=plan, status='past_due', stripe_customer_id='cus_test1')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job.user = user
    job.save(update_fields=['user'])

    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_payment_failed(job=job, brand_dna=dna)

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    html = call_kwargs[1]['html_message']
    assert 'https://cosmic.anuarbarrera.dev' in html


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_month_ready_email_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_month_ready(job=job, brand_dna=dna)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    subject = mock_send.call_args[0][0]
    assert 'Tu Web MX' in subject
    assert 'mes' in subject.lower()


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_month_ready_email_uses_month_specific_copy(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_month_ready(job=job, brand_dna=dna)
    html = mock_send.call_args[1]['html_message']
    assert '7 días' not in html
    assert '4 semanas' in html
    assert 'La generación del mes de contenido que adquiriste' in html


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_week_ready_email_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_week_ready(job=job, brand_dna=dna)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    subject = mock_send.call_args[0][0]
    assert 'Tu Web MX' in subject
    assert 'semana' in subject.lower()


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_week_ready_email_clarifies_it_is_part_of_paid_month(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_week_ready(job=job, brand_dna=dna)
    html = mock_send.call_args[1]['html_message']
    assert 'del mes que adquiriste' in html



@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_send_month_expired_email_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    from core.tenant_management.models import TenantModel, Subscription, Plan
    job, dna, calendar, posts = full_setup
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username=job.email, email=job.email, password=_TEST_PWD)
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=plan, status='trial_expired')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job.user = user
    job.save(update_fields=['user'])

    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_month_expired(job=job, brand_dna=dna)

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    html = call_kwargs[1]['html_message']
    # Ya no va a Stripe: entra al calendario por magic link.
    assert 'buy.stripe.com' not in html
    assert f'/calendar/{job.id}/' in html or '/auth/entrar/' in html
    assert 'suscripción' not in html.lower()




@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_reactivation_calendar_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_reactivation_calendar(calendar=calendar)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    assert 'Tu Web MX' in call_kwargs[0][0]


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_reactivation_analysis_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    from django.contrib.auth import get_user_model
    job, dna, calendar, posts = full_setup
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username=job.email, email=job.email, password=_TEST_PWD)
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_reactivation_analysis(user=user)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert user.email in call_kwargs[1]['recipient_list']


# ── Magic link en los correos ──

from django.core.cache import cache
from unittest.mock import patch
from core.tenant_management.models import LoginToken


@pytest.fixture
def job_con_user(full_setup):
    """full_setup crea el AnalysisJob SIN user (el campo es nullable).
    Aquí se le asigna uno para ejercitar el camino con magic link."""
    User = get_user_model()
    job, dna, calendar, posts = full_setup
    user = User.objects.create_user(email='dueno@ejemplo.com', password=_TEST_PWD)
    job.user = user
    job.save(update_fields=['user'])
    return job, dna, calendar, posts, user


def test_magic_url_crea_token_con_el_destino_correcto(db):
    User = get_user_model()
    from core.content_pipeline.email_sender import _magic_url
    user = User.objects.create_user(email='mu@ejemplo.com', password=_TEST_PWD)

    url = _magic_url(user, '/calendar/abc/')

    tok = LoginToken.objects.get(user=user)
    assert tok.redirect_to == '/calendar/abc/'
    assert tok.token in url
    assert url.startswith('http')


def test_magic_url_sin_user_devuelve_link_normal(db):
    """AnalysisJob.user es nullable (on_delete=SET_NULL): un job cuyo usuario
    fue eliminado sigue mandando correos. Ese caso es ESPERADO, no excepcional,
    así que se atiende con un guard explícito en vez de dejarlo caer al
    except — si no, cada correo de un job sin usuario escribiría un stack
    trace completo en los logs."""
    from core.content_pipeline.email_sender import _magic_url

    url = _magic_url(None, '/dashboard/')

    assert url.endswith('/dashboard/')
    assert '/auth/entrar/' not in url
    assert LoginToken.objects.count() == 0


def test_magic_url_fail_open_si_falla_la_creacion_del_token(db):
    """LA PRUEBA MÁS IMPORTANTE DEL PLAN: si la base de datos falla al crear el
    token, el correo que anuncia el contenido generado DEBE salir igual, con el
    link de siempre. Nunca un fallo del magic link puede bloquear el correo que
    entrega el valor."""
    User = get_user_model()
    from core.content_pipeline.email_sender import _magic_url
    user = User.objects.create_user(email='fo@ejemplo.com', password=_TEST_PWD)

    with patch(
        'core.tenant_management.models.LoginToken.objects.create',
        side_effect=Exception('base de datos caida'),
    ):
        url = _magic_url(user, '/calendar/xyz/')

    assert url.endswith('/calendar/xyz/')
    assert '/auth/entrar/' not in url


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_los_cinco_correos_de_calendario_llevan_magic_link(job_con_user):
    """Los 5 correos que aterrizan en calendar_review. Se prueban juntos porque
    comparten destino: si uno se queda sin _magic_url, este test lo atrapa."""
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts, user = job_con_user
    sender = EmailSender()
    destino = f'/calendar/{job.id}/'

    llamadas = [
        lambda: sender.send_initial(job=job, brand_dna=dna),
        lambda: sender.send_month_ready(job=job, brand_dna=dna),
        lambda: sender.send_week_ready(job=job, brand_dna=dna),
        lambda: sender.send_daily(post=posts[0]),
        lambda: sender.send_reactivation_calendar(calendar=calendar),
    ]

    for llamada in llamadas:
        LoginToken.objects.all().delete()
        with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
            llamada()
        tok = LoginToken.objects.get(user=user)
        assert tok.redirect_to == destino
        assert f'/auth/entrar/{tok.token}/' in mock_send.call_args[1]['html_message']


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_payment_failed_lleva_magic_link_al_dashboard(job_con_user):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts, user = job_con_user

    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        EmailSender().send_payment_failed(job=job, brand_dna=dna)

    tok = LoginToken.objects.get(user=user)
    assert tok.redirect_to == '/dashboard/'
    assert f'/auth/entrar/{tok.token}/' in mock_send.call_args[1]['html_message']


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_reactivation_analysis_lleva_magic_link_a_nuevo_analisis(db):
    User = get_user_model()
    from core.content_pipeline.email_sender import EmailSender
    user = User.objects.create_user(email='react@ejemplo.com', password=_TEST_PWD)

    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        EmailSender().send_reactivation_analysis(user=user)

    from django.urls import reverse
    tok = LoginToken.objects.get(user=user)
    assert tok.redirect_to == reverse('new_analysis')
    assert f'/auth/entrar/{tok.token}/' in mock_send.call_args[1]['html_message']


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_los_correos_de_vencimiento_llevan_al_calendario_no_a_stripe(job_con_user):
    """Antes iban directo a buy.stripe.com desde el buzón. Eso rompía la
    expectativa (el botón prometía generar, el destino cobraba), generaba el
    mes con el pool de fotos de hace un mes sin poder actualizarlo, y dejaba al
    usuario en la pantalla de login al volver de Stripe — porque llegaba al
    pago sin sesión.

    Ahora entran al calendario por magic link: ven su contenido, el banner de
    pago con el modal de fotos ya construido, y llegan a Stripe YA logueados,
    así que el retorno a /dashboard/ los encuentra con sesión viva."""
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts, user = job_con_user
    sender = EmailSender()
    destino = f'/calendar/{job.id}/'

    for llamada in (sender.send_trial_expired, sender.send_month_expired):
        LoginToken.objects.all().delete()
        with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
            llamada(job=job, brand_dna=dna)

        tok = LoginToken.objects.get(user=user)
        assert tok.redirect_to == destino
        html = mock_send.call_args[1]['html_message']
        plain = mock_send.call_args[0][1]
        assert f'/auth/entrar/{tok.token}/' in html
        assert 'buy.stripe.com' not in html
        assert 'buy.stripe.com' not in plain
