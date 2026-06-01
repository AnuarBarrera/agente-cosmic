from unittest.mock import patch, MagicMock
from django.test import override_settings


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
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
)
def test_generate_returns_url():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc, \
         patch.object(gen, '_upload_to_storage', return_value='https://storage.googleapis.com/test/img.jpg'), \
         patch.object(gen, '_overlay_text', side_effect=lambda b, c: b) as mock_overlay:
        mock_candidate = MagicMock()
        mock_part = MagicMock()
        mock_part.inline_data = MagicMock()
        mock_part.inline_data.data = b'fake-png-bytes'
        mock_candidate.content.parts = [mock_part]
        mock_vc.return_value.models.generate_content.return_value.candidates = [mock_candidate]

        url = gen.generate(
            caption='Diseno web profesional',
            colors=['#1a1a2e'],
            tone='profesional',
            filename='test-img',
        )
    assert url.startswith('https://')
    mock_overlay.assert_called_once()


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
)
def test_generate_returns_fallback_on_error():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
        mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
        url = gen.generate(
            caption='Diseno web profesional',
            colors=['#1a1a2e'],
            tone='profesional',
            filename='test-img',
        )
    assert url == ''


from PIL import Image
import io


class TestOverlayText:
    def test_overlay_produces_valid_png(self):
        """_overlay_text debe devolver bytes PNG válidos con las dimensiones originales."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        img = Image.new('RGB', (1024, 1024), color=(30, 30, 60))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        original_bytes = buf.getvalue()

        result = gen._overlay_text(original_bytes, "Este es un caption de prueba para redes sociales")

        out = Image.open(io.BytesIO(result))
        assert out.size == (1024, 1024)
        assert result != original_bytes

    def test_overlay_handles_long_caption(self):
        """Captions largos deben truncarse/envolverse sin crash."""
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        img = Image.new('RGB', (1024, 1024), color=(30, 30, 60))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        long_caption = "A" * 300

        result = gen._overlay_text(buf.getvalue(), long_caption)
        assert len(result) > 0
        out = Image.open(io.BytesIO(result))
        assert out.size == (1024, 1024)
