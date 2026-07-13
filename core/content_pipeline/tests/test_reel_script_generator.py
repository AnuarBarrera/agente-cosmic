import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db


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
def test_generate_returns_fallback_on_api_error(brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    post_data = {'caption': 'Descubre nuestra nueva coleccion de bolsos artesanales'}
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    assert set(result.keys()) == {
        'hook_text', 'highlight_word', 'tag_cta', 'narration_script',
        'scene_prompts', 'music_mood',
    }
    assert len(result['scene_prompts']) == 3
    assert len(result['hook_text']) > 0


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_parses_valid_gemini_response(brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"Bolsos que cuentan tu historia","highlight_word":"historia",'
        '"tag_cta":"Compra ahora","narration_script":"Cada bolso es unico, hecho a mano con materiales de la mas alta calidad.",'
        '"scene_prompts":["scene1, no text, no logos, no people speaking to camera.",'
        '"scene2, no text, no logos, no people speaking to camera.",'
        '"scene3, no text, no logos, no people speaking to camera."],'
        '"music_mood":"warm acoustic, artisanal feel"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    assert result['hook_text'] == 'Bolsos que cuentan tu historia'
    assert result['highlight_word'] == 'historia'
    assert result['tag_cta'] == 'Compra ahora'
    assert len(result['scene_prompts']) == 3


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_uses_fallback_scenes_when_gemini_returns_wrong_count(brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    post_data = {'caption': 'Bolsos artesanales'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C",'
        '"narration_script":"N","scene_prompts":["solo una escena"],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    assert len(result['scene_prompts']) == 3


@pytest.fixture
def sensitive_brand_dna():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://pediatra.com')
    return BrandDNA.objects.create(
        job=job, business_name='Pediatra Juan Gonzalez', business_url='https://pediatra.com',
        description='Atencion pediatrica para ninos de 0 a 12 anos',
        keywords=['pediatria', 'salud infantil'],
        audience='Padres y tutores de ninos', tone='profesional', primary_colors=['#1a1a2e'],
    )


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_rejects_banned_language_in_sensitive_niche(sensitive_brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    post_data = {'caption': 'Atencion pediatrica de calidad'}
    response_json = (
        '{"hook_text":"Garantizamos tu salud","highlight_word":"Garantizamos","tag_cta":"Agenda hoy",'
        '"narration_script":"Aseguramos resultados en cada consulta.","scene_prompts":'
        '["s1, no text, no logos, no people speaking to camera.",'
        '"s2, no text, no logos, no people speaking to camera.",'
        '"s3, no text, no logos, no people speaking to camera."],"music_mood":"calm"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, sensitive_brand_dna)

    assert result['hook_text'] != 'Garantizamos tu salud'
