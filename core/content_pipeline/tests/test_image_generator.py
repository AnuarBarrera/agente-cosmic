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
         patch.object(gen, '_upload_to_storage', return_value='https://storage.googleapis.com/test/img.jpg'):
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
