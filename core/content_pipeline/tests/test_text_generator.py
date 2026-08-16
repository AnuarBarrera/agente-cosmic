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


@pytest.fixture(autouse=True)
def _mock_brand_consistency_qc():
    with patch('core.content_pipeline.generators.text_generator.audit_brand_consistency', return_value={}) as mock_audit, \
         patch('core.content_pipeline.generators.text_generator.rewrite_for_brand_consistency') as mock_rewrite:
        yield mock_audit, mock_rewrite


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


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_normalizes_hashtags_missing_hash_prefix(brand_dna):
    """Bug real detectado en prueba manual de Anuar (2026-08-16): Gemini a
    veces devuelve los hashtags como palabras sueltas sin '#' (ej.
    'hechoamano' en vez de '#hechoamano') -- nada en el pipeline lo
    agregaba, asi que se guardaban y renderizaban tal cual, sin '#'."""
    from core.content_pipeline.generators.text_generator import TextGenerator
    mock_response_without_hash = MOCK_VERTEX_RESPONSE.replace('"#disenoweb"', '"disenoweb"')
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(mock_response_without_hash)
        gen = TextGenerator()
        result = gen.generate(brand_dna)

    assert result[0]['hashtags'] == ['#disenoweb']


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_does_not_double_prefix_hashtags_that_already_have_hash(brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(brand_dna)

    assert result[0]['hashtags'] == ['#disenoweb']


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_tags_each_post_with_its_pillar(brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator, CONTENT_PILLARS
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(brand_dna)

    assert [p['pillar'] for p in result] == [pillar['name'] for pillar in CONTENT_PILLARS]


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_marks_only_carousel_day_as_carousel_format(brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator, CAROUSEL_DAY, REEL_DAY
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(brand_dna)

    formats = [p['format'] for p in result]
    assert formats.count('carousel') == 1
    assert formats[CAROUSEL_DAY - 1] == 'carousel'
    special_days = {CAROUSEL_DAY - 1, REEL_DAY - 1}
    assert all(f == 'single' for i, f in enumerate(formats) if i not in special_days)


def test_generate_marks_only_reel_day_as_reel_format(brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator, REEL_DAY
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(brand_dna)

    formats = [p['format'] for p in result]
    assert formats.count('reel') == 1
    assert formats[REEL_DAY - 1] == 'reel'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_prompt_includes_pillars_block(brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator, CONTENT_PILLARS
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_client = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        mock_vc.return_value = mock_client
        TextGenerator().generate(brand_dna)

    prompt_sent = mock_client.models.generate_content.call_args.kwargs['contents']
    for pillar in CONTENT_PILLARS:
        assert pillar['name'] in prompt_sent


@pytest.fixture
def sensitive_brand_dna():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://pediatra.com')
    return BrandDNA.objects.create(
        job=job, business_name='Pediatra Juan Gonzalez', business_url='https://pediatra.com',
        description='Atención pediátrica para niños de 0 a 12 años',
        keywords=['pediatria', 'salud infantil'],
        audience='Padres y tutores de niños', tone='profesional', primary_colors=['#1a1a2e'],
    )


def test_is_sensitive_niche_detects_health_and_children(sensitive_brand_dna):
    from core.content_pipeline.generators.text_generator import _is_sensitive_niche
    assert _is_sensitive_niche(sensitive_brand_dna) is True


def test_is_sensitive_niche_false_for_normal_business(brand_dna):
    from core.content_pipeline.generators.text_generator import _is_sensitive_niche
    assert _is_sensitive_niche(brand_dna) is False


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_skips_safety_qc_for_normal_business(brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc, \
         patch.object(TextGenerator, '_validate_caption_safety') as mock_qc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        gen.generate(brand_dna)

    mock_qc.assert_not_called()


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_runs_safety_qc_for_sensitive_niche(sensitive_brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc, \
         patch.object(TextGenerator, '_validate_caption_safety', return_value=True) as mock_qc, \
         patch.object(TextGenerator, '_regenerate_safe_caption') as mock_fix:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(sensitive_brand_dna)

    assert mock_qc.call_count == 7
    mock_fix.assert_not_called()
    assert len(result) == 7


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_regenerates_caption_that_fails_safety_qc(sensitive_brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc, \
         patch.object(TextGenerator, '_validate_caption_safety', side_effect=[False, True] + [True] * 6) as mock_qc, \
         patch.object(TextGenerator, '_regenerate_safe_caption', return_value='Version corregida sin promesas') as mock_fix:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(sensitive_brand_dna)

    mock_fix.assert_called_once()
    assert result[0]['caption'] == 'Version corregida sin promesas'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_gives_up_after_max_retries_and_keeps_last_caption(sensitive_brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc, \
         patch.object(TextGenerator, '_validate_caption_safety', return_value=False) as mock_qc, \
         patch.object(TextGenerator, '_regenerate_safe_caption', return_value='Sigue sin pasar QC') as mock_fix:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(sensitive_brand_dna, max_qc_retries=2)

    # 3 intentos (0,1,2) por cada uno de los 7 captions = 21 llamadas a QC
    assert mock_qc.call_count == 21
    assert result[0]['caption'] == 'Sigue sin pasar QC'


def test_pillars_day_3_4_7_match_rework_spec():
    from core.content_pipeline.generators.text_generator import CONTENT_PILLARS
    by_day = {p['day']: p for p in CONTENT_PILLARS}
    assert by_day[3]['name'] == 'Antes y despues'
    assert by_day[4]['name'] == 'Beneficio en profundidad'
    assert by_day[7]['name'] == 'Conexion emocional'
    assert len(CONTENT_PILLARS) == 7


@pytest.fixture
def brand_dna_no_url():
    job = AnalysisJob.objects.create(email='sinurl@t.com', business_url='')
    return BrandDNA.objects.create(
        job=job, business_name='Perro Rebelde', business_url='',
        description='Ropa para perros hecha de ropa reciclada',
        keywords=['ropa para perros', 'reciclaje'],
        audience='Dueños de mascotas', tone='casual', primary_colors=['#1a1a2e'],
    )


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_runs_safety_qc_when_no_business_url(brand_dna_no_url):
    from core.content_pipeline.generators.text_generator import TextGenerator
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc, \
         patch.object(TextGenerator, '_validate_caption_safety', return_value=True) as mock_qc, \
         patch.object(TextGenerator, '_regenerate_safe_caption') as mock_fix:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(brand_dna_no_url)

    assert mock_qc.call_count == 7
    mock_fix.assert_not_called()
    assert len(result) == 7


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_validate_caption_safety_rejects_website_mention_when_no_url(brand_dna_no_url):
    from core.content_pipeline.generators.text_generator import TextGenerator
    gen = TextGenerator()
    mock_resp_text = '{"has_absolute_promise": false, "has_unverifiable_claim": false, "has_website_mention": true, "ok": true}'
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(mock_resp_text)
        result = gen._validate_caption_safety('Visita nuestra pagina web', 'casual', 'Dueños de mascotas', '')
    assert result is False


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_validate_caption_safety_allows_website_mention_when_url_present(brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator
    gen = TextGenerator()
    mock_resp_text = '{"has_absolute_promise": false, "has_unverifiable_claim": false, "has_website_mention": true, "ok": true}'
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(mock_resp_text)
        result = gen._validate_caption_safety('Visita nuestra pagina web', 'profesional', 'PYMEs', 'https://tuwebmx.com')
    assert result is True


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_rewrites_only_flagged_caption(brand_dna, _mock_brand_consistency_qc):
    from core.content_pipeline.generators.text_generator import TextGenerator
    mock_audit, mock_rewrite = _mock_brand_consistency_qc
    mock_audit.return_value = {'post_2': 'connotacion inferior'}
    mock_rewrite.return_value = 'Caption corregida'
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(brand_dna)

    mock_audit.assert_called_once()
    mock_rewrite.assert_called_once_with('post_2', 'Post 3: tu marca online', 'connotacion inferior', brand_dna)
    assert result[2]['caption'] == 'Caption corregida'
    assert result[0]['caption'] == 'Post 1: diseno que convierte'
    assert result[1]['caption'] == 'Post 2: presencia digital'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_runs_brand_consistency_audit_for_normal_business(brand_dna, _mock_brand_consistency_qc):
    from core.content_pipeline.generators.text_generator import TextGenerator
    mock_audit, _ = _mock_brand_consistency_qc
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        gen.generate(brand_dna)

    mock_audit.assert_called_once()
    called_fields = mock_audit.call_args.args[0]
    assert len(called_fields) == 7


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_vertex_client_uses_global_text_location():
    with patch('core.content_pipeline.generators.text_generator.genai.Client') as mock_client:
        from core.content_pipeline.generators.text_generator import _vertex_client
        _vertex_client()
    mock_client.assert_called_once_with(vertexai=True, project='agente-cosmic', location='global')


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
                    VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash')
def test_caption_safety_call_disables_thinking(brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator
    gen = TextGenerator()
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_resp = MagicMock()
        mock_resp.text = '{"ok": true, "has_absolute_promise": false, "has_unverifiable_claim": false, "has_website_mention": false}'
        mock_vc.return_value.models.generate_content.return_value = mock_resp
        gen._validate_caption_safety('Caption de prueba', 'casual', 'Todos', '')
        call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
    assert call_kwargs['config'].thinking_config.thinking_budget == 0
