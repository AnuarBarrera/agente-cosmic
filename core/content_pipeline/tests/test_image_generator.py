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
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
)
def test_generate_with_vertex_passes_negative_prompt_for_imagen():
    from core.content_pipeline.generators.image_generator import ImageGenerator, _IMAGE_NEGATIVE_PROMPT
    gen = ImageGenerator(bucket_name='test-bucket')
    mock_client = MagicMock()
    mock_client.models.generate_images.return_value = MagicMock(
        generated_images=[MagicMock(image=MagicMock(image_bytes=b'fake-png'))]
    )
    with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client):
        gen._generate_with_vertex('a test prompt')

    call_kwargs = mock_client.models.generate_images.call_args.kwargs
    assert call_kwargs['config'].negative_prompt == _IMAGE_NEGATIVE_PROMPT



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

