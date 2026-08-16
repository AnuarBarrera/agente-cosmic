import io
from unittest.mock import patch, MagicMock
from django.test import override_settings
from PIL import Image


# ---- Helpers ----

def _png_bytes(color=(30, 30, 60), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='PNG')
    return buf.getvalue()


# ---- Existing tests (updated mocks) ----

@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
)
def test_generate_returns_url():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    with patch.object(gen, '_layered_pipeline', return_value=b'fake-png-bytes'), \
         patch.object(gen, '_upload_to_storage', return_value='https://storage.googleapis.com/test/img.jpg'):
        url = gen.generate(
            caption='Diseno web profesional',
            colors=['#1a1a2e'],
            tone='profesional',
            filename='test-img',
        )
    assert url.startswith('https://')


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='global',
    VERTEX_IMAGE_MODEL='gemini-3.1-flash-image',
)
def test_generate_with_vertex_includes_negative_prompt_text_and_forces_square():
    from core.content_pipeline.generators.image_generator import ImageGenerator, _IMAGE_NEGATIVE_PROMPT
    gen = ImageGenerator(bucket_name='test-bucket')
    mock_client = MagicMock()
    mock_part = MagicMock()
    mock_part.inline_data.data = b'fake-png'
    mock_client.models.generate_content.return_value = MagicMock(
        candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
    )
    with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client):
        result = gen._generate_with_vertex('a test prompt')

    assert result == b'fake-png'
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    # Gemini no tiene negative_prompt estructurado -- se dobla en el texto (decision
    # 2026-08-07, ver spec de migracion).
    assert _IMAGE_NEGATIVE_PROMPT in call_kwargs['contents']
    assert call_kwargs['config'].image_config.aspect_ratio == '1:1'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='global',
    VERTEX_IMAGE_MODEL='gemini-3.1-flash-image',
)
def test_generate_with_vertex_records_image_cost_not_token_cost():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    mock_client = MagicMock()
    mock_part = MagicMock()
    mock_part.inline_data.data = b'fake-png'
    mock_client.models.generate_content.return_value = MagicMock(
        candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
    )
    with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client), \
         patch('core.content_pipeline.generators.image_generator.record_gemini_image_generation') as mock_record, \
         patch('core.content_pipeline.generators.image_generator.record_tokens') as mock_tokens:
        gen._generate_with_vertex('a test prompt')

    mock_record.assert_called_once_with('generate')
    mock_tokens.assert_not_called()


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='global',
    VERTEX_IMAGE_MODEL='gemini-3.1-flash-image',
)
def test_generate_with_vertex_raises_value_error_when_no_image_parts():
    """Reproduce bug real (2026-08-16): cuando Gemini bloquea la respuesta
    (seguridad/politica de contenido) devuelve 200 OK pero con
    candidates[0].content.parts=None. Sin guard, iterar sobre eso crashea con
    TypeError('NoneType' object is not iterable) sin control -- un ValueError
    limpio es lo que _generate_background ya sabe atrapar y reintentar con un
    prompt de respaldo (ver _generate_background, except ValueError)."""
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = MagicMock(
        candidates=[MagicMock(content=MagicMock(parts=None), finish_reason='SAFETY')]
    )
    with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client):
        try:
            gen._generate_with_vertex('a test prompt')
            assert False, "esperaba ValueError, no crasheo"
        except ValueError:
            pass
        except TypeError:
            assert False, "crasheo con TypeError sin control en vez de un ValueError limpio"


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='global',
    VERTEX_IMAGE_MODEL='gemini-3.1-flash-image',
    GEMINI_API_KEY='fake-api-key',
)
def test_generate_with_vertex_uses_gemini_api_client_when_paid_and_omits_labels():
    """Plan pagado (use_gemini_api=True) -- decision de Anuar 2026-08-14 de
    separar el gasto real de usuarios pagos (Gemini API) de los creditos de
    GCP del trial gratis (Vertex). labels= es billing export de Vertex/
    BigQuery, sin equivalente en Gemini API -- no debe mandarse ahi."""
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket', use_gemini_api=True)
    mock_client = MagicMock()
    mock_part = MagicMock()
    mock_part.inline_data.data = b'fake-png'
    mock_client.models.generate_content.return_value = MagicMock(
        candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
    )
    with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vertex, \
         patch('core.content_pipeline.generators.image_generator._gemini_api_client', return_value=mock_client) as mock_gemini_api:
        result = gen._generate_with_vertex('a test prompt')

    assert result == b'fake-png'
    mock_gemini_api.assert_called_once()
    mock_vertex.assert_not_called()
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    assert call_kwargs['config'].labels is None


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
)
def test_generate_derives_font_seed_from_filename_without_day_suffix():
    """Las 7 imagenes de una semana comparten job_id en el filename (job-day1..7) —
    el seed de fuente debe ser el mismo para todas, sin importar el dia."""
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    with patch.object(gen, '_layered_pipeline', return_value=b'fake-png-bytes') as mock_pipeline, \
         patch.object(gen, '_upload_to_storage', return_value='https://storage.googleapis.com/test/img.jpg'):
        gen.generate(
            caption='Diseno web profesional',
            colors=['#1a1a2e'],
            tone='profesional',
            filename='job-abc-day3',
        )
    assert mock_pipeline.call_args.kwargs['font_seed'] == 'job-abc'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
)
def test_generate_returns_fallback_on_error():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    with patch.object(gen, '_layered_pipeline', side_effect=Exception('Pipeline error')):
        url = gen.generate(
            caption='Diseno web profesional',
            colors=['#1a1a2e'],
            tone='profesional',
            filename='test-img',
        )
    assert url == ''


# ---- New tests ----

class TestExtractHeadline:
    def test_returns_words_without_trailing_connectors(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        headline = gen._extract_headline(
            "Descubre el sabor auténtico de nuestra panadería artesanal #panaderia #food"
        )
        # Trailing connector "de" is stripped — result is a grammatically clean phrase
        assert headline == "Descubre el sabor auténtico"
        assert not headline.endswith(' de')

    def test_skips_hashtags(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        headline = gen._extract_headline("#promo #sale Gran oferta hoy en tu tienda favorita")
        assert '#' not in headline

    def test_short_caption(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        assert len(gen._extract_headline("Hola mundo")) > 0



class TestGeneratePostContent:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_all_required_keys_on_fallback(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._generate_post_content('Tu negocio necesita una web profesional')
        assert set(result.keys()) == {'headline', 'subtitle', 'cta', 'tag'}
        assert len(result['headline']) > 0
        assert len(result['cta']) > 0
        assert len(result['tag']) > 0

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_system_instruction_forbids_absolute_promise_words(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"H","subtitle":"S","cta":"C","tag":"T"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._generate_post_content('Atencion pediatrica experta')
            _, kwargs = mock_vc.return_value.models.generate_content.call_args
            system_instruction = kwargs['config'].system_instruction
        for word in ('garantizado', 'asegurar', 'aseguramos'):
            assert word in system_instruction

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_parses_valid_gemini_response(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"Web en 48h","subtitle":"Sitio profesional listo en dos días","cta":"Empieza hoy","tag":"DISEÑO WEB"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Diseño web profesional para tu empresa')
        assert result['headline'] == 'Web en 48h'
        assert result['subtitle'] == 'Sitio profesional listo en dos días'
        assert result['cta'] == 'Empieza hoy'
        assert result['tag'] == 'DISEÑO WEB'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_tag_is_uppercased(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"Impulsa tu marca","subtitle":"Resultados reales y medibles","cta":"Ver más","tag":"marketing digital"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Marketing digital que convierte')
        assert result['tag'] == 'MARKETING DIGITAL'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_retries_on_429_and_succeeds(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_resp = MagicMock()
        mock_resp.text = '{"headline":"H","subtitle":"S","cta":"C","tag":"T"}'
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc, \
             patch('core.shared.rate_limiter.time.sleep'):
            mock_vc.return_value.models.generate_content.side_effect = [
                Exception('429 Resource exhausted'), mock_resp,
            ]
            result = gen._generate_post_content('Caption de prueba')
        assert result['headline'] == 'H'
        assert mock_vc.return_value.models.generate_content.call_count == 2

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fallback_subtitle_truncates_at_word_boundary(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        long_caption = 'palabra ' * 20
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._generate_post_content(long_caption)
        subtitle = result['subtitle']
        assert subtitle.endswith('…')
        without_ellipsis = subtitle[:-1]
        assert long_caption.startswith(without_ellipsis)
        assert long_caption[len(without_ellipsis)] == ' '

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fallback_subtitle_not_truncated_when_short(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._generate_post_content('Caption corto')
        assert result['subtitle'] == 'Caption corto'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_sanitizes_cta_when_no_business_url(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"H","subtitle":"S","cta":"Visita nuestra web","tag":"T"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Caption de prueba', business_url='')
        assert result['cta'] == 'Contáctanos hoy'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_keeps_cta_when_business_url_present(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"H","subtitle":"S","cta":"Visita nuestra web","tag":"T"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Caption de prueba', business_url='https://ejemplo.com')
        assert result['cta'] == 'Visita nuestra web'


class TestRenderHtmlTemplate:
    def _make_mock_playwright(self, screenshot_bytes: bytes):
        """Helper: builds a mock sync_playwright context that returns screenshot_bytes."""
        mock_page = MagicMock()
        mock_page.screenshot.return_value = screenshot_bytes
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw_ctx = MagicMock()
        mock_pw_ctx.chromium.launch.return_value = mock_browser
        mock_pw = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw_ctx)
        mock_pw.__exit__ = MagicMock(return_value=False)
        return mock_pw, mock_page

    def test_returns_screenshot_bytes(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        content = {'headline': 'Web profesional', 'subtitle': 'Tu negocio en línea', 'cta': 'Ver más', 'tag': 'DISEÑO WEB'}
        mock_pw, _ = self._make_mock_playwright(fake_shot)

        with patch('core.content_pipeline.generators.image_generator.sync_playwright', return_value=mock_pw), \
             patch.object(ImageGenerator, '_choose_template_for_image', return_value='instagram_post.html'):
            result = gen._render_html_template(fake_bg, content, ['#e94560'])

        assert result == fake_shot

    def test_injects_button_color_into_html(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator, _pick_button_color
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        content = {'headline': 'Título', 'subtitle': 'Subtítulo', 'cta': 'Empieza', 'tag': 'TEST'}
        mock_pw, mock_page = self._make_mock_playwright(fake_shot)

        colors = ['#ff5500']
        with patch('core.content_pipeline.generators.image_generator.sync_playwright', return_value=mock_pw), \
             patch.object(ImageGenerator, '_choose_template_for_image', return_value='instagram_post.html'):
            gen._render_html_template(fake_bg, content, colors)

        html_arg = mock_page.set_content.call_args[0][0]
        expected_color = _pick_button_color(colors)
        assert expected_color in html_arg

    def test_uses_fallback_button_color_when_no_colors(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator, _pick_button_color
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        content = {'headline': 'Título', 'subtitle': 'Subtítulo', 'cta': 'Empieza', 'tag': 'TEST'}
        mock_pw, mock_page = self._make_mock_playwright(fake_shot)

        with patch('core.content_pipeline.generators.image_generator.sync_playwright', return_value=mock_pw), \
             patch.object(ImageGenerator, '_choose_template_for_image', return_value='instagram_post.html'):
            gen._render_html_template(fake_bg, content, [])

        html_arg = mock_page.set_content.call_args[0][0]
        fallback = _pick_button_color([])
        assert fallback in html_arg

    def test_uses_color_pool_for_primary_color_when_no_colors(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator, _FALLBACK_COLOR_POOL
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        content = {'headline': 'Título', 'subtitle': 'Subtítulo', 'cta': 'Empieza', 'tag': 'TEST'}
        mock_pw, mock_page = self._make_mock_playwright(fake_shot)

        with patch('core.content_pipeline.generators.image_generator.sync_playwright', return_value=mock_pw), \
             patch.object(ImageGenerator, '_choose_template_for_image', return_value='reel_cta.html'), \
             patch('core.content_pipeline.generators.image_generator.random.choice', return_value='#3ED694') as mock_choice:
            gen._render_html_template(fake_bg, content, [], font_seed='test')

        mock_choice.assert_called_once_with(_FALLBACK_COLOR_POOL)
        html_arg = mock_page.set_content.call_args[0][0]
        assert '#3ED694' in html_arg


    def test_injects_font_family_into_html(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator, _choose_font_preset
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        content = {'headline': 'Título', 'subtitle': 'Subtítulo', 'cta': 'Empieza', 'tag': 'TEST'}
        mock_pw, mock_page = self._make_mock_playwright(fake_shot)

        with patch('core.content_pipeline.generators.image_generator.sync_playwright', return_value=mock_pw), \
             patch.object(ImageGenerator, '_choose_template_for_image', return_value='instagram_post.html'):
            gen._render_html_template(fake_bg, content, ['#e94560'], font_seed='job-abc')

        html_arg = mock_page.set_content.call_args[0][0]
        expected = _choose_font_preset('job-abc')
        assert expected['font_family'] in html_arg
        assert expected['font_import'] in html_arg
        assert '{{font_family}}' not in html_arg
        assert '{{font_import}}' not in html_arg

    def test_waits_for_fonts_ready_before_screenshot(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        content = {'headline': 'Título', 'subtitle': 'Subtítulo', 'cta': 'Empieza', 'tag': 'TEST'}
        mock_pw, mock_page = self._make_mock_playwright(fake_shot)

        with patch('core.content_pipeline.generators.image_generator.sync_playwright', return_value=mock_pw), \
             patch.object(ImageGenerator, '_choose_template_for_image', return_value='instagram_post.html'):
            gen._render_html_template(fake_bg, content, ['#e94560'])

        mock_page.evaluate.assert_called_once_with('document.fonts.ready')


class TestChooseFontPreset:
    def test_same_seed_always_returns_same_preset(self):
        from core.content_pipeline.generators.image_generator import _choose_font_preset
        first = _choose_font_preset('job-123')
        second = _choose_font_preset('job-123')
        assert first == second

    def test_empty_seed_does_not_raise(self):
        from core.content_pipeline.generators.image_generator import _choose_font_preset, _FONT_PRESETS
        result = _choose_font_preset('')
        assert result in _FONT_PRESETS

    def test_different_seeds_can_return_different_presets(self):
        from core.content_pipeline.generators.image_generator import _choose_font_preset
        seeds = [f'job-{i}' for i in range(20)]
        results = {_choose_font_preset(s)['font_family'] for s in seeds}
        assert len(results) > 1


class TestLayeredPipeline:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_pipeline_calls_render_html_template(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes((30, 30, 60))
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))
        fake_content = {'headline': 'Web en 48h', 'subtitle': 'Tu negocio online', 'cta': 'Empieza', 'tag': 'DISEÑO WEB'}

        with patch.object(gen, '_generate_background', return_value=fake_bg), \
             patch.object(gen, '_generate_post_content', return_value=fake_content), \
             patch.object(gen, '_render_html_template', return_value=fake_shot) as mock_render:
            result = gen._layered_pipeline('Caption de prueba', ['#1a1a2e'], 'profesional')

        mock_render.assert_called_once_with(fake_bg, fake_content, ['#1a1a2e'], svg_overlay='', font_seed='')
        assert result == fake_shot

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_skips_generate_background_when_background_bytes_given(self):
        """El camino de foto real de producto (generate_from_product_photo/
        regenerate_with_reference) pasa un fondo ya editado por nano banana --
        _layered_pipeline NO debe pisarlo generando uno nuevo desde cero."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        given_bg = _png_bytes((10, 20, 30))
        fake_content = {'headline': 'Hola', 'subtitle': 'Mundo', 'cta': 'Ver', 'tag': 'TEST'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_background') as mock_bg, \
             patch.object(gen, '_generate_post_content', return_value=fake_content), \
             patch.object(gen, '_render_html_template', return_value=fake_shot) as mock_render:
            result = gen._layered_pipeline('Caption', ['#1a1a2e'], 'profesional', background_bytes=given_bg)

        mock_bg.assert_not_called()
        mock_render.assert_called_once_with(given_bg, fake_content, ['#1a1a2e'], svg_overlay='', font_seed='')
        assert result == fake_shot

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_calls_generate_background_when_background_bytes_not_given(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes((30, 30, 60))
        fake_content = {'headline': 'Hola', 'subtitle': 'Mundo', 'cta': 'Ver', 'tag': 'TEST'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_background', return_value=fake_bg) as mock_bg, \
             patch.object(gen, '_generate_post_content', return_value=fake_content), \
             patch.object(gen, '_render_html_template', return_value=fake_shot) as mock_render:
            gen._layered_pipeline('Caption', ['#1a1a2e'], 'profesional')

        mock_bg.assert_called_once()
        mock_render.assert_called_once_with(fake_bg, fake_content, ['#1a1a2e'], svg_overlay='', font_seed='')

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_pipeline_propagates_render_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_content = {'headline': 'Hola', 'subtitle': 'Mundo', 'cta': 'Ver', 'tag': 'TEST'}

        with patch.object(gen, '_generate_background', return_value=fake_bg), \
             patch.object(gen, '_generate_post_content', return_value=fake_content), \
             patch.object(gen, '_render_html_template', side_effect=Exception('Playwright error')):
            import pytest
            with pytest.raises(Exception, match='Playwright error'):
                gen._layered_pipeline('Caption', ['#1a1a2e'], 'profesional')

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_generates_brand_context_and_calls_post_content(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes((30, 30, 60))
        fake_content = {'headline': 'Hola mundo', 'subtitle': 'Mundo', 'cta': 'Ver', 'tag': 'TEST'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_background', return_value=fake_bg) as mock_bg, \
             patch.object(gen, '_generate_post_content', return_value=fake_content) as mock_content, \
             patch.object(gen, '_render_html_template', return_value=fake_shot):
            gen._layered_pipeline('Caption', ['#1a1a2e'], 'profesional')

        mock_bg.assert_called_once()
        mock_content.assert_called_once_with('Caption', brand_context='Tono: profesional.', business_url='')


class TestValidateBackground:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_when_image_ok(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "ok": true}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_background(b'fake-png')
        assert result is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_has_text(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": false, "ok": false}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_background(b'fake-png')
        assert result is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_on_api_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._validate_background(b'fake-png')
        assert result is True  # don't block pipeline on QC error

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_has_malformed_object(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, '
                '"has_malformed_object": true, "ok": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_background(b'fake-png')
        assert result is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_has_unrealistic_grounding(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, '
                '"has_malformed_object": false, "has_unrealistic_grounding": true, "ok": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_background(b'fake-png')
        assert result is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_suggestive_content_detected(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, '
                '"has_malformed_object": false, "has_unrealistic_grounding": false, '
                '"has_suggestive_or_exposed_content": true, "ok": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_background(b'fake-png')
        assert result is False


class TestAnalyzeBrandScene:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_gemini_prompt_avoids_literal_product_depiction(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"mode": "lifestyle", "prompt": "A cozy scene"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._analyze_brand_scene('Caption', ['keyword'], 'Descripcion', 'profesional', ['#1a1a2e'])
        call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
        gemini_prompt = call_kwargs['contents']
        assert 'focus on the product/food/objects only' not in gemini_prompt
        assert "DO NOT attempt to depict this business's exact product design" in gemini_prompt
        assert 'focus on how a customer FEELS' in gemini_prompt

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fallback_prompt_does_not_promise_literal_product_focus(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API down')
            scene_prompt, product_mode = gen._analyze_brand_scene(
                'Caption', ['keyword'], 'Descripcion', 'profesional', ['#1a1a2e'], audience='niños'
            )
        assert product_mode is True
        assert 'Focus on the product itself.' not in scene_prompt
        assert 'artful' not in scene_prompt.lower()
        assert 'Generic/abstract representation only' in scene_prompt


class TestChooseTemplateForImage:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_maps_bottom_zone_to_lower_third_template(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"safe_zone": "bottom"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._choose_template_for_image(b'fake-png')
        assert result == 'instagram_post.html'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_maps_top_zone_to_upper_third_template(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"safe_zone": "top"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._choose_template_for_image(b'fake-png')
        assert result == 'instagram_post_top.html'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_maps_center_zone_to_centered_template(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"safe_zone": "center"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._choose_template_for_image(b'fake-png')
        assert result == 'instagram_post_center.html'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_falls_back_to_random_on_api_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._choose_template_for_image(b'fake-png')
        assert result in gen._TEMPLATES

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_falls_back_to_random_on_invalid_zone(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"safe_zone": "diagonal"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._choose_template_for_image(b'fake-png')
        assert result in gen._TEMPLATES



class TestGenerateBackgroundQC:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_retries_until_valid_image(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        bad_img = _png_bytes((200, 50, 50))
        good_img = _png_bytes((50, 200, 50))
        with patch.object(gen, '_generate_with_retry', side_effect=[bad_img, bad_img, good_img]), \
             patch.object(gen, '_validate_background', side_effect=[False, False, True]):
            result = gen._generate_background('Caption de prueba', ['#1a1a2e'], 'profesional')
        assert result == good_img

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_last_image_when_all_retries_fail(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        img1 = _png_bytes((200, 50, 50))
        img2 = _png_bytes((50, 50, 200))
        img3 = _png_bytes((50, 200, 200))
        with patch.object(gen, '_generate_with_retry', side_effect=[img1, img2, img3]), \
             patch.object(gen, '_validate_background', return_value=False):
            result = gen._generate_background('Caption', ['#1a1a2e'], 'profesional')
        assert result == img3  # last attempt returned even if rejected



class TestValidateFinalImage:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_when_image_is_clean(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_background_text": false, "has_shadow_artifacts": false, "plain_white_background": false, "ok": true}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_final_image(_png_bytes())
        assert result is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_shadow_artifacts_detected(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_background_text": false, "has_shadow_artifacts": true, "plain_white_background": false, "ok": false}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_final_image(_png_bytes())
        assert result is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_background_text_detected(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_background_text": true, "has_shadow_artifacts": false, "plain_white_background": false, "ok": false}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_final_image(_png_bytes())
        assert result is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_plain_white_background(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_background_text": false, "has_shadow_artifacts": false, "plain_white_background": true, "ok": false}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_final_image(_png_bytes())
        assert result is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_on_api_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._validate_final_image(_png_bytes())
        assert result is True  # no bloquear pipeline si QC falla


class TestUploadToStorage:
    def test_appends_cache_busting_query_param(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_blob = MagicMock()
        mock_blob.public_url = 'https://storage.googleapis.com/test-bucket/posts/job1-day3.png'
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        with patch('core.content_pipeline.generators.image_generator.storage.Client', return_value=mock_client):
            url = gen._upload_to_storage(b'fake-bytes', 'job1-day3')
        mock_blob.upload_from_string.assert_called_once_with(b'fake-bytes', content_type='image/png')
        assert url.startswith('https://storage.googleapis.com/test-bucket/posts/job1-day3.png?v=')

    def test_reupload_produces_different_url(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_blob = MagicMock()
        mock_blob.public_url = 'https://storage.googleapis.com/test-bucket/posts/job1-day3.png'
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        with patch('core.content_pipeline.generators.image_generator.storage.Client', return_value=mock_client), \
             patch('core.content_pipeline.generators.image_generator.time.time', side_effect=[1000, 2000]):
            url1 = gen._upload_to_storage(b'fake-bytes-v1', 'job1-day3')
            url2 = gen._upload_to_storage(b'fake-bytes-v2', 'job1-day3')
        assert url1 != url2


class TestGenerateCarouselSlidesContent:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_num_slides_items_on_fallback(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            slides = gen._generate_carousel_slides_content('Nuestros clientes confian en nosotros', num_slides=4)
        assert len(slides) == 4
        for slide in slides:
            assert set(slide.keys()) == {'headline', 'subtitle', 'cta', 'tag'}

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_only_last_slide_has_real_cta_on_fallback(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            slides = gen._generate_carousel_slides_content('Nuestros clientes confian en nosotros', num_slides=3)
        assert slides[-1]['cta'] == 'Contáctanos hoy'
        assert all(s['cta'] == 'Desliza para ver más' for s in slides[:-1])

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_parses_valid_gemini_response_in_order(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '[{"headline":"El problema","subtitle":"Antes batallaban","cta":"Desliza","tag":"TESTIMONIO"},'
                '{"headline":"La solucion","subtitle":"Encontraron su respuesta","cta":"Desliza","tag":"TESTIMONIO"},'
                '{"headline":"El resultado","subtitle":"Hoy estan felices","cta":"Contáctanos hoy","tag":"TESTIMONIO"}]'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            slides = gen._generate_carousel_slides_content('Caption de testimonio', num_slides=3)
        assert [s['headline'] for s in slides] == ['El problema', 'La solucion', 'El resultado']
        assert slides[-1]['cta'] == 'Contáctanos hoy'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fills_missing_items_with_fallback_when_gemini_returns_fewer(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '[{"headline":"Unica slide","subtitle":"Sub","cta":"Desliza","tag":"TESTIMONIO"}]'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            slides = gen._generate_carousel_slides_content('Caption', num_slides=4)
        assert len(slides) == 4
        assert slides[0]['headline'] == 'Unica slide'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fallback_uses_transformacion_tag_and_headlines(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            slides = gen._generate_carousel_slides_content('Nuestro servicio ayuda a resolver X', num_slides=3)
        assert all(s['tag'] == 'TRANSFORMACION' for s in slides)
        assert slides[0]['headline'] == 'Antes y despues 1'
        assert slides[1]['headline'] == 'Antes y despues 2'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_prompt_does_not_mention_prueba_social_or_testimonio(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '[{"headline":"H","subtitle":"S","cta":"Desliza","tag":"TAG"}]'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._generate_carousel_slides_content('Caption', num_slides=1)
        prompt_sent = mock_vc.return_value.models.generate_content.call_args.kwargs['contents'].lower()
        assert 'prueba social' not in prompt_sent
        assert 'testimonio' not in prompt_sent
        assert 'un cliente nos comento' not in prompt_sent
        assert 'problema' in prompt_sent
        assert 'beneficio' in prompt_sent

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_retries_on_429_and_succeeds(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_resp = MagicMock()
        mock_resp.text = '[{"headline":"H","subtitle":"S","cta":"Desliza","tag":"TAG"}]'
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc, \
             patch('core.shared.rate_limiter.time.sleep'):
            mock_vc.return_value.models.generate_content.side_effect = [
                Exception('429 Resource exhausted'), mock_resp,
            ]
            slides = gen._generate_carousel_slides_content('Caption', num_slides=1)
        assert slides[0]['headline'] == 'H'
        assert mock_vc.return_value.models.generate_content.call_count == 2

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fallback_subtitle_truncates_at_word_boundary(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        long_caption = 'palabra ' * 20
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            slides = gen._generate_carousel_slides_content(long_caption, num_slides=1)
        subtitle = slides[0]['subtitle']
        assert subtitle.endswith('…')
        without_ellipsis = subtitle[:-1]
        assert long_caption.startswith(without_ellipsis)
        assert long_caption[len(without_ellipsis)] == ' '

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_sanitizes_cta_when_no_business_url(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '[{"headline":"H","subtitle":"S","cta":"Visita nuestra pagina web","tag":"T"}]'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            slides = gen._generate_carousel_slides_content('Caption', num_slides=1, business_url='')
        assert slides[0]['cta'] == 'Contáctanos hoy'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_keeps_cta_when_business_url_present(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch("core.content_pipeline.generators.image_generator._vertex_text_client") as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '[{"headline":"H","subtitle":"S","cta":"Visita nuestra pagina web","tag":"T"}]'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            slides = gen._generate_carousel_slides_content('Caption', num_slides=1, business_url='https://ejemplo.com')
        assert slides[0]['cta'] == 'Visita nuestra pagina web'


class TestGenerateCarousel:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_one_url_per_slide(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        fake_slides = [
            {'headline': f'H{i}', 'subtitle': 'S', 'cta': 'CTA', 'tag': 'TAG'} for i in range(4)
        ]
        with patch.object(gen, '_generate_background', return_value=fake_bg), \
             patch.object(gen, '_generate_carousel_slides_content', return_value=fake_slides), \
             patch.object(gen, '_render_html_template', return_value=fake_shot), \
             patch.object(gen, '_upload_to_storage', side_effect=[f'https://storage.test/slide{i}.png' for i in range(4)]) as mock_upload:
            urls = gen.generate_carousel('Caption', ['#1a1a2e'], 'profesional', 'job1-day3', num_slides=4)
        assert len(urls) == 4
        assert urls == [f'https://storage.test/slide{i}.png' for i in range(4)]
        uploaded_filenames = [call.args[1] for call in mock_upload.call_args_list]
        assert uploaded_filenames == ['job1-day3-slide1', 'job1-day3-slide2', 'job1-day3-slide3', 'job1-day3-slide4']

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_generates_background_only_once_for_all_slides(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        fake_slides = [{'headline': f'H{i}', 'subtitle': 'S', 'cta': 'CTA', 'tag': 'TAG'} for i in range(4)]
        with patch.object(gen, '_generate_background', return_value=fake_bg) as mock_bg, \
             patch.object(gen, '_generate_carousel_slides_content', return_value=fake_slides), \
             patch.object(gen, '_render_html_template', return_value=fake_shot) as mock_render, \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/slide.png'):
            gen.generate_carousel('Caption', ['#1a1a2e'], 'profesional', 'job1-day3', num_slides=4)
        mock_bg.assert_called_once()
        assert mock_render.call_count == 4
        for call in mock_render.call_args_list:
            assert call.args[0] == fake_bg

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_returns_empty_list_on_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_background', side_effect=Exception('Background error')):
            urls = gen.generate_carousel('Caption', ['#1a1a2e'], 'profesional', 'job1-day3')
        assert urls == []

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_derives_font_seed_from_filename_without_day_suffix(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        fake_slides = [{'headline': 'H', 'subtitle': 'S', 'cta': 'CTA', 'tag': 'TAG'}]
        with patch.object(gen, '_generate_background', return_value=fake_bg), \
             patch.object(gen, '_generate_carousel_slides_content', return_value=fake_slides), \
             patch.object(gen, '_render_html_template', return_value=fake_shot) as mock_render, \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/slide.png'):
            gen.generate_carousel('Caption', ['#1a1a2e'], 'profesional', 'job-abc-day3', num_slides=1)
        assert mock_render.call_args.kwargs['font_seed'] == 'job-abc'


class TestSanitizeWebVisitMention:
    def test_no_url_and_mentions_website_returns_fallback(self):
        from core.content_pipeline.generators.image_generator import _sanitize_web_visit_mention
        result = _sanitize_web_visit_mention('Visita nuestra web hoy', '', 'Contáctanos hoy')
        assert result == 'Contáctanos hoy'

    def test_no_url_and_no_mention_returns_original(self):
        from core.content_pipeline.generators.image_generator import _sanitize_web_visit_mention
        result = _sanitize_web_visit_mention('Compra ahora', '', 'Contáctanos hoy')
        assert result == 'Compra ahora'

    def test_has_url_and_mentions_website_returns_original(self):
        from core.content_pipeline.generators.image_generator import _sanitize_web_visit_mention
        result = _sanitize_web_visit_mention('Visita nuestra web hoy', 'https://ejemplo.com', 'Contáctanos hoy')
        assert result == 'Visita nuestra web hoy'


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
                    GOOGLE_CLOUD_LOCATION='us-central1')
def test_vertex_text_client_uses_global_location():
    from unittest.mock import patch
    with patch('core.content_pipeline.generators.image_generator.genai.Client') as mock_client:
        from core.content_pipeline.generators.image_generator import _vertex_text_client, _vertex_client
        _vertex_text_client()
        _vertex_client()
    calls = mock_client.call_args_list
    assert calls[0].kwargs == {'vertexai': True, 'project': 'agente-cosmic', 'location': 'global'}
    assert calls[1].kwargs == {'vertexai': True, 'project': 'agente-cosmic', 'location': 'us-central1'}


class TestValidateBackgroundThinking:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash',
    )
    def test_validate_background_disables_thinking(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._validate_background(b'fake-png')
            call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
        assert call_kwargs['config'].thinking_config.thinking_budget == 0


class TestValidateFinalImageThinking:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash',
    )
    def test_validate_final_image_disables_thinking(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_background_text": false, "has_shadow_artifacts": false, "plain_white_background": false, "ok": true}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._validate_final_image(_png_bytes())
            call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
        assert call_kwargs['config'].thinking_config.thinking_budget == 0


class TestChooseTemplateForImageThinking:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash',
    )
    def test_choose_template_for_image_disables_thinking(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"safe_zone": "bottom"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._choose_template_for_image(b'fake-png')
            call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
        assert call_kwargs['config'].thinking_config.thinking_budget == 0


class TestValidateProductPhotoGeneration:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_passes_when_no_text(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = (
            '{"has_text": false, "text_is_correct_spanish": true, "is_abstract_3d": false, '
            '"has_screen_content": false, "has_malformed_object": false, '
            '"has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'
        )
        mock_client.models.generate_content.return_value = mock_resp
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', return_value=mock_client):
            assert gen._validate_product_photo_generation(b'fake-png') is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_passes_when_text_is_correct_spanish(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = (
            '{"has_text": true, "text_is_correct_spanish": true, "is_abstract_3d": false, '
            '"has_screen_content": false, "has_malformed_object": false, '
            '"has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'
        )
        mock_client.models.generate_content.return_value = mock_resp
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', return_value=mock_client):
            assert gen._validate_product_photo_generation(b'fake-png') is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_asks_gemini_for_the_lenient_text_rule_not_the_strict_one(self):
        """El criterio de texto de este auditor (rechazar solo si esta MAL escrito,
        no por su sola presencia) vive en el prompt y en el response_schema — no en
        codigo Python. Sin estas aserciones, volver a la regla estricta de
        _validate_background, o pasar ImageQCSchema por error, dejaria la suite en
        verde igual."""
        from core.content_pipeline.generators.image_generator import (
            ImageGenerator, ProductPhotoQCSchema,
        )
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = (
            '{"has_text": true, "text_is_correct_spanish": true, "is_abstract_3d": false, '
            '"has_screen_content": false, "has_malformed_object": false, '
            '"has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'
        )
        mock_client.models.generate_content.return_value = mock_resp
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', return_value=mock_client):
            gen._validate_product_photo_generation(b'fake-png')

        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert call_kwargs['config'].response_schema is ProductPhotoQCSchema
        prompt = next(c for c in call_kwargs['contents'] if isinstance(c, str))
        # La regla laxa: el texto presente solo descalifica si esta mal escrito.
        assert '(has_text=false OR text_is_correct_spanish=true)' in prompt
        # Y explicitamente NO la regla estricta de _validate_background.
        assert 'ok: true ONLY if has_text=false' not in prompt

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_asks_gemini_to_not_penalize_visual_distortion_of_original_text(self):
        """Hallazgo real (2026-08-16, Anuar): la foto de origen a veces ya trae
        texto propio del producto (ej. un globo con "Feliz Cumpleanos" impreso).
        La edicion de nano banana puede dejar ese texto borroso/recortado/
        deformado sin que las palabras en si sean invalidas -- el juez de QC
        confundia esa distorsion visual con texto mal formado y rechazaba de
        mas. El prompt debe instruir explicitamente a no penalizar la
        distorsion visual, solo el contenido de las palabras."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = (
            '{"has_text": true, "text_is_correct_spanish": true, "is_abstract_3d": false, '
            '"has_screen_content": false, "has_malformed_object": false, '
            '"has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'
        )
        mock_client.models.generate_content.return_value = mock_resp
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', return_value=mock_client):
            gen._validate_product_photo_generation(b'fake-png')

        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        prompt = next(c for c in call_kwargs['contents'] if isinstance(c, str))
        assert 'visual imperfection alone is NOT an error' in prompt
        assert 'ORIGINAL text that already existed on the product' in prompt

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_rejects_when_text_is_incorrect_spanish(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = (
            '{"has_text": true, "text_is_correct_spanish": false, "is_abstract_3d": false, '
            '"has_screen_content": false, "has_malformed_object": false, '
            '"has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": false}'
        )
        mock_client.models.generate_content.return_value = mock_resp
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', return_value=mock_client):
            assert gen._validate_product_photo_generation(b'fake-png') is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_does_not_trust_the_models_ok_field_when_inconsistent(self):
        """El veredicto se deriva en Python de los 7 booleanos. Si el modelo se
        contradice (ok=true con is_abstract_3d=true — la condicion tiene un OR
        anidado, la forma que un LLM compone peor), el QC debe rechazar igual."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = (
            '{"has_text": false, "text_is_correct_spanish": true, "is_abstract_3d": true, '
            '"has_screen_content": false, "has_malformed_object": false, '
            '"has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'
        )
        mock_client.models.generate_content.return_value = mock_resp
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', return_value=mock_client):
            assert gen._validate_product_photo_generation(b'fake-png') is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_rejects_suggestive_content_even_if_model_says_ok(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = (
            '{"has_text": false, "text_is_correct_spanish": true, "is_abstract_3d": false, '
            '"has_screen_content": false, "has_malformed_object": false, '
            '"has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": true, "ok": true}'
        )
        mock_client.models.generate_content.return_value = mock_resp
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', return_value=mock_client):
            assert gen._validate_product_photo_generation(b'fake-png') is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fail_open_on_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', side_effect=Exception('boom')):
            assert gen._validate_product_photo_generation(b'fake-png') is True


class TestGenerateFromPhotoAspectRatio:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_defaults_to_square_aspect_ratio(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-generated-png'
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch('core.shared.rate_limiter.throttle'):
            gen._generate_from_photo_with_retry('a prompt', MagicMock())

        call_kwargs = mock_gen_client.models.generate_content.call_args.kwargs
        assert call_kwargs['config'].image_config.aspect_ratio == '1:1'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_uses_explicit_aspect_ratio(self):
        """Los shots de reel necesitan formato vertical 9:16 -- distinto del
        1:1 cuadrado que usan los posts."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-generated-png'
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch('core.shared.rate_limiter.throttle'):
            gen._generate_from_photo_with_retry('a prompt', MagicMock(), aspect_ratio='9:16')

        call_kwargs = mock_gen_client.models.generate_content.call_args.kwargs
        assert call_kwargs['config'].image_config.aspect_ratio == '9:16'


class TestGenerateValidatedPhotoEdit:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_returns_bytes_when_first_attempt_passes_qc(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_from_photo_with_retry', return_value=b'good-bytes') as mock_gen, \
             patch.object(gen, '_validate_product_photo_generation', return_value=True):
            result = gen._generate_validated_photo_edit('prompt', MagicMock(), max_qc_retries=2, aspect_ratio='9:16')

        assert result == b'good-bytes'
        mock_gen.assert_called_once_with('prompt', mock_gen.call_args.args[1], aspect_ratio='9:16')

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_retries_when_gemini_returns_no_image(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_from_photo_with_retry', side_effect=[ValueError('no image'), b'good-bytes']) as mock_gen, \
             patch.object(gen, '_validate_product_photo_generation', return_value=True):
            result = gen._generate_validated_photo_edit('prompt', MagicMock(), max_qc_retries=2)

        assert result == b'good-bytes'
        assert mock_gen.call_count == 2

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_returns_none_when_every_attempt_returns_no_image(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_from_photo_with_retry', side_effect=ValueError('no image')) as mock_gen:
            result = gen._generate_validated_photo_edit('prompt', MagicMock(), max_qc_retries=1)

        assert result is None
        assert mock_gen.call_count == 2  # 1 + max_qc_retries

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_returns_last_bytes_when_qc_never_passes(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_from_photo_with_retry', side_effect=[b'bad-1', b'bad-2']), \
             patch.object(gen, '_validate_product_photo_generation', return_value=False):
            result = gen._generate_validated_photo_edit('prompt', MagicMock(), max_qc_retries=1)

        assert result == b'bad-2'  # reintentos agotados, se acepta la ultima imagen


class TestGenerateFromProductPhoto:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_sends_photo_and_creative_direction_uses_lite_model(self):
        """De vuelta a VERTEX_IMAGE_MODEL_LITE el 2026-08-16 -- el swap
        temporal al modelo normal (commit anterior) confirmo que el rechazo
        no era exclusivo del lite; la causa real era thinking_config ausente
        (ver test_enables_automatic_thinking), asi que se revierte al modelo
        economico con thinking ya activo."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-generated-png'
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch('core.shared.rate_limiter.throttle') as mock_throttle, \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_photo_post', return_value=('https://storage.test/bg.png', 'https://storage.test/final.png')) as mock_upload:
            result = gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product',
            )

        assert result == ('https://storage.test/bg.png', 'https://storage.test/final.png')
        mock_upload.assert_called_once_with(
            b'fake-generated-png', 'Aretes artesanales', ['#e94560'], 'alegre', '', None, '', 'test-product',
        )
        call_kwargs = mock_gen_client.models.generate_content.call_args.kwargs
        assert call_kwargs['model'] == 'gemini-3.1-flash-lite-image'
        contents = call_kwargs['contents']
        assert len(contents) == 2
        assert isinstance(contents[0], str)  # el prompt de direccion creativa
        assert contents[1].inline_data.data == b'fake-photo-bytes'  # types.Part.from_bytes real, no mockeado
        assert contents[1].inline_data.mime_type == 'image/jpeg'
        # El rate limit se pide sobre el modelo economico y la superficie Vertex
        # (RPM_LIMITS['vertex']['gemini-3.1-flash-lite-image']).
        mock_throttle.assert_called_with('gemini-3.1-flash-lite-image', 'vertex')

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_enables_automatic_thinking(self):
        """Root cause real del rechazo (finish_reason=OTHER) confirmado por
        Anuar probando 'Nano Banana Lite' en Vertex AI Studio (2026-08-16):
        el modelo necesita thinking activo para poder editar el contenido
        real que le mandamos -- sin thinking_config, el default es
        insuficiente. thinking_budget=-1 = AUTOMATIC (deja que el modelo
        decida cuanto pensar), no 0 (deshabilitado, que es lo que se usa a
        proposito en las llamadas de QC de texto)."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-generated-png'
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch('core.shared.rate_limiter.throttle'), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_photo_post', return_value=('https://storage.test/bg.png', 'https://storage.test/final.png')):
            gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product',
            )

        call_kwargs = mock_gen_client.models.generate_content.call_args.kwargs
        assert call_kwargs['config'].thinking_config.thinking_budget == -1

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_prompt_preserves_product_text_removes_only_watermarks_and_no_new_text(self):
        """Hallazgo real (2026-08-16, Anuar): pedirle a nano banana que borre
        CUALQUIER texto/logo de la foto original causaba que intentara borrar
        texto legitimo del producto (branding en un globo, en envolturas de
        dulces) y dejara restos garabateados -- confirmado en 3 fotos reales
        distintas. Fix: el prompt ahora distingue texto/marca que es PARTE del
        producto (debe conservarse intacto) de marcas de agua/texto ilegible
        que NO es parte del producto (si se puede borrar)."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-generated-png'
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch('core.shared.rate_limiter.throttle'), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_photo_post', return_value=('https://storage.test/bg.png', 'https://storage.test/final.png')):
            gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product',
            )

        call_kwargs = mock_gen_client.models.generate_content.call_args.kwargs
        prompt_text = ' '.join(str(c) for c in call_kwargs['contents'] if isinstance(c, str))
        # Texto/marca que es parte del producto: conservar intacto, no tocar.
        assert 'must stay exactly as they are' in prompt_text
        assert 'do not alter or remove them' in prompt_text
        # Solo se borra lo que NO es parte del producto: marcas de agua / texto ilegible.
        assert 'watermark' in prompt_text.lower()
        assert 'illegible' in prompt_text.lower() or 'garbled' in prompt_text.lower()
        # No agregar texto nuevo sigue vigente.
        assert 'do not add text' in prompt_text.lower() or 'no text' in prompt_text.lower()
        # caption y vision_context son entrada del usuario -- van delimitados
        # con el mismo marcador que _regenerate_caption (core/brand_dna/views.py).
        assert '=== INICIO DATOS DEL CLIENTE' in prompt_text
        assert '=== FIN DATOS DEL CLIENTE' in prompt_text

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_returns_empty_tuple_on_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client', side_effect=Exception('boom')), \
             patch('core.shared.rate_limiter.throttle'):
            result = gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product',
            )
        assert result == ('', '')

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_records_cost_at_the_lite_model_rate(self):
        """De vuelta al modelo lite (2026-08-16) -- contabilizarlo a la
        tarifa del modelo normal (_GEMINI_IMAGE_COST_PER_IMAGE) inflaba el
        panel de costo de Prometheus, justo la medicion para la que se eligio
        el lite."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        from core.shared.metrics_utils import (
            _GEMINI_LITE_IMAGE_COST_PER_IMAGE, _GEMINI_IMAGE_COST_PER_IMAGE,
        )
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-generated-png'
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch('core.shared.rate_limiter.throttle'), \
             patch('core.content_pipeline.generators.image_generator.record_gemini_image_generation') as mock_record, \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_photo_post', return_value=('https://storage.test/bg.png', 'https://storage.test/final.png')):
            gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product',
            )

        mock_record.assert_called_once_with(
            'generate_from_photo', cost_per_image=_GEMINI_LITE_IMAGE_COST_PER_IMAGE,
        )
        assert _GEMINI_LITE_IMAGE_COST_PER_IMAGE != _GEMINI_IMAGE_COST_PER_IMAGE

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_retries_when_gemini_returns_no_image_parts(self):
        """Reproduce bug real de prueba manual (2026-08-16, job
        94a75f45-0365-4953-97f6-f29c99f1a89d): Gemini respondio 200 OK pero
        sin imagen (bloqueo de seguridad sobre la foto real) -- el intento 1
        crasheaba y abortaba TODO el presupuesto de reintentos de QC (2 mas
        disponibles) en vez de intentar de nuevo, dejando el post con
        image_url='' aunque el intento 2 hubiera funcionado."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        blocked_resp = MagicMock(candidates=[MagicMock(content=MagicMock(parts=None), finish_reason='SAFETY')])
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-generated-png'
        ok_resp = MagicMock(candidates=[MagicMock(content=MagicMock(parts=[mock_part]))])
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.side_effect = [blocked_resp, ok_resp]
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch('core.shared.rate_limiter.throttle'), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_photo_post', return_value=('https://storage.test/bg.png', 'https://storage.test/final.png')):
            result = gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product', max_qc_retries=2,
            )

        assert result == ('https://storage.test/bg.png', 'https://storage.test/final.png')
        assert mock_gen_client.models.generate_content.call_count == 2

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_returns_empty_tuple_when_every_attempt_returns_no_image(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=None), finish_reason='SAFETY')]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch('core.shared.rate_limiter.throttle'):
            result = gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product', max_qc_retries=2,
            )
        assert result == ('', '')
        assert mock_gen_client.models.generate_content.call_count == 3  # 1 + max_qc_retries

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_uploads_background_and_final_via_layered_pipeline(self):
        """Overlay exitoso: sube el fondo limpio Y el resultado final
        compuesto con _layered_pipeline, con URLs distintas."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-generated-png'
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        upload_calls = []
        def fake_upload(image_bytes, filename):
            upload_calls.append(filename)
            return f'https://storage.test/{filename}.png'
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch('core.shared.rate_limiter.throttle'), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_layered_pipeline', return_value=b'fake-final-bytes') as mock_layered, \
             patch.object(gen, '_upload_to_storage', side_effect=fake_upload):
            background_url, final_url = gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product', description='Joyeria artesanal',
                keywords=['aretes', 'plata'], business_url='https://ejemplo.com',
            )

        assert background_url == 'https://storage.test/test-product-bg.png'
        assert final_url == 'https://storage.test/test-product.png'
        assert upload_calls == ['test-product-bg', 'test-product']
        mock_layered.assert_called_once_with(
            'Aretes artesanales', ['#e94560'], 'alegre', ['aretes', 'plata'], 'Joyeria artesanal',
            business_url='https://ejemplo.com', font_seed='test-product', background_bytes=b'fake-generated-png',
        )

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_degrades_to_clean_background_when_overlay_fails(self):
        """Si _layered_pipeline falla (Playwright, plantilla) despues de un
        fondo valido, ambas URLs apuntan al fondo limpio -- no se pierde el
        trabajo de nano banana."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-generated-png'
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch('core.shared.rate_limiter.throttle'), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_layered_pipeline', side_effect=Exception('Playwright error')), \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/test-product-bg.png'):
            background_url, final_url = gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product',
            )

        assert background_url == 'https://storage.test/test-product-bg.png'
        assert final_url == background_url


class TestRegenerateWithReference:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_sends_current_background_not_original_photo(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-regenerated-png'
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client), \
             patch('core.shared.rate_limiter.throttle'), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_photo_post', return_value=('https://storage.test/bg.png', 'https://storage.test/final.png')):
            result = gen.regenerate_with_reference(
                current_background_bytes=b'current-background-bytes',
                feedback='hazlo mas colorido',
                vision_context='Aretes de plata con turquesa',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product-regen',
            )

        assert result == ('https://storage.test/bg.png', 'https://storage.test/final.png')
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        prompt_text = ' '.join(str(c) for c in call_kwargs['contents'] if isinstance(c, str))
        assert 'hazlo mas colorido' in prompt_text
        assert 'Aretes de plata con turquesa' in prompt_text
        # feedback y vision_context son entrada del usuario -- delimitados.
        assert '=== INICIO DATOS DEL CLIENTE' in prompt_text
        assert '=== FIN DATOS DEL CLIENTE' in prompt_text
        assert 'Do not add new text' in prompt_text
        # La imagen enviada es el FONDO LIMPIO actual, no la foto original del producto.
        contents = call_kwargs['contents']
        assert contents[1].inline_data.data == b'current-background-bytes'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_returns_empty_tuple_on_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client', side_effect=Exception('boom')), \
             patch('core.shared.rate_limiter.throttle'):
            result = gen.regenerate_with_reference(
                current_background_bytes=b'current-background-bytes', feedback='mas colorido',
                vision_context='', caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product-regen',
            )
        assert result == ('', '')

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_retries_when_gemini_returns_no_image_parts(self):
        """Mismo bug real que TestGenerateFromProductPhoto -- ver ese test para
        el caso reproducido en produccion. Aqui se cubre el segundo caller de
        _generate_from_photo_with_retry para no dejar la regeneracion con el
        mismo hueco."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        blocked_resp = MagicMock(candidates=[MagicMock(content=MagicMock(parts=None), finish_reason='SAFETY')])
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-regenerated-png'
        ok_resp = MagicMock(candidates=[MagicMock(content=MagicMock(parts=[mock_part]))])
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [blocked_resp, ok_resp]
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client), \
             patch('core.shared.rate_limiter.throttle'), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_photo_post', return_value=('https://storage.test/bg.png', 'https://storage.test/final.png')):
            result = gen.regenerate_with_reference(
                current_background_bytes=b'current-background-bytes', feedback='mas colorido',
                vision_context='', caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product-regen', max_qc_retries=2,
            )
        assert result == ('https://storage.test/bg.png', 'https://storage.test/final.png')
        assert mock_client.models.generate_content.call_count == 2

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_detects_real_mime_type_of_current_background(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-regenerated-png'
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        jpeg_bytes = b'\xff\xd8\xff' + b'fake-jpeg-body'
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client), \
             patch('core.shared.rate_limiter.throttle'), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_photo_post', return_value=('https://storage.test/bg.png', 'https://storage.test/final.png')):
            gen.regenerate_with_reference(
                current_background_bytes=jpeg_bytes, feedback='mas colorido',
                vision_context='', caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product-regen',
            )
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert call_kwargs['contents'][1].inline_data.mime_type == 'image/jpeg'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_composes_overlay_with_new_caption_via_upload_photo_post(self):
        """El caption ya viene regenerado (por _regenerate_caption en
        views.py, antes de encolar la tarea) -- regenerate_with_reference debe
        pasarlo tal cual a _upload_photo_post para que el overlay use el
        contenido correcto, no el viejo."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-regenerated-png'
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client), \
             patch('core.shared.rate_limiter.throttle'), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_photo_post', return_value=('https://storage.test/bg.png', 'https://storage.test/final.png')) as mock_upload:
            gen.regenerate_with_reference(
                current_background_bytes=b'current-background-bytes', feedback='mas colorido',
                vision_context='Aretes de plata', caption='Nuevo caption regenerado',
                colors=['#e94560'], tone='alegre', filename='test-product-regen',
                description='Joyeria artesanal', keywords=['aretes'], business_url='https://ejemplo.com',
            )

        mock_upload.assert_called_once_with(
            b'fake-regenerated-png', 'Nuevo caption regenerado', ['#e94560'], 'alegre',
            'Joyeria artesanal', ['aretes'], 'https://ejemplo.com', 'test-product-regen',
        )
