import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db

MOCK_VERTEX_RESPONSE = '''[
  {"caption": "Post 1: diseno que convierte", "hashtags": ["#disenoweb"], "suggested_time": "19:00"},
  {"caption": "Post 2: presencia digital", "hashtags": ["#marketing"], "suggested_time": "12:00"},
  {"caption": "Post 3: tu marca online", "hashtags": ["#branding"], "suggested_time": "19:00"},
  {"caption": "Post 4: resultados reales", "hashtags": ["#resultados"], "suggested_time": "09:00"},
  {"caption": "Post 5: clientes felices", "hashtags": ["#testimonios"], "suggested_time": "19:00"},
  {"caption": "Post 6: innovacion digital", "hashtags": ["#tech"], "suggested_time": "12:00"},
  {"caption": "Post 7: cierra la semana", "hashtags": ["#viernes"], "suggested_time": "17:00"}
]'''


@pytest.fixture
def brand_dna():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    return BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno', 'web'],
        audience='PYMEs', tone='profesional', primary_colors=['#1a1a2e'],
    )


def _mock_vertex_client(json_text):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json_text
    mock_client.models.generate_content.return_value = mock_resp
    return mock_client


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_returns_7_posts(brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(brand_dna)

    assert len(result) == 7


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_post_has_required_keys(brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(brand_dna)

    post = result[0]
    assert 'caption' in post
    assert 'hashtags' in post
    assert 'suggested_time' in post
    assert isinstance(post['hashtags'], list)
