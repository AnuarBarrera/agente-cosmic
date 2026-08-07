import io
import json
import os
from unittest.mock import patch, MagicMock, call
import pytest
from django.test import override_settings
from google.cloud import vision
from PIL import Image


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
            result = gen._generate_showcase(b'fake-enhanced-photo', '#1a1a2e', '#3ED694', 'compositions/confetti-fall.html', 'sway_dolly')

        assert result == fake_output
        assert captured['variables']['primary_color'] == '#1a1a2e'
        assert captured['variables']['secondary_color'] == '#3ED694'
        assert captured['variables']['photo_src'].startswith('assets/tmp/')
        # 'fake-enhanced-photo' no es una imagen real -- fallback seguro a 1.0 (cuadrado)
        assert captured['variables']['photo_aspect'] == 1.0
        assert captured['variables']['camera_motion'] == 'sway_dolly'
        assert captured['cmd'][captured['cmd'].index('-c') + 1] == 'compositions/confetti-fall.html'
        mock_record.assert_called_once_with('product_showcase')
        # El archivo temporal de la foto se limpia despues del render
        from core.content_pipeline.generators.product_showcase_generator import _HYPERFRAMES_PROJECT_DIR
        photo_filename = captured['variables']['photo_src'].split('/')[-1]
        assert not os.path.exists(os.path.join(_HYPERFRAMES_PROJECT_DIR, 'assets', 'tmp', photo_filename))

    def test_computes_photo_aspect_from_real_image(self, tmp_path):
        # HALLAZGO 87: la foto ya no se recorta a cuadrado -- el aspect ratio real
        # (ancho/alto) se calcula y se pasa al template 3D para que ajuste su plano.
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        buf = io.BytesIO()
        Image.new('RGB', (200, 100), color='red').save(buf, format='PNG')  # aspect 2.0
        captured = {}

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[cmd.index('--variables-file') + 1]) as f:
                captured['variables'] = json.load(f)
            output_path = cmd[cmd.index('-o') + 1]
            with open(output_path, 'wb') as f:
                f.write(b'fake-output')

        with patch('core.content_pipeline.generators.product_showcase_generator.subprocess.run', side_effect=fake_run), \
             patch('core.content_pipeline.generators.product_showcase_generator.record_hyperframes_generation'):
            gen._generate_showcase(buf.getvalue(), '#1a1a2e', '#3ED694', 'compositions/confetti-fall.html', 'sway_dolly')

        assert captured['variables']['photo_aspect'] == 2.0

    def test_returns_none_on_subprocess_error(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.subprocess.run',
                   side_effect=Exception('render failed')):
            result = gen._generate_showcase(b'fake-enhanced-photo', '#1a1a2e', '#3ED694', 'compositions/confetti-fall.html', 'sway_dolly')
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
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator, _SHOWCASE_COMPOSITIONS
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_choose_showcase_selection', return_value=('confetti-fall', 'sway_dolly')), \
             patch.object(gen, '_generate_showcase', return_value=b'video-bytes') as mock_showcase, \
             patch.object(gen, '_extract_frame', return_value=b'poster-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://poster.url', 'https://video.url']) as mock_upload:
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo', 'job1-sample', colors=['#111111', '#222222'])

        assert reason == ''
        assert poster_url == 'https://poster.url'
        assert video_url == 'https://video.url'
        mock_showcase.assert_called_once_with(b'enhanced', '#111111', '#222222', _SHOWCASE_COMPOSITIONS['confetti-fall'], 'sway_dolly')

    def test_retries_once_when_showcase_generation_fails(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_choose_showcase_selection', return_value=('confetti-fall', 'sway_dolly')), \
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
             patch.object(gen, '_choose_showcase_selection', return_value=('confetti-fall', 'sway_dolly')), \
             patch.object(gen, '_generate_showcase', return_value=None) as mock_showcase:
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo', 'job1-sample')
        assert video_url == '' and poster_url == ''
        assert 'no se pudo generar' in reason.lower()
        assert mock_showcase.call_count == 2

    def test_uses_fallback_colors_when_none_provided(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator, _SHOWCASE_COMPOSITIONS
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_choose_showcase_selection', return_value=('confetti-fall', 'sway_dolly')), \
             patch.object(gen, '_generate_showcase', return_value=b'video-bytes') as mock_showcase, \
             patch.object(gen, '_extract_frame', return_value=b'poster-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://poster.url', 'https://video.url']):
            gen.generate_reel(b'fake-photo', 'job1-sample')
        mock_showcase.assert_called_once_with(b'enhanced', '#e94560', '#3ED694', _SHOWCASE_COMPOSITIONS['confetti-fall'], 'sway_dolly')


class TestChooseShowcaseSelection:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash',
    )
    def test_returns_template_and_camera_motion_chosen_by_gemini(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"template": "frame-assembly", "camera_motion": "slow_orbit"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            template, camera_motion = gen._choose_showcase_selection('elegante y premium')
        assert template == 'frame-assembly'
        assert camera_motion == 'slow_orbit'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash',
    )
    def test_falls_back_to_random_on_api_error(self):
        from core.content_pipeline.generators.product_showcase_generator import (
            ProductShowcaseGenerator, _SHOWCASE_TEMPLATES, _CAMERA_MOTIONS,
        )
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator._vertex_text_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            template, camera_motion = gen._choose_showcase_selection('tono cualquiera')
        assert template in _SHOWCASE_TEMPLATES
        assert camera_motion in _CAMERA_MOTIONS

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash',
    )
    def test_falls_back_to_random_on_invalid_template_name(self):
        from core.content_pipeline.generators.product_showcase_generator import (
            ProductShowcaseGenerator, _SHOWCASE_TEMPLATES, _CAMERA_MOTIONS,
        )
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"template": "not-a-real-template", "camera_motion": "sway_dolly"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            template, camera_motion = gen._choose_showcase_selection('tono cualquiera')
        assert template in _SHOWCASE_TEMPLATES
        assert camera_motion in _CAMERA_MOTIONS

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash',
    )
    def test_invalid_template_only_randomizes_template_keeps_valid_camera_motion(self):
        # Cada dimension se valida/randomiza de forma independiente: si solo el
        # template es invalido, el camera_motion valido elegido por Gemini se
        # conserva en vez de randomizarse tambien (antes: 1 invalida tiraba las 2).
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator._vertex_text_client') as mock_vc, \
             patch('core.content_pipeline.generators.product_showcase_generator.random.choice',
                   return_value='confetti-fall') as mock_choice:
            mock_resp = MagicMock()
            mock_resp.text = '{"template": "not-a-real-template", "camera_motion": "sway_dolly"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            template, camera_motion = gen._choose_showcase_selection('tono cualquiera')
        assert template == 'confetti-fall'
        assert camera_motion == 'sway_dolly'
        mock_choice.assert_called_once()

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash',
    )
    def test_falls_back_to_random_on_invalid_camera_motion(self):
        from core.content_pipeline.generators.product_showcase_generator import (
            ProductShowcaseGenerator, _SHOWCASE_TEMPLATES, _CAMERA_MOTIONS,
        )
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator._vertex_text_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"template": "confetti-fall", "camera_motion": "not-a-real-motion"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            template, camera_motion = gen._choose_showcase_selection('tono cualquiera')
        assert template in _SHOWCASE_TEMPLATES
        assert camera_motion in _CAMERA_MOTIONS

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash',
    )
    def test_invalid_camera_motion_only_randomizes_camera_motion_keeps_valid_template(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator._vertex_text_client') as mock_vc, \
             patch('core.content_pipeline.generators.product_showcase_generator.random.choice',
                   return_value='sway_dolly') as mock_choice:
            mock_resp = MagicMock()
            mock_resp.text = '{"template": "frame-assembly", "camera_motion": "not-a-real-motion"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            template, camera_motion = gen._choose_showcase_selection('tono cualquiera')
        assert template == 'frame-assembly'
        assert camera_motion == 'sway_dolly'
        mock_choice.assert_called_once()



class TestGenerateReelUsesChosenTemplate:
    def test_generate_reel_passes_composition_path_from_chosen_template(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator, _SHOWCASE_COMPOSITIONS
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_choose_showcase_selection', return_value=('glass-shatter-reveal', 'static_hold')) as mock_choose, \
             patch.object(gen, '_generate_showcase', return_value=b'video-bytes') as mock_showcase, \
             patch.object(gen, '_extract_frame', return_value=b'poster-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://poster.url', 'https://video.url']):
            gen.generate_reel(b'fake-photo', 'job1-sample', colors=['#111111', '#222222'], tone='dramatico')

        mock_choose.assert_called_once_with('dramatico')
        mock_showcase.assert_called_once_with(
            b'enhanced', '#111111', '#222222', _SHOWCASE_COMPOSITIONS['glass-shatter-reveal'], 'static_hold',
        )

    def test_generate_reel_works_with_empty_tone(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_choose_showcase_selection', return_value=('confetti-fall', 'sway_dolly')) as mock_choose, \
             patch.object(gen, '_generate_showcase', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=b'poster-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://poster.url', 'https://video.url']):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo', 'job1-sample')

        assert reason == ''
        mock_choose.assert_called_once_with('')


class TestShowcaseCatalogIntegrity:
    def test_composition_keys_match_templates_list(self):
        from core.content_pipeline.generators.product_showcase_generator import _SHOWCASE_TEMPLATES, _SHOWCASE_COMPOSITIONS
        assert set(_SHOWCASE_COMPOSITIONS.keys()) == set(_SHOWCASE_TEMPLATES)

    def test_all_composition_files_exist_on_disk(self):
        import os
        from core.content_pipeline.generators.product_showcase_generator import _SHOWCASE_COMPOSITIONS, _HYPERFRAMES_PROJECT_DIR
        for path in _SHOWCASE_COMPOSITIONS.values():
            assert os.path.exists(os.path.join(_HYPERFRAMES_PROJECT_DIR, path)), f"falta {path}"

    def test_all_composition_files_declare_camera_motion_variable(self):
        from core.content_pipeline.generators.product_showcase_generator import _SHOWCASE_COMPOSITIONS, _HYPERFRAMES_PROJECT_DIR
        for template, path in _SHOWCASE_COMPOSITIONS.items():
            full_path = os.path.join(_HYPERFRAMES_PROJECT_DIR, path)
            with open(full_path) as f:
                content = f.read()
            assert '"id":"camera_motion"' in content, f"{template} no declara camera_motion"

    def test_poster_offset_keys_match_templates_list(self):
        from core.content_pipeline.generators.product_showcase_generator import _SHOWCASE_TEMPLATES, _SHOWCASE_POSTER_OFFSETS
        assert set(_SHOWCASE_POSTER_OFFSETS.keys()) == set(_SHOWCASE_TEMPLATES)

    def test_camera_motion_functions_have_no_drift_across_templates(self):
        # Las 3 composiciones definen sus propias 3 funciones applyCameraMotion_*
        # (sin modulo compartido, por diseño -- ver comentarios en cada archivo).
        # Este test detecta si alguien edita una copia sin replicar el cambio en
        # las otras 2. Se normalizan comentarios de linea y espacios en blanco
        # porque los comentarios inline de cada archivo pueden diferir levemente
        # (ver cada composicion) -- la logica (lo que importa aqui) es identica
        # en los 3.
        import re
        from core.content_pipeline.generators.product_showcase_generator import _SHOWCASE_COMPOSITIONS, _HYPERFRAMES_PROJECT_DIR

        def normalize(block: str) -> str:
            no_comments = re.sub(r'//[^\n]*', '', block)
            return re.sub(r'\s+', ' ', no_comments).strip()

        normalized_blocks = {}
        for template, path in _SHOWCASE_COMPOSITIONS.items():
            full_path = os.path.join(_HYPERFRAMES_PROJECT_DIR, path)
            with open(full_path) as f:
                content = f.read()
            start = content.index('function applyCameraMotion_swayDolly')
            end = content.index('applyCameraMotion(t, camera, cardGroup)', start)
            block = content[start:end]
            normalized_blocks[template] = normalize(block)

        distinct_versions = set(normalized_blocks.values())
        assert len(distinct_versions) == 1, (
            f"applyCameraMotion_* divergio entre templates: {normalized_blocks}"
        )

    def test_shadow_setup_has_no_drift_across_templates(self):
        # Las 3 composiciones configuran su unica luz con sombra (keyLight) con
        # el mismo bloque de codigo (mapSize + limites de frustum de la camara
        # de sombra) -- sin modulo compartido, por diseño -- ver comentarios en
        # cada archivo. Este test detecta si alguien edita una copia sin
        # replicar el cambio en las otras 2. Se normalizan comentarios de linea
        # y espacios en blanco porque los comentarios inline de cada archivo
        # difieren a proposito (cada uno explica que objeto de ESE template es
        # el que castea sombra) -- la logica (lo que importa aqui) es identica
        # en los 3.
        import re
        from core.content_pipeline.generators.product_showcase_generator import _SHOWCASE_COMPOSITIONS, _HYPERFRAMES_PROJECT_DIR

        def normalize(block: str) -> str:
            no_comments = re.sub(r'//[^\n]*', '', block)
            return re.sub(r'\s+', ' ', no_comments).strip()

        normalized_blocks = {}
        for template, path in _SHOWCASE_COMPOSITIONS.items():
            full_path = os.path.join(_HYPERFRAMES_PROJECT_DIR, path)
            with open(full_path) as f:
                content = f.read()
            start = content.index('renderer.shadowMap.enabled = true')
            end = content.index('scene.add(keyLight);', start)
            block = content[start:end]
            normalized_blocks[template] = normalize(block)

        distinct_versions = set(normalized_blocks.values())
        assert len(distinct_versions) == 1, (
            f"bloque de sombra divergio entre templates: {normalized_blocks}"
        )
