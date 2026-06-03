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

        mock_render.assert_called_once_with(fake_bg, fake_content, ['#1a1a2e'])
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
        fake_content = {'headline': 'Brilla distinto', 'subtitle': 'Plata artesanal', 'cta': 'Cómpralo', 'tag': 'JOYERÍA'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_product_scene', return_value=scene_img) as mock_scene, \
             patch.object(gen, '_generate_background') as mock_bg, \
             patch.object(gen, '_generate_post_content', return_value=fake_content) as mock_content, \
             patch.object(gen, '_render_html_template', return_value=fake_shot) as mock_render:
            result = gen._layered_pipeline('Collar artesanal', ['#c0c0c0'], 'elegante', product_image_bytes=product_img)

        mock_scene.assert_called_once_with(product_img, 'Collar artesanal', ['#c0c0c0'], 'elegante')
        mock_bg.assert_not_called()
        mock_content.assert_called_once()
        call_kwargs = mock_content.call_args.kwargs
        assert call_kwargs['product_image_bytes'] == product_img
        assert 'brand_context' in call_kwargs and len(call_kwargs['brand_context']) > 0
        mock_render.assert_called_once_with(scene_img, fake_content, ['#c0c0c0'])
        assert result == fake_shot

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
        mock_content.assert_called_once_with('Caption', product_image_bytes=None)


class TestGenerateProductScene:
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
            result = gen._generate_product_scene(product_img, 'Collar artesanal', ['#c0c0c0'], 'elegante')
        assert result == scene_img
        call_kwargs = mock_vc.return_value.models.edit_image.call_args.kwargs
        assert call_kwargs['model'] == 'imagen-3.0-capability-001'
        assert 'person' in call_kwargs['prompt'].lower() or 'lifestyle' in call_kwargs['prompt'].lower()

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
    )
    def test_falls_back_to_product_image_on_api_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes()
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.edit_image.side_effect = Exception('API error')
            result = gen._generate_product_scene(product_img, 'Caption', [], 'profesional')
        assert result == product_img

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
    )
    def test_falls_back_to_product_image_on_empty_response(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes()
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.edit_image.return_value.generated_images = []
            result = gen._generate_product_scene(product_img, 'Caption', [], 'profesional')
        assert result == product_img

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
    )
    def test_uses_subject_reference_product_image_mode(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes()
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.edit_image.side_effect = Exception('stop early')
            gen._generate_product_scene(product_img, 'Caption', [], 'pro')
        call_kwargs = mock_vc.return_value.models.edit_image.call_args.kwargs
        config = call_kwargs['config']
        from google.genai.types import EditMode
        assert config.edit_mode == EditMode.EDIT_MODE_PRODUCT_IMAGE
        ref_images = call_kwargs['reference_images']
        assert len(ref_images) == 1  # SubjectReferenceImage
