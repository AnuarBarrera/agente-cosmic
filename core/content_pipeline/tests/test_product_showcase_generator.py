import json
import os
from unittest.mock import patch, MagicMock, call
import pytest
from django.test import override_settings
from google.cloud import vision


def _safe_search_response(adult='VERY_UNLIKELY', violence='VERY_UNLIKELY', racy='VERY_UNLIKELY', labels=None):
    likelihood = {
        'VERY_UNLIKELY': vision.Likelihood.VERY_UNLIKELY, 'UNLIKELY': vision.Likelihood.UNLIKELY,
        'POSSIBLE': vision.Likelihood.POSSIBLE, 'LIKELY': vision.Likelihood.LIKELY,
        'VERY_LIKELY': vision.Likelihood.VERY_LIKELY,
    }
    resp = MagicMock()
    resp.safe_search_annotation.adult = likelihood[adult]
    resp.safe_search_annotation.violence = likelihood[violence]
    resp.safe_search_annotation.racy = likelihood[racy]
    label_mocks = []
    for description, score in (labels or []):
        label_mock = MagicMock()
        label_mock.description = description
        label_mock.score = score
        label_mocks.append(label_mock)
    resp.label_annotations = label_mocks
    return resp


class TestCheckPhotoSafety:
    @override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic')
    def test_passes_clean_photo(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.vision.ImageAnnotatorClient') as mock_vc:
            mock_vc.return_value.annotate_image.return_value = _safe_search_response(
                labels=[('Food', 0.9), ('Ingredient', 0.8)],
            )
            result = gen._check_photo_safety(b'fake-photo-bytes')
        assert result == ''
        mock_vc.assert_called_once_with(client_options={'quota_project_id': 'agente-cosmic'})

    def test_rejects_screenshot_label(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.vision.ImageAnnotatorClient') as mock_vc:
            mock_vc.return_value.annotate_image.return_value = _safe_search_response(
                labels=[('Screenshot', 0.83), ('Text', 0.95)],
            )
            result = gen._check_photo_safety(b'fake-photo-bytes')
        assert 'captura de pantalla' in result.lower()

    def test_rejects_adult_content(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.vision.ImageAnnotatorClient') as mock_vc:
            mock_vc.return_value.annotate_image.return_value = _safe_search_response(adult='VERY_LIKELY')
            result = gen._check_photo_safety(b'fake-photo-bytes')
        assert 'sensible' in result.lower()

    def test_does_not_reject_heavy_text_overlay_without_screenshot_label(self):
        # Caso real de HALLAZGO IMG-13 (gelopaleta_stitch.jpg): mucho texto/graficos
        # superpuestos pero SIN la etiqueta Screenshot -- no debe rechazarse.
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.vision.ImageAnnotatorClient') as mock_vc:
            mock_vc.return_value.annotate_image.return_value = _safe_search_response(
                labels=[('Plastic', 0.59), ('Toy', 0.57), ('Party Supply', 0.57)],
            )
            result = gen._check_photo_safety(b'fake-photo-bytes')
        assert result == ''

    def test_fails_open_on_exception(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.vision.ImageAnnotatorClient',
                   side_effect=Exception('API error')):
            result = gen._check_photo_safety(b'fake-photo-bytes')
        assert result == ''


class TestGenerateShowcase:
    def test_builds_variables_and_renders(self, tmp_path):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        fake_output = b'fake-showcase-mp4'
        captured = {}

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[cmd.index('--variables-file') + 1]) as f:
                captured['variables'] = json.load(f)
            captured['cmd'] = cmd
            captured['cwd'] = kwargs.get('cwd')
            output_path = cmd[cmd.index('-o') + 1]
            with open(output_path, 'wb') as f:
                f.write(fake_output)

        with patch('core.content_pipeline.generators.product_showcase_generator.subprocess.run', side_effect=fake_run), \
             patch('core.content_pipeline.generators.product_showcase_generator.record_hyperframes_generation') as mock_record:
            result = gen._generate_showcase(b'fake-enhanced-photo', '#1a1a2e', '#3ED694')

        assert result == fake_output
        assert captured['variables']['primary_color'] == '#1a1a2e'
        assert captured['variables']['secondary_color'] == '#3ED694'
        assert captured['variables']['photo_src'].startswith('assets/tmp/')
        assert captured['cmd'][captured['cmd'].index('-c') + 1] == 'compositions/product-showcase.html'
        mock_record.assert_called_once_with('product_showcase')
        # El archivo temporal de la foto se limpia despues del render
        from core.content_pipeline.generators.product_showcase_generator import _HYPERFRAMES_PROJECT_DIR
        photo_filename = captured['variables']['photo_src'].split('/')[-1]
        assert not os.path.exists(os.path.join(_HYPERFRAMES_PROJECT_DIR, 'assets', 'tmp', photo_filename))

    def test_returns_none_on_subprocess_error(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.subprocess.run',
                   side_effect=Exception('render failed')):
            result = gen._generate_showcase(b'fake-enhanced-photo', '#1a1a2e', '#3ED694')
        assert result is None


class TestGenerateReel:
    def test_rejects_via_safety_gate(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value='mensaje de rechazo'):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo', 'job1-sample')
        assert video_url == '' and poster_url == ''
        assert reason == 'mensaje de rechazo'

    def test_happy_path_uploads_video_and_poster(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_generate_showcase', return_value=b'video-bytes') as mock_showcase, \
             patch.object(gen, '_extract_frame', return_value=b'poster-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://poster.url', 'https://video.url']) as mock_upload:
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo', 'job1-sample', colors=['#111111', '#222222'])

        assert reason == ''
        assert poster_url == 'https://poster.url'
        assert video_url == 'https://video.url'
        mock_showcase.assert_called_once_with(b'enhanced', '#111111', '#222222')

    def test_retries_once_when_showcase_generation_fails(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_generate_showcase', side_effect=[None, b'video-bytes']) as mock_showcase, \
             patch.object(gen, '_extract_frame', return_value=b'poster-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://poster.url', 'https://video.url']):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo', 'job1-sample')
        assert reason == ''
        assert mock_showcase.call_count == 2

    def test_gives_up_after_retry_fails(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_generate_showcase', return_value=None) as mock_showcase:
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo', 'job1-sample')
        assert video_url == '' and poster_url == ''
        assert 'no se pudo generar' in reason.lower()
        assert mock_showcase.call_count == 2

    def test_uses_fallback_colors_when_none_provided(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_generate_showcase', return_value=b'video-bytes') as mock_showcase, \
             patch.object(gen, '_extract_frame', return_value=b'poster-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://poster.url', 'https://video.url']):
            gen.generate_reel(b'fake-photo', 'job1-sample')
        mock_showcase.assert_called_once_with(b'enhanced', '#e94560', '#3ED694')
