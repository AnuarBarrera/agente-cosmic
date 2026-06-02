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

def test_build_prompt_includes_colors():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    prompt = gen._build_prompt(
        caption='Diseno web profesional para tu empresa',
        colors=['#1a1a2e', '#e94560'],
        tone='profesional',
    )
    assert '#1a1a2e' in prompt
    assert 'profesional' in prompt


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


class TestOverlayText:
    def test_overlay_produces_valid_png(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        result = gen._overlay_text(_png_bytes(size=(1024, 1024)), "Caption de prueba")
        out = Image.open(io.BytesIO(result))
        assert out.size == (1024, 1024)

    def test_overlay_handles_long_caption(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        result = gen._overlay_text(_png_bytes(size=(1024, 1024)), "A" * 300)
        assert len(result) > 0
        assert Image.open(io.BytesIO(result)).size == (1024, 1024)


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


class TestGenerateTextAsset:
    def test_returns_valid_png_bytes(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        result = gen._generate_text_asset("Digitaliza tu negocio", ['#1a1a2e'])
        assert result is not None
        out = Image.open(io.BytesIO(result))
        assert out.size == (1024, 512)
        assert out.mode == 'RGB'

    def test_background_is_exact_magenta(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        import numpy as np
        gen = ImageGenerator(bucket_name='test-bucket')
        result = gen._generate_text_asset("Hola Mundo", ['#ffffff'])
        img = Image.open(io.BytesIO(result)).convert('RGB')
        arr = np.array(img)
        # Top-left corner should be magenta (255, 0, 255) — no text there
        corner = arr[5, 5]
        assert corner[0] == 255 and corner[1] == 0 and corner[2] == 255

    def test_returns_none_on_exception(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        from unittest.mock import patch as _patch
        gen = ImageGenerator(bucket_name='test-bucket')
        with _patch('core.content_pipeline.generators.image_generator.Image.new', side_effect=Exception('PIL error')):
            result = gen._generate_text_asset("Test", [])
        assert result is None


class TestAnalyzeBackground:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_fallback_on_api_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._analyze_background(b'fake', 'Tu negocio necesita una web')
        assert 'x' in result and 'y' in result and 'width' in result and 'headline' in result
        assert 0.0 <= result['x'] <= 1.0
        assert len(result['headline']) > 0

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
            mock_resp.text = '{"x": 0.05, "y": 0.62, "width": 0.9, "headline": "Tu Web En 48h"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._analyze_background(b'fake', 'Tu negocio necesita una web YA')
        assert result['x'] == 0.05
        assert result['y'] == 0.62
        assert result['headline'] == 'Tu Web En 48h'


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

        with patch('core.content_pipeline.generators.image_generator.sync_playwright', return_value=mock_pw):
            result = gen._render_html_template(fake_bg, content, ['#e94560'])

        assert result == fake_shot

    def test_injects_primary_color_into_html(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        content = {'headline': 'Título', 'subtitle': 'Subtítulo', 'cta': 'Empieza', 'tag': 'TEST'}
        mock_pw, mock_page = self._make_mock_playwright(fake_shot)

        with patch('core.content_pipeline.generators.image_generator.sync_playwright', return_value=mock_pw):
            gen._render_html_template(fake_bg, content, ['#ff5500'])

        html_arg = mock_page.set_content.call_args[0][0]
        assert '#ff5500' in html_arg

    def test_uses_fallback_color_when_no_colors(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        content = {'headline': 'Título', 'subtitle': 'Subtítulo', 'cta': 'Empieza', 'tag': 'TEST'}
        mock_pw, mock_page = self._make_mock_playwright(fake_shot)

        with patch('core.content_pipeline.generators.image_generator.sync_playwright', return_value=mock_pw):
            gen._render_html_template(fake_bg, content, [])

        html_arg = mock_page.set_content.call_args[0][0]
        assert '#e94560' in html_arg  # fallback color


class TestLayeredPipeline:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_pipeline_composites_when_text_asset_succeeds(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        import core.content_pipeline.generators.layer_composer as lc_module
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes((30, 30, 60))
        fake_text = _png_bytes((255, 0, 255))
        fake_composite = _png_bytes((100, 100, 100))

        with patch.object(gen, '_generate_background', return_value=fake_bg), \
             patch.object(gen, '_generate_text_asset', return_value=fake_text), \
             patch.object(gen, '_analyze_background', return_value={'x': 0.1, 'y': 0.6, 'width': 0.8, 'headline': 'Caption de prueba'}), \
             patch.object(lc_module, 'composite_layers', return_value=fake_composite) as mock_composite:
            result = gen._layered_pipeline('Caption de prueba', ['#1a1a2e'], 'profesional')

        mock_composite.assert_called_once()
        call_kwargs = mock_composite.call_args
        assert call_kwargs.kwargs['x'] == 0.1
        assert call_kwargs.kwargs['y'] == 0.6
        assert call_kwargs.kwargs['width'] == 0.8
        assert result == fake_composite

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_pipeline_returns_background_when_text_asset_fails(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes((30, 30, 60))

        with patch.object(gen, '_generate_background', return_value=fake_bg), \
             patch.object(gen, '_generate_text_asset', return_value=None), \
             patch.object(gen, '_analyze_background', return_value={'x': 0.1, 'y': 0.6, 'width': 0.8, 'headline': 'Caption de prueba'}):
            result = gen._layered_pipeline('Caption', ['#1a1a2e'], 'profesional')

        assert result == fake_bg
