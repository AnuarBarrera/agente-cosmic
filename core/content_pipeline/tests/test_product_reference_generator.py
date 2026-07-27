import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestGenerateImage:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_url_when_scene_and_qc_succeed(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        scene_resp = MagicMock()
        scene_part = MagicMock()
        scene_part.inline_data.data = b'fake-scene-png'
        scene_part.text = None
        scene_resp.candidates = [MagicMock(content=MagicMock(parts=[scene_part]))]

        qc_resp = MagicMock()
        qc_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "ok": true}'

        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc, \
             patch.object(ProductReferenceGenerator, '_upload_to_storage', return_value='https://storage.test/scene.png'):
            mock_vc.return_value.models.generate_content.side_effect = [scene_resp, qc_resp]
            result = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert result == 'https://storage.test/scene.png'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_string_when_scene_generation_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert result == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_string_when_qc_rejects_scene(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        scene_resp = MagicMock()
        scene_part = MagicMock()
        scene_part.inline_data.data = b'fake-scene-png'
        scene_part.text = None
        scene_resp.candidates = [MagicMock(content=MagicMock(parts=[scene_part]))]

        qc_resp = MagicMock()
        qc_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "ok": false}'

        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = [scene_resp, qc_resp]
            result = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert result == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_generate_image_returns_empty_string_when_upload_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=True), \
             patch.object(gen, '_upload_to_storage', side_effect=Exception('GCS down')):
            result = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert result == ''


class TestGenerateReel:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_video_and_poster_url_when_everything_succeeds(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        ok_qc = MagicMock()
        ok_qc.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "ok": true}'

        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=True), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=b'frame-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://storage.test/poster.png', 'https://storage.test/video.mp4']):
            video_url, poster_url = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert video_url == 'https://storage.test/video.mp4'
        assert poster_url == 'https://storage.test/poster.png'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_strings_when_scene_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=None):
            video_url, poster_url = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_strings_when_video_generation_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=True), \
             patch.object(gen, '_animate_scene', return_value=None):
            video_url, poster_url = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_strings_when_a_video_frame_fails_qc(self):
        """Reproduce el hallazgo real de hoy: un frame intermedio del video con un
        logo alucinado que no estaba en el frame inicial — debe rechazar el
        resultado completo, no solo advertir."""
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', side_effect=[True, True, False]), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=b'frame-bytes'):
            video_url, poster_url = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_strings_when_frame_extraction_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=True), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=None):
            video_url, poster_url = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''


class TestValidateScene:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_when_image_ok(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "ok": true}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_scene(b'fake-png')
        assert result is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_has_text(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "ok": false}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_scene(b'fake-png')
        assert result is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_on_api_error(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._validate_scene(b'fake-png')
        assert result is True  # fail-open, mismo criterio que _validate_background
