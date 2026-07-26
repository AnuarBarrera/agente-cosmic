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


@pytest.fixture(autouse=True)
def _mock_brand_consistency_qc():
    with patch('core.content_pipeline.generators.reel_script_generator.audit_brand_consistency', return_value={}) as mock_audit, \
         patch('core.content_pipeline.generators.reel_script_generator.rewrite_for_brand_consistency') as mock_rewrite:
        yield mock_audit, mock_rewrite


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
    assert len(result['scene_prompts']) == 6
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
        '"scene3, no text, no logos, no people speaking to camera.",'
        '"scene4, no text, no logos, no people speaking to camera.",'
        '"scene5, no text, no logos, no people speaking to camera.",'
        '"scene6, no text, no logos, no people speaking to camera."],'
        '"music_mood":"warm acoustic, artisanal feel"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    assert result['hook_text'] == 'Bolsos que cuentan tu historia'
    assert result['highlight_word'] == 'historia'
    assert result['tag_cta'] == 'Compra ahora'
    assert len(result['scene_prompts']) == 6


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

    assert len(result['scene_prompts']) == 6


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


def test_scrub_brand_leak_replaces_scene_mentioning_business_name():
    from core.content_pipeline.generators.reel_script_generator import _scrub_brand_leak, _FALLBACK_SCENES
    scenes = [
        'a candle with a label reading MariBelas, soft lighting, no text, no logos.',
        'clean detail shot, no text, no logos.',
        'clean detail shot, no text, no logos.',
        'clean detail shot, no text, no logos.',
        'clean detail shot, no text, no logos.',
        'clean detail shot, no text, no logos.',
    ]
    result = _scrub_brand_leak(scenes, 'MariBelas')
    assert result[0] == _FALLBACK_SCENES[0]
    assert result[1:] == scenes[1:]


def test_scrub_brand_leak_replaces_scene_with_generic_branding_keyword():
    from core.content_pipeline.generators.reel_script_generator import _scrub_brand_leak, _FALLBACK_SCENES
    scenes = [
        'clean overhead shot, no text, no logos.',
        'product with visible packaging label design, no text, no logos.',
        'clean detail shot, no text, no logos.',
        'clean detail shot, no text, no logos.',
        'clean detail shot, no text, no logos.',
        'clean detail shot, no text, no logos.',
    ]
    result = _scrub_brand_leak(scenes, 'Tu Web MX')
    assert result[1] == _FALLBACK_SCENES[1]
    assert result[0] == scenes[0]


def test_scrub_brand_leak_leaves_clean_scenes_untouched():
    from core.content_pipeline.generators.reel_script_generator import _scrub_brand_leak
    scenes = ['clean shot describing texture and material, no text, no logos.'] * 6
    result = _scrub_brand_leak(scenes, 'MariBelas')
    assert result == scenes


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_scrubs_business_name_leak_from_gemini_response(brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator, _FALLBACK_SCENES
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C","narration_script":"N",'
        '"scene_prompts":["a bag with a label reading Tu Web MX, no text, no logos.",'
        '"s2, no text, no logos.","s3, no text, no logos.","s4, no text, no logos.",'
        '"s5, no text, no logos.","s6, no text, no logos."],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    assert result['scene_prompts'][0] == _FALLBACK_SCENES[0]
    assert result['scene_prompts'][1] == 's2, no text, no logos.'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_prompt_differentiates_veo_scene_from_imagen_scenes(brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C","narration_script":"N",'
        '"scene_prompts":["s1","s2","s3","s4","s5","s6"],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        ReelScriptGenerator().generate(post_data, brand_dna)

    sent_prompt = mock_vc.return_value.models.generate_content.call_args.kwargs['contents']
    assert 'scene_prompts[0]' in sent_prompt
    assert 'GENERADOR DE VIDEO' in sent_prompt
    assert 'scene_prompts[1] a scene_prompts[5]' in sent_prompt
    assert 'GENERADOR DE IMAGEN FIJA' in sent_prompt
    assert '5 shots' in sent_prompt
    assert 'NO debe incluir manipulacion precisa' in sent_prompt


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_prompt_avoids_manufacturing_process_and_requires_style_consistency(brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C","narration_script":"N",'
        '"scene_prompts":["s1","s2","s3","s4","s5","s6"],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        ReelScriptGenerator().generate(post_data, brand_dna)

    sent_prompt = mock_vc.return_value.models.generate_content.call_args.kwargs['contents']
    assert 'manos trabajando' not in sent_prompt
    assert 'cliente disfrutando' in sent_prompt or 'sensacion de satisfaccion' in sent_prompt
    assert 'mismo estilo fotografico consistente' in sent_prompt



@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_rewrites_field_flagged_by_brand_consistency_audit(brand_dna, _mock_brand_consistency_qc):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    mock_audit, mock_rewrite = _mock_brand_consistency_qc
    mock_audit.return_value = {'narration_script': 'connotacion inferior'}
    mock_rewrite.return_value = 'Hecho con upcycling.'
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C",'
        '"narration_script":"Hecho con materiales reutilizados.",'
        '"scene_prompts":["s1, no text, no logos.","s2, no text, no logos.",'
        '"s3, no text, no logos.","s4, no text, no logos.",'
        '"s5, no text, no logos.","s6, no text, no logos."],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    mock_audit.assert_called_once()
    mock_rewrite.assert_called_once_with(
        'narration_script', 'Hecho con materiales reutilizados.', 'connotacion inferior', brand_dna,
    )
    assert result['narration_script'] == 'Hecho con upcycling.'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_does_not_rewrite_scene_prompts_when_flagged(brand_dna, _mock_brand_consistency_qc):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    mock_audit, mock_rewrite = _mock_brand_consistency_qc
    mock_audit.return_value = {'scene_prompts': 'inconsistente'}
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C","narration_script":"N",'
        '"scene_prompts":["s1, no text, no logos.","s2, no text, no logos.",'
        '"s3, no text, no logos.","s4, no text, no logos.",'
        '"s5, no text, no logos.","s6, no text, no logos."],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    mock_rewrite.assert_not_called()
    assert result['scene_prompts'][0] == 's1, no text, no logos.'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_skips_rewrite_when_audit_returns_no_issues(brand_dna, _mock_brand_consistency_qc):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    _, mock_rewrite = _mock_brand_consistency_qc
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C","narration_script":"N",'
        '"scene_prompts":["s1, no text, no logos.","s2, no text, no logos.",'
        '"s3, no text, no logos.","s4, no text, no logos.",'
        '"s5, no text, no logos.","s6, no text, no logos."],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        ReelScriptGenerator().generate(post_data, brand_dna)

    mock_rewrite.assert_not_called()

