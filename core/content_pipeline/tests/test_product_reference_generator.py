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
        qc_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'

        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc, \
             patch.object(ProductReferenceGenerator, '_upload_to_storage', return_value='https://storage.test/scene.png'):
            mock_vc.return_value.models.generate_content.side_effect = [scene_resp, qc_resp]
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert url == 'https://storage.test/scene.png'
        assert reason == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_string_when_scene_generation_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert url == ''
        assert reason != ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_watermark_message_when_qc_rejects_for_text(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        scene_resp = MagicMock()
        scene_part = MagicMock()
        scene_part.inline_data.data = b'fake-scene-png'
        scene_part.text = None
        scene_resp.candidates = [MagicMock(content=MagicMock(parts=[scene_part]))]

        qc_resp = MagicMock()
        qc_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": false}'

        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.return_value = scene_resp
            with patch('core.content_pipeline.generators.product_reference_generator._vertex_text_client') as mock_vc_text:
                mock_vc_text.return_value.models.generate_content.return_value = qc_resp
                url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert url == ''
        assert 'marca de agua' in reason.lower()

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_screenshot_message_when_text_and_screen_content(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        scene_resp = MagicMock()
        scene_part = MagicMock()
        scene_part.inline_data.data = b'fake-scene-png'
        scene_part.text = None
        scene_resp.candidates = [MagicMock(content=MagicMock(parts=[scene_part]))]

        qc_resp = MagicMock()
        qc_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": true, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": false}'

        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.return_value = scene_resp
            with patch('core.content_pipeline.generators.product_reference_generator._vertex_text_client') as mock_vc_text:
                mock_vc_text.return_value.models.generate_content.return_value = qc_resp
                url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert url == ''
        assert 'captura de pantalla' in reason.lower()

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_generate_image_returns_empty_string_when_upload_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_upload_to_storage', side_effect=Exception('GCS down')):
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert url == ''
        assert reason != ''

    def test_returns_reject_message_without_calling_generate_scene(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('reject', {'is_screenshot_or_ui': True})), \
             patch.object(gen, '_generate_scene') as mock_generate_scene:
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert url == ''
        assert 'captura de pantalla' in reason.lower()
        mock_generate_scene.assert_not_called()

    def test_enhance_route_uploads_enhanced_photo_without_calling_generate_scene(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('enhance', {'is_already_professional': True})), \
             patch('core.content_pipeline.generators.product_reference_generator.enhance_photo_classic',
                   return_value=b'enhanced-bytes') as mock_enhance, \
             patch.object(gen, '_generate_scene') as mock_generate_scene, \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/enhanced.png') as mock_upload:
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert url == 'https://storage.test/enhanced.png'
        assert reason == ''
        mock_generate_scene.assert_not_called()
        mock_enhance.assert_called_once_with(b'fake-photo-bytes')
        mock_upload.assert_called_once_with(b'enhanced-bytes', 'job123-sample', 'image/png', 'product-samples')


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

        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=b'frame-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://storage.test/poster.png', 'https://storage.test/video.mp4']):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert video_url == 'https://storage.test/video.mp4'
        assert poster_url == 'https://storage.test/poster.png'
        assert reason == ''

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
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=None):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert reason != ''

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
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_animate_scene', return_value=None):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert reason != ''

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
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', side_effect=[
                 (True, {'ok': True}), (True, {'ok': True}), (False, {'has_text': True, 'ok': False}),
             ]), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=b'frame-bytes'):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert 'marca de agua' in reason.lower()

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
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=None):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert reason != ''

    def test_returns_reject_message_without_calling_generate_scene(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('reject', {'has_aggressive_watermark': True})), \
             patch.object(gen, '_generate_scene') as mock_generate_scene:
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert 'marca de agua' in reason.lower()
        mock_generate_scene.assert_not_called()

    def test_enhance_route_animates_with_ffmpeg_without_calling_veo(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('enhance', {'has_full_person_subject': True})), \
             patch('core.content_pipeline.generators.product_reference_generator.enhance_photo_classic',
                   return_value=b'enhanced-bytes'), \
             patch.object(gen, '_generate_scene') as mock_generate_scene, \
             patch.object(gen, '_animate_scene') as mock_animate_scene, \
             patch.object(gen, '_animate_still_to_clip', return_value=b'ffmpeg-video-bytes') as mock_animate_clip, \
             patch.object(gen, '_upload_to_storage', side_effect=[
                 'https://storage.test/poster.png', 'https://storage.test/video.mp4',
             ]):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == 'https://storage.test/video.mp4'
        assert poster_url == 'https://storage.test/poster.png'
        assert reason == ''
        mock_generate_scene.assert_not_called()
        mock_animate_scene.assert_not_called()
        mock_animate_clip.assert_called_once_with(b'enhanced-bytes')

    def test_enhance_route_returns_message_when_ffmpeg_animation_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('enhance', {'is_already_professional': True})), \
             patch('core.content_pipeline.generators.product_reference_generator.enhance_photo_classic',
                   return_value=b'enhanced-bytes'), \
             patch.object(gen, '_animate_still_to_clip', return_value=None):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert 'foto mejorada' in reason.lower()


class TestValidateScene:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_when_image_ok(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            ok, data = gen._validate_scene(b'fake-png')
        assert ok is True
        assert data.get('ok') is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_has_text(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": false}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            ok, data = gen._validate_scene(b'fake-png')
        assert ok is False
        assert data.get('has_text') is True

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
            ok, data = gen._validate_scene(b'fake-png')
        assert ok is True  # fail-open, mismo criterio que _validate_background
        assert data == {}

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_suggestive_content_detected(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, '
                '"has_malformed_object": false, "has_unrealistic_grounding": false, '
                '"has_suggestive_or_exposed_content": true, "ok": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            ok, data = gen._validate_scene(b'fake-png')
        assert ok is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash',
    )
    def test_validate_scene_call_disables_thinking(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._validate_scene(b'fake-png')
            call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
        assert call_kwargs['config'].thinking_config.thinking_budget == 0


class TestDescribeQcFailure:
    def test_screenshot_pattern_wins_over_text_alone(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_qc_failure
        msg = _describe_qc_failure({'has_text': True, 'has_screen_content': True})
        assert 'captura de pantalla' in msg.lower()

    def test_text_alone_mentions_watermark(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_qc_failure
        msg = _describe_qc_failure({'has_text': True, 'has_screen_content': False})
        assert 'marca de agua' in msg.lower()

    def test_suggestive_content_message(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_qc_failure
        msg = _describe_qc_failure({'has_suggestive_or_exposed_content': True})
        assert 'sensible' in msg.lower()

    def test_empty_data_returns_generic_message(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_qc_failure
        msg = _describe_qc_failure({})
        assert 'calidad' in msg.lower()


class TestRouteFromTriage:
    def test_screenshot_wins_over_everything(self):
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_REJECT,
        )
        data = {
            'is_screenshot_or_ui': True, 'has_aggressive_watermark': False,
            'product_identity_is_text': True, 'has_full_person_subject': True,
            'is_already_professional': True,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_REJECT

    def test_aggressive_watermark_wins_over_enhance_criteria(self):
        # Caso de prioridad de la spec: aunque is_already_professional=True,
        # si has_aggressive_watermark=True tambien, debe RECHAZAR, no MEJORAR.
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_REJECT,
        )
        data = {
            'is_screenshot_or_ui': False, 'has_aggressive_watermark': True,
            'product_identity_is_text': False, 'has_full_person_subject': False,
            'is_already_professional': True,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_REJECT

    def test_product_identity_is_text_routes_to_enhance(self):
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_ENHANCE,
        )
        data = {
            'is_screenshot_or_ui': False, 'has_aggressive_watermark': False,
            'product_identity_is_text': True, 'has_full_person_subject': False,
            'is_already_professional': False,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_ENHANCE

    def test_full_person_subject_routes_to_enhance(self):
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_ENHANCE,
        )
        data = {
            'is_screenshot_or_ui': False, 'has_aggressive_watermark': False,
            'product_identity_is_text': False, 'has_full_person_subject': True,
            'is_already_professional': False,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_ENHANCE

    def test_already_professional_routes_to_enhance(self):
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_ENHANCE,
        )
        data = {
            'is_screenshot_or_ui': False, 'has_aggressive_watermark': False,
            'product_identity_is_text': False, 'has_full_person_subject': False,
            'is_already_professional': True,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_ENHANCE

    def test_no_flags_set_routes_to_regenerate(self):
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_REGENERATE,
        )
        data = {
            'is_screenshot_or_ui': False, 'has_aggressive_watermark': False,
            'product_identity_is_text': False, 'has_full_person_subject': False,
            'is_already_professional': False,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_REGENERATE

    def test_empty_data_routes_to_regenerate(self):
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_REGENERATE,
        )
        assert _route_from_triage({}) == _TRIAGE_ROUTE_REGENERATE


class TestDescribeTriageRejection:
    def test_screenshot_message(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_triage_rejection
        msg = _describe_triage_rejection({'is_screenshot_or_ui': True})
        assert 'captura de pantalla' in msg.lower()

    def test_watermark_message(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_triage_rejection
        msg = _describe_triage_rejection({'has_aggressive_watermark': True})
        assert 'marca de agua' in msg.lower()

    def test_generic_message_when_no_flags(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_triage_rejection
        msg = _describe_triage_rejection({})
        assert msg == 'La foto no pudo procesarse. Intenta con otra foto.'


class TestTriage:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_calls_gemini_and_returns_route_from_response(self):
        from core.content_pipeline.generators.product_reference_generator import (
            ProductReferenceGenerator, _TRIAGE_ROUTE_REGENERATE,
        )
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"is_screenshot_or_ui": false, "has_aggressive_watermark": false, '
                '"product_identity_is_text": false, "has_full_person_subject": false, '
                '"is_already_professional": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            route, data = gen._triage(b'fake-photo-bytes')
        assert route == _TRIAGE_ROUTE_REGENERATE
        assert data.get('is_screenshot_or_ui') is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fails_open_to_regenerate_on_exception(self):
        from core.content_pipeline.generators.product_reference_generator import (
            ProductReferenceGenerator, _TRIAGE_ROUTE_REGENERATE,
        )
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_text_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            route, data = gen._triage(b'fake-photo-bytes')
        assert route == _TRIAGE_ROUTE_REGENERATE
        assert data == {}

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash',
    )
    def test_triage_call_disables_thinking(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"is_screenshot_or_ui": false, "has_aggressive_watermark": false, '
                '"product_identity_is_text": false, "has_full_person_subject": false, '
                '"is_already_professional": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._triage(b'fake-photo-bytes')
            call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
        assert call_kwargs['config'].thinking_config.thinking_budget == 0


class TestAnimateStillToClip:
    def test_preserves_aspect_ratio_with_letterbox_padding(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        fake_output = b'fake-animated-mp4-bytes'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.product_reference_generator.subprocess.run',
                    side_effect=fake_run) as mock_run:
            result = gen._animate_still_to_clip(b'fake-square-image-bytes')

        assert result == fake_output
        cmd = mock_run.call_args.args[0]
        vf_idx = cmd.index('-vf')
        vf = cmd[vf_idx + 1]
        assert 'force_original_aspect_ratio=decrease' in vf
        assert 'pad=1080:1920' in vf
        assert 's=1080x1920:fps=24' in vf
        assert 'zoompan' in vf

    def test_returns_none_when_ffmpeg_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator.subprocess.run',
                    side_effect=Exception('ffmpeg not found')):
            result = gen._animate_still_to_clip(b'fake-image-bytes')
        assert result is None
