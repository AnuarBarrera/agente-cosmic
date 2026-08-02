import json
import pytest
from unittest.mock import patch
from django.test import Client
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_plan():
    from core.tenant_management.models import Plan
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    return plan


@pytest.fixture
def user(django_user_model, free_plan):
    from core.tenant_management.models import TenantModel, Subscription
    u = django_user_model.objects.create_user(
        username='dna-edit@test.com', email='dna-edit@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=u.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=free_plan)
    u.tenant = tenant
    u.save(update_fields=['tenant'])
    return u


@pytest.fixture
def job_with_calendar(user):
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com', user=user,
        status=AnalysisJob.STATUS_DONE, stage=AnalysisJob.STAGE_COMPLETE, progress=100,
    )
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    calendar = ContentCalendar.objects.create(brand_dna=dna)
    for i in range(1, 8):
        ContentPost.objects.create(
            calendar=calendar, day_number=i, caption=f'Post {i}',
            image_url='https://example.com/img.jpg' if i == 1 else '',
            suggested_time='19:00', hashtags=[],
            scheduled_at=timezone.now() + timedelta(days=i),
        )
    return job


def test_results_page_renders_editable_dna_when_done(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    response = c.get(f'/resultados/{job_with_calendar.id}/')
    assert response.status_code == 200
    content = response.content.decode()
    assert 'value-description' in content
    assert 'value-tone' in content
    assert 'Reanalizar' in content
    assert 'regenBanner' in content


def test_field_edit_description(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'description', 'action': 'edit', 'value': 'Nueva descripcion'}),
        content_type='application/json',
    )
    assert response.status_code == 200
    data = response.json()
    assert data['value'] == 'Nueva descripcion'
    job_with_calendar.brand_dna.refresh_from_db()
    assert job_with_calendar.brand_dna.description == 'Nueva descripcion'


def test_field_edit_business_name_rejected(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'business_name', 'action': 'edit', 'value': 'Otro nombre'}),
        content_type='application/json',
    )
    assert response.status_code == 400


def test_field_edit_invalid_tone_rejected(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'tone', 'action': 'edit', 'value': 'sarcastico'}),
        content_type='application/json',
    )
    assert response.status_code == 400
    job_with_calendar.brand_dna.refresh_from_db()
    assert job_with_calendar.brand_dna.tone == 'profesional'


def test_field_edit_valid_tone(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'tone', 'action': 'edit', 'value': 'casual'}),
        content_type='application/json',
    )
    assert response.status_code == 200
    job_with_calendar.brand_dna.refresh_from_db()
    assert job_with_calendar.brand_dna.tone == 'casual'


def test_field_edit_keywords_splits_on_comma(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'keywords', 'action': 'edit', 'value': 'web, diseno, marketing'}),
        content_type='application/json',
    )
    assert response.status_code == 200
    job_with_calendar.brand_dna.refresh_from_db()
    assert job_with_calendar.brand_dna.keywords == ['web', 'diseno', 'marketing']


def test_field_edit_colors_invalid_format_rejected(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'primary_colors', 'action': 'edit', 'value': 'rojo, azul'}),
        content_type='application/json',
    )
    assert response.status_code == 400


def test_field_edit_colors_valid_hex(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'primary_colors', 'action': 'edit', 'value': '#E94560, #1a1a2e'}),
        content_type='application/json',
    )
    assert response.status_code == 200
    job_with_calendar.brand_dna.refresh_from_db()
    assert job_with_calendar.brand_dna.primary_colors == ['#E94560', '#1a1a2e']


def test_field_reanalyze_tone_rejected(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'tone', 'action': 'reanalyze', 'value': 'no me gusta'}),
        content_type='application/json',
    )
    assert response.status_code == 400


def test_field_reanalyze_description_calls_gemini(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.genai.Client') as MockClient:
        mock_resp = MockClient.return_value.models.generate_content.return_value
        mock_resp.text = '{"value": "Descripcion corregida por IA"}'
        mock_resp.usage_metadata = None
        response = c.post(
            f'/api/brand-dna/{job_with_calendar.id}/field/',
            data=json.dumps({'field': 'description', 'action': 'reanalyze', 'value': 'no menciona que somos B2B'}),
            content_type='application/json',
        )
    assert response.status_code == 200
    data = response.json()
    assert data['value'] == 'Descripcion corregida por IA'


def test_field_reanalyze_colors_without_url_rejected(user, job_with_calendar):
    job_with_calendar.business_url = ''
    job_with_calendar.save(update_fields=['business_url'])
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'primary_colors', 'action': 'reanalyze', 'value': ''}),
        content_type='application/json',
    )
    assert response.status_code == 400


def test_field_action_requires_login(job_with_calendar):
    c = Client()
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'description', 'action': 'edit', 'value': 'x'}),
        content_type='application/json',
    )
    assert response.status_code == 302


def test_field_action_blocks_other_user(job_with_calendar, django_user_model, free_plan):
    from core.tenant_management.models import TenantModel, Subscription
    other = django_user_model.objects.create_user(
        username='other-dna@test.com', email='other-dna@test.com', password='pass1234'
    )
    t = TenantModel.objects.create(name=other.email, status='active')
    Subscription.objects.create(tenant=t, plan=free_plan)
    other.tenant = t
    other.save(update_fields=['tenant'])
    c = Client()
    c.force_login(other)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'description', 'action': 'edit', 'value': 'hackeo'}),
        content_type='application/json',
    )
    assert response.status_code == 404


_ALL_DNA_FIELDS = ['description', 'audience', 'tone', 'keywords', 'primary_colors']


def test_regenerate_calendar_updates_pending_posts_only(user, job_with_calendar):
    job_with_calendar.brand_dna.approved_fields = _ALL_DNA_FIELDS
    job_with_calendar.brand_dna.save(update_fields=['approved_fields'])
    calendar = job_with_calendar.brand_dna.calendar
    day1 = calendar.posts.get(day_number=1)
    day2 = calendar.posts.get(day_number=2)
    day2.status = ContentPost.STATUS_SENT
    day2.save(update_fields=['status'])

    fake_posts_data = [{'caption': f'Nuevo caption {i}', 'hashtags': ['#nuevo']} for i in range(1, 8)]

    c = Client()
    c.force_login(user)
    with patch('core.content_pipeline.generators.text_generator.TextGenerator.generate', return_value=fake_posts_data), \
         patch('core.content_pipeline.generators.image_generator.ImageGenerator.generate', return_value='https://example.com/new-img.jpg'):
        response = c.post(f'/api/calendar/{job_with_calendar.id}/regenerate/')

    assert response.status_code == 200
    data = response.json()
    assert 1 in data['regenerated_days']
    assert 2 not in data['regenerated_days']

    day1.refresh_from_db()
    day2.refresh_from_db()
    assert day1.caption == 'Nuevo caption 1'
    assert day1.image_url == 'https://example.com/new-img.jpg'
    assert day2.caption == 'Post 2'  # ya enviado, no se toca


def test_regenerate_calendar_no_calendar_returns_404(user):
    job = AnalysisJob.objects.create(email=user.email, business_url='https://tuwebmx.com', user=user)
    c = Client()
    c.force_login(user)
    response = c.post(f'/api/calendar/{job.id}/regenerate/')
    assert response.status_code == 404


def test_regenerate_calendar_all_sent_returns_400(user, job_with_calendar):
    job_with_calendar.brand_dna.approved_fields = _ALL_DNA_FIELDS
    job_with_calendar.brand_dna.save(update_fields=['approved_fields'])
    job_with_calendar.brand_dna.calendar.posts.update(status=ContentPost.STATUS_SENT)
    c = Client()
    c.force_login(user)
    response = c.post(f'/api/calendar/{job_with_calendar.id}/regenerate/')
    assert response.status_code == 400


def test_regenerate_calendar_blocked_when_not_all_fields_approved(user, job_with_calendar):
    job_with_calendar.brand_dna.approved_fields = ['description', 'audience']  # faltan tone/keywords/primary_colors
    job_with_calendar.brand_dna.save(update_fields=['approved_fields'])
    c = Client()
    c.force_login(user)
    response = c.post(f'/api/calendar/{job_with_calendar.id}/regenerate/')
    assert response.status_code == 400
    assert 'Aprueba todos los campos' in response.json()['error']


def test_regenerate_calendar_blocked_when_no_fields_approved(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    response = c.post(f'/api/calendar/{job_with_calendar.id}/regenerate/')
    assert response.status_code == 400


def test_field_approve_action_persists(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'description', 'action': 'approve'}),
        content_type='application/json',
    )
    assert response.status_code == 200
    data = response.json()
    assert data['approved_fields'] == ['description']
    assert data['all_approved'] is False
    job_with_calendar.brand_dna.refresh_from_db()
    assert job_with_calendar.brand_dna.approved_fields == ['description']


def test_field_approve_reports_all_approved_when_complete(user, job_with_calendar):
    c = Client()
    c.force_login(user)
    for field in _ALL_DNA_FIELDS[:-1]:
        c.post(
            f'/api/brand-dna/{job_with_calendar.id}/field/',
            data=json.dumps({'field': field, 'action': 'approve'}),
            content_type='application/json',
        )
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': _ALL_DNA_FIELDS[-1], 'action': 'approve'}),
        content_type='application/json',
    )
    data = response.json()
    assert data['all_approved'] is True
    assert set(data['approved_fields']) == set(_ALL_DNA_FIELDS)


def test_field_edit_rejected_when_already_approved(user, job_with_calendar):
    job_with_calendar.brand_dna.approved_fields = ['description']
    job_with_calendar.brand_dna.save(update_fields=['approved_fields'])
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'description', 'action': 'edit', 'value': 'Nueva descripcion'}),
        content_type='application/json',
    )
    assert response.status_code == 400
    assert 'ya está aprobado' in response.json()['error']
    job_with_calendar.brand_dna.refresh_from_db()
    assert job_with_calendar.brand_dna.description == 'Agencia digital'  # sin cambios
    assert job_with_calendar.brand_dna.approved_fields == ['description']  # sigue aprobado


def test_field_edit_allowed_when_other_field_approved(user, job_with_calendar):
    job_with_calendar.brand_dna.approved_fields = ['audience']
    job_with_calendar.brand_dna.save(update_fields=['approved_fields'])
    c = Client()
    c.force_login(user)
    response = c.post(
        f'/api/brand-dna/{job_with_calendar.id}/field/',
        data=json.dumps({'field': 'description', 'action': 'edit', 'value': 'Nueva descripcion'}),
        content_type='application/json',
    )
    assert response.status_code == 200
    job_with_calendar.brand_dna.refresh_from_db()
    assert job_with_calendar.brand_dna.description == 'Nueva descripcion'
    assert job_with_calendar.brand_dna.approved_fields == ['audience']  # intacto


def test_field_reanalyze_rejected_when_already_approved(user, job_with_calendar):
    job_with_calendar.brand_dna.approved_fields = ['description']
    job_with_calendar.brand_dna.save(update_fields=['approved_fields'])
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.genai.Client') as MockClient:
        mock_resp = MockClient.return_value.models.generate_content.return_value
        mock_resp.text = 'Descripcion corregida por IA'
        mock_resp.usage_metadata = None
        response = c.post(
            f'/api/brand-dna/{job_with_calendar.id}/field/',
            data=json.dumps({'field': 'description', 'action': 'reanalyze', 'value': 'no menciona que somos B2B'}),
            content_type='application/json',
        )
    assert response.status_code == 400
    MockClient.return_value.models.generate_content.assert_not_called()
    assert 'ya está aprobado' in response.json()['error']
    job_with_calendar.brand_dna.refresh_from_db()
    assert job_with_calendar.brand_dna.description == 'Agencia digital'  # sin cambios
