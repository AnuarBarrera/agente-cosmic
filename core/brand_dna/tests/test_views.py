import pytest
import json
from unittest.mock import patch
from django.test import Client
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db


def test_landing_page_returns_200():
    c = Client()
    response = c.get('/')
    assert response.status_code == 200


def test_analyze_submit_creates_job():
    c = Client()
    with patch('core.brand_dna.views.django_rq'):
        response = c.post('/analizar/', {
            'email': 'test@example.com',
            'business_url': 'https://tuwebmx.com',
        })
    assert response.status_code == 302
    assert AnalysisJob.objects.filter(email='test@example.com').exists()


def test_analyze_submit_enqueues_task():
    c = Client()
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        c.post('/analizar/', {
            'email': 'test@example.com',
            'business_url': 'https://tuwebmx.com',
        })
    mock_rq.enqueue.assert_called_once()


def test_status_api_returns_progress():
    c = Client()
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status='processing', stage='logo', progress=50,
    )
    response = c.get(f'/api/brand-dna/status/{job.id}/')
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data['progress'] == 50
    assert data['stage'] == 'logo'
    assert data['status'] == 'processing'


def test_status_api_returns_brand_dna_when_done():
    c = Client()
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status='done', stage='complete', progress=100,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    response = c.get(f'/api/brand-dna/status/{job.id}/')
    data = json.loads(response.content)
    assert data['brand_dna'] is not None
    assert data['brand_dna']['business_name'] == 'Tu Web MX'


def test_results_page_returns_200():
    c = Client()
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    response = c.get(f'/resultados/{job.id}/')
    assert response.status_code == 200
