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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc, \
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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


class TestValidateBackground:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_when_image_ok(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, '
                '"has_malformed_object": false, "has_unrealistic_grounding": true, "ok": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_background(b'fake-png')
        assert result is False


class TestChooseTemplateForImage:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_maps_bottom_zone_to_lower_third_template(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"safe_zone": "diagonal"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._choose_template_for_image(b'fake-png')
        assert result in gen._TEMPLATES


class TestGeneratePostContentWithProduct:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_multimodal_call_when_product_image_provided(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_image = _png_bytes()
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"Brilla distinto","subtitle":"Plata 925 hecha a mano para ti","cta":"Cómpralo ahora","tag":"JOYERÍA"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Collar artesanal de plata', product_image_bytes=fake_image)
        call_args = mock_vc.return_value.models.generate_content.call_args
        contents = call_args.kwargs.get('contents') or (call_args.args[1] if len(call_args.args) > 1 else None)
        assert isinstance(contents, list), "Multimodal call must pass contents as list [image_part, prompt]"
        assert result['headline'] == 'Brilla distinto'
        assert result['tag'] == 'JOYERÍA'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_text_only_call_when_no_product_image(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"Impulsa tu negocio","subtitle":"Tecnología que funciona para ti","cta":"Empieza hoy","tag":"TECNOLOGÍA"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Soluciones tecnológicas', product_image_bytes=None)
        call_args = mock_vc.return_value.models.generate_content.call_args
        contents = call_args.kwargs.get('contents') or (call_args.args[1] if len(call_args.args) > 1 else None)
        assert isinstance(contents, str), "Text-only call must pass contents as string"
        assert result['headline'] == 'Impulsa tu negocio'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_brand_context_included_in_multimodal_prompt(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_image = _png_bytes()
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"Tu marca brilla","subtitle":"Identidad que vende","cta":"Hablemos","tag":"BRANDING"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._generate_post_content(
                'Post sobre branding',
                product_image_bytes=fake_image,
                brand_context='Agencia de branding. Tono: profesional.',
            )
        call_args = mock_vc.return_value.models.generate_content.call_args
        contents = call_args.kwargs.get('contents') or (call_args.args[1] if len(call_args.args) > 1 else None)
        assert isinstance(contents, list)
        prompt_text = contents[1]  # second element is the text prompt
        assert 'Agencia de branding' in prompt_text, "brand_context must appear in the multimodal prompt"


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


class TestLayeredPipelineWithProduct:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_product_path_calls_generate_product_scene(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes((200, 150, 100))
        scene_img = _png_bytes((100, 180, 140))
        fake_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080"></svg>'
        fake_content = {'headline': 'Brilla distinto', 'subtitle': 'Plata artesanal', 'cta': 'Cómpralo', 'tag': 'JOYERÍA'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_product_scene', return_value=(scene_img, fake_svg)) as mock_scene, \
             patch.object(gen, '_generate_background') as mock_bg, \
             patch.object(gen, '_generate_post_content', return_value=fake_content) as mock_content, \
             patch.object(gen, '_render_html_template', return_value=fake_shot) as mock_render, \
             patch.object(gen, '_validate_final_image', return_value=True):
            result = gen._layered_pipeline('Collar artesanal', ['#c0c0c0'], 'elegante', product_image_bytes=product_img)

        mock_scene.assert_called_once_with(product_img, 'Collar artesanal', ['#c0c0c0'], 'elegante', max_qc_retries=2)
        mock_bg.assert_not_called()
        mock_content.assert_called_once()
        call_kwargs = mock_content.call_args.kwargs
        assert call_kwargs['product_image_bytes'] == product_img
        assert 'brand_context' in call_kwargs and len(call_kwargs['brand_context']) > 0
        mock_render.assert_called_once_with(scene_img, fake_content, ['#c0c0c0'], svg_overlay=fake_svg, font_seed='')
        assert result == fake_shot

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_final_qc_fail_rerenders_without_svg(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes((200, 150, 100))
        scene_img = _png_bytes((100, 180, 140))
        fake_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080"></svg>'
        fake_content = {'headline': 'Test', 'subtitle': 'Sub', 'cta': 'CTA', 'tag': 'TAG'}
        render_with_svg = _png_bytes((200, 50, 50), size=(1080, 1080))
        render_no_svg = _png_bytes((50, 200, 50), size=(1080, 1080))

        with patch.object(gen, '_generate_product_scene', return_value=(scene_img, fake_svg)), \
             patch.object(gen, '_generate_post_content', return_value=fake_content), \
             patch.object(gen, '_render_html_template', side_effect=[render_with_svg, render_no_svg]) as mock_render, \
             patch.object(gen, '_validate_final_image', return_value=False):
            result = gen._layered_pipeline('Caption', ['#c0c0c0'], 'elegante', product_image_bytes=product_img)

        assert mock_render.call_count == 2
        second_call = mock_render.call_args_list[1]
        assert second_call.kwargs.get('svg_overlay') == '' or second_call.args[-1] == ''
        assert result == render_no_svg

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_final_qc_skipped_when_max_qc_retries_zero(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes()
        fake_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080"></svg>'
        fake_content = {'headline': 'Test', 'subtitle': 'Sub', 'cta': 'CTA', 'tag': 'TAG'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_product_scene', return_value=(_png_bytes(), fake_svg)), \
             patch.object(gen, '_generate_post_content', return_value=fake_content), \
             patch.object(gen, '_render_html_template', return_value=fake_shot), \
             patch.object(gen, '_validate_final_image') as mock_qc:
            gen._layered_pipeline('Caption', ['#c0c0c0'], 'elegante', product_image_bytes=product_img, max_qc_retries=0)

        mock_qc.assert_not_called()  # QC disabled para UI (max_qc_retries=0)

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_no_product_uses_imagen3_flow(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes((30, 30, 60))
        fake_content = {'headline': 'Hola mundo', 'subtitle': 'Mundo', 'cta': 'Ver', 'tag': 'TEST'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_background', return_value=fake_bg) as mock_bg, \
             patch.object(gen, '_generate_post_content', return_value=fake_content) as mock_content, \
             patch.object(gen, '_render_html_template', return_value=fake_shot):
            gen._layered_pipeline('Caption', ['#1a1a2e'], 'profesional', product_image_bytes=None)

        mock_bg.assert_called_once()
        mock_content.assert_called_once_with('Caption', product_image_bytes=None, brand_context='Tono: profesional.', business_url='')


class TestGenerateProductScene:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_scene_and_svg_tuple(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes((200, 150, 100))
        scene_img = _png_bytes((100, 180, 140))
        fake_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080"></svg>'

        with patch.object(gen, '_analyze_product_style', return_value='premium env prompt'), \
             patch.object(gen, '_bgswap_product', return_value=(scene_img, True)), \
             patch.object(gen, '_validate_background', return_value=True), \
             patch.object(gen, '_generate_svg_overlay', return_value=fake_svg):
            result = gen._generate_product_scene(product_img, 'Collar artesanal', ['#c0c0c0'], 'elegante')

        assert isinstance(result, tuple) and len(result) == 2
        assert result[0] == scene_img
        assert result[1] == fake_svg

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_bgswap_fallback_skips_svg_overlay(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes()

        with patch.object(gen, '_analyze_product_style', return_value='env prompt'), \
             patch.object(gen, '_bgswap_product', return_value=(product_img, False)), \
             patch.object(gen, '_generate_svg_overlay') as mock_svg:
            scene_bytes, svg = gen._generate_product_scene(product_img, 'Caption', [], 'pro')

        mock_svg.assert_not_called()  # SVG no se genera si BGSWAP falló
        assert scene_bytes == product_img
        assert svg == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_scene_qc_retries_bgswap_on_fail(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes((200, 150, 100))
        bad_scene = _png_bytes((200, 50, 50))
        good_scene = _png_bytes((50, 200, 50))

        with patch.object(gen, '_analyze_product_style', return_value='env prompt'), \
             patch.object(gen, '_bgswap_product', side_effect=[(bad_scene, True), (good_scene, True)]) as mock_bgswap, \
             patch.object(gen, '_validate_background', side_effect=[False, True]), \
             patch.object(gen, '_generate_svg_overlay', return_value=''):
            scene_bytes, _ = gen._generate_product_scene(product_img, 'Caption', [], 'pro', max_qc_retries=2)

        assert mock_bgswap.call_count == 2  # reintentó BGSWAP al fallar QC
        assert scene_bytes == good_scene

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_scene_qc_skipped_when_max_qc_retries_zero(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes()
        scene_img = _png_bytes((100, 180, 140))

        with patch.object(gen, '_analyze_product_style', return_value='env prompt'), \
             patch.object(gen, '_bgswap_product', return_value=(scene_img, True)), \
             patch.object(gen, '_validate_background') as mock_validate, \
             patch.object(gen, '_generate_svg_overlay', return_value=''):
            gen._generate_product_scene(product_img, 'Caption', [], 'pro', max_qc_retries=0)

        mock_validate.assert_not_called()  # QC desactivado para UI


class TestBgswapProduct:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
    )
    def test_returns_scene_bytes_on_success(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes((200, 150, 100))
        scene_img = _png_bytes((100, 180, 140))
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_generated = MagicMock()
            mock_generated.image.image_bytes = scene_img
            mock_vc.return_value.models.edit_image.return_value.generated_images = [mock_generated]
            result_bytes, ok = gen._bgswap_product(product_img, 'luxury marble pedestal, warm lighting')
        assert result_bytes == scene_img
        assert ok is True
        call_kwargs = mock_vc.return_value.models.edit_image.call_args.kwargs
        from google.genai.types import EditMode, MaskReferenceMode
        assert call_kwargs['config'].edit_mode == EditMode.EDIT_MODE_BGSWAP
        assert len(call_kwargs['reference_images']) == 2  # RawReferenceImage + MaskReferenceImage

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
    )
    def test_falls_back_to_product_on_api_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes()
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.edit_image.side_effect = Exception('API error')
            result_bytes, ok = gen._bgswap_product(product_img, 'some prompt')
        assert result_bytes == product_img
        assert ok is False


class TestGenerateSvgOverlay:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_svg_string_on_success(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080"><ellipse cx="540" cy="900" rx="200" ry="30" fill="black" opacity="0.18"/></svg>'
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = fake_svg
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_svg_overlay(_png_bytes(), ['#c0c0c0'])
        assert result.startswith('<svg')
        assert result.endswith('</svg>')

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_empty_string_on_api_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._generate_svg_overlay(_png_bytes(), [])
        assert result == ''


class TestValidateFinalImage:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_when_image_is_clean(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc, \
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
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
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_uses_product_scene_when_product_image_provided(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        fake_slides = [{'headline': 'H', 'subtitle': 'S', 'cta': 'CTA', 'tag': 'TAG'}] * 3
        product_img = _png_bytes((200, 50, 50))
        with patch.object(gen, '_generate_product_scene', return_value=(fake_bg, '<svg></svg>')) as mock_scene, \
             patch.object(gen, '_generate_background') as mock_no_product_bg, \
             patch.object(gen, '_generate_carousel_slides_content', return_value=fake_slides), \
             patch.object(gen, '_render_html_template', return_value=fake_shot), \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/slide.png'):
            gen.generate_carousel('Caption', ['#1a1a2e'], 'profesional', 'job1-day3', product_image_bytes=product_img, num_slides=3)
        mock_scene.assert_called_once()
        mock_no_product_bg.assert_not_called()

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

