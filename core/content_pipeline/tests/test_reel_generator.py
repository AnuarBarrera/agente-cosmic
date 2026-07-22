from unittest.mock import patch, MagicMock, call
from django.test import override_settings
import json
import subprocess


from core.content_pipeline.generators.reel_generator import (
    _escape_drawtext, _wrap_text, _hex_to_ffmpeg_color, _measure_text_width,
    _build_hook_filter_parts, _build_cta_filter_parts, _CTA_FONTSIZE,
    _readable_text_color, _write_drawtext_textfile,
)


class TestEscapeDrawtext:
    # HALLAZGO (analisisPipeline.md, 2026-07-22): con text='...' inline, NINGUNA
    # secuencia de escape probada para el apostrofe hacia que ffmpeg renderizara
    # el texto sin fallar o sin quedar vacio (verificado empiricamente). El fix
    # es leer el texto desde archivo (textfile=, ver _write_drawtext_textfile) —
    # ahi ':' y "'" ya no pasan por el parser de comillas del filtergraph y no
    # necesitan escape. Solo '\' y '%' siguen necesitando escape (sintaxis de
    # expansion propia de drawtext, %{...}, se aplica igual via textfile).
    def test_does_not_escape_colon(self):
        assert _escape_drawtext('Hola: bienvenido') == 'Hola: bienvenido'

    def test_does_not_escape_single_quote(self):
        assert _escape_drawtext("Tu 'mejor' opcion") == "Tu 'mejor' opcion"

    def test_escapes_percent(self):
        assert _escape_drawtext('50% de descuento') == '50\\% de descuento'

    def test_escapes_backslash_first(self):
        assert _escape_drawtext('a\\b') == 'a\\\\b'


class TestWriteDrawtextTextfile:
    def test_writes_escaped_text_and_returns_path(self, tmp_path):
        path = _write_drawtext_textfile(str(tmp_path), 'hook0.txt', "Maika Pet's: 50% off")
        with open(path, encoding='utf-8') as f:
            content = f.read()
        assert content == "Maika Pet's: 50\\% off"
        assert path.endswith('hook0.txt')


class TestWrapText:
    def test_returns_unchanged_when_short(self):
        assert _wrap_text('Hola mundo') == 'Hola mundo'

    def test_wraps_long_text_into_two_lines(self):
        text = 'Tu negocio en linea en menos de 48 horas'
        result = _wrap_text(text, max_chars=20)
        assert result == 'Tu negocio en linea\nen menos de 48 horas'


class TestHexToFfmpegColor:
    def test_converts_hash_prefix_to_0x(self):
        assert _hex_to_ffmpeg_color('#002951') == '0x002951'

    def test_handles_missing_hash(self):
        assert _hex_to_ffmpeg_color('002951') == '0x002951'


class TestReadableTextColor:
    def test_dark_background_gets_white_text(self):
        # #1a1a2e es el fallback usado cuando una marca no tiene primary_colors —
        # el mismo color que antes estaba hardcodeado como fontcolor, causando
        # texto invisible sobre su propia caja cuando el color primario coincidia.
        assert _readable_text_color('#1a1a2e') == 'white'

    def test_light_background_gets_black_text(self):
        assert _readable_text_color('#f5f5f5') == 'black'

    def test_handles_hash_and_no_hash_prefix(self):
        assert _readable_text_color('#1a1a2e') == _readable_text_color('1a1a2e')


class TestSplitHighlight:
    def test_splits_around_highlight_word(self):
        from core.content_pipeline.generators.reel_generator import _split_highlight
        before, highlight, after = _split_highlight('Descubre algo nuevo', 'algo')
        assert before == 'Descubre '
        assert highlight == 'algo'
        assert after == ' nuevo'

    def test_case_insensitive_match(self):
        from core.content_pipeline.generators.reel_generator import _split_highlight
        before, highlight, after = _split_highlight('Descubre ALGO nuevo', 'algo')
        assert highlight == 'ALGO'

    def test_returns_full_text_as_before_when_word_not_found(self):
        from core.content_pipeline.generators.reel_generator import _split_highlight
        before, highlight, after = _split_highlight('Descubre algo nuevo', 'inexistente')
        assert before == 'Descubre algo nuevo'
        assert highlight == ''
        assert after == ''

    def test_returns_full_text_as_before_when_no_highlight_word(self):
        from core.content_pipeline.generators.reel_generator import _split_highlight
        before, highlight, after = _split_highlight('Descubre algo nuevo', '')
        assert before == 'Descubre algo nuevo'
        assert highlight == ''
        assert after == ''


class TestMeasureTextWidth:
    def test_empty_string_is_zero(self):
        assert _measure_text_width('', 64) == 0

    def test_longer_text_is_wider(self):
        short = _measure_text_width('Hola', 64)
        long_ = _measure_text_width('Hola mundo entero', 64)
        assert short > 0
        assert long_ > short


def _read_textfile_from_filter(filter_part: str) -> str:
    # Extrae la ruta de textfile=... del fragmento de filtro (termina en ':' o
    # '[') y devuelve el contenido real escrito en disco — el reemplazo directo
    # de "assert \"text='...'\" in parts[0]" ahora que el texto vive en archivo.
    start = filter_part.index('textfile=') + len('textfile=')
    end = min(i for i in (filter_part.find(':', start), filter_part.find('[', start)) if i != -1)
    path = filter_part[start:end]
    with open(path, encoding='utf-8') as f:
        return f.read()


class TestBuildHookFilterParts:
    def test_plain_line_when_highlight_not_found(self, tmp_path):
        parts, last_label = _build_hook_filter_parts(
            'Texto sin resaltado', 'inexistente', '#002951', '0:v', str(tmp_path),
        )
        assert len(parts) == 1
        assert parts[0].startswith('[0:v]drawtext=')
        assert _read_textfile_from_filter(parts[0]) == 'Texto sin resaltado'
        assert 'box=1' not in parts[0]
        assert "enable='between(t,0,3)'" in parts[0]
        assert last_label == 'hook0'

    def test_splits_line_around_highlight_word(self, tmp_path):
        # 'nuevo' esta al final de la frase (20 caracteres, no se envuelve) ->
        # queda un segmento 'before' + el resaltado, sin segmento 'after'.
        parts, last_label = _build_hook_filter_parts(
            'Descubre algo nuevo', 'nuevo', '#002951', '0:v', str(tmp_path),
        )
        assert any(_read_textfile_from_filter(p) == 'Descubre algo ' for p in parts)
        highlight_parts = [p for p in parts if _read_textfile_from_filter(p) == 'nuevo']
        assert len(highlight_parts) == 1
        assert 'box=1' in highlight_parts[0]
        assert 'boxcolor=0x002951@1.0' in highlight_parts[0]
        assert 'fontcolor=white' in highlight_parts[0]  # #002951 es oscuro, texto blanco para contraste
        assert last_label == 'hook0b'

    def test_wraps_long_hook_into_multiple_lines(self, tmp_path):
        long_hook = 'Una frase mucho mas larga que el limite de caracteres permitido'
        parts, last_label = _build_hook_filter_parts(
            long_hook, 'inexistente', '#002951', '0:v', str(tmp_path),
        )
        assert len(parts) >= 2

    def test_all_filters_enabled_only_during_first_three_seconds(self, tmp_path):
        parts, _ = _build_hook_filter_parts(
            'Descubre algo nuevo', 'nuevo', '#002951', '0:v', str(tmp_path),
        )
        assert all("enable='between(t,0,3)'" in p for p in parts)

    def test_hook_text_with_apostrophe_writes_intact_textfile(self, tmp_path):
        # HALLAZGO (analisisPipeline.md, 2026-07-22): con text='...' inline
        # esto rompia ffmpeg (exit 8) o renderizaba vacio segun el escape
        # probado — ver comentario en TestEscapeDrawtext. Con textfile=, el
        # apostrofe no necesita ningun tratamiento especial.
        parts, _ = _build_hook_filter_parts(
            "Maika Pet's", 'inexistente', '#002951', '0:v', str(tmp_path),
        )
        assert _read_textfile_from_filter(parts[0]) == "Maika Pet's"


class TestBuildCtaFilterParts:
    def test_builds_single_filter_with_box_and_enable_window(self, tmp_path):
        parts, last_label = _build_cta_filter_parts(
            'Compra ahora', '#002951', 'hook0b', 21.0, 24.0, str(tmp_path),
        )
        assert len(parts) == 1
        assert parts[0].startswith('[hook0b]drawtext=')
        assert _read_textfile_from_filter(parts[0]) == 'Compra ahora'
        assert 'box=1' in parts[0]
        assert 'boxcolor=0x002951@1.0' in parts[0]
        assert 'fontcolor=white' in parts[0]  # #002951 es oscuro, texto blanco para contraste
        assert "enable='between(t,21.0,24.0)'" in parts[0]
        assert last_label == 'cta0'

    def test_light_primary_color_gets_black_text(self, tmp_path):
        parts, _ = _build_cta_filter_parts(
            'Compra ahora', '#f5f5f5', '0:v', 21.0, 24.0, str(tmp_path),
        )
        assert 'fontcolor=black' in parts[0]

    def test_scale_shrinks_fontsize_and_box_border(self, tmp_path):
        parts_full, _ = _build_cta_filter_parts(
            'Compra ahora', '#002951', '0:v', 21.0, 24.0, str(tmp_path), scale=1.0,
        )
        parts_scaled, _ = _build_cta_filter_parts(
            'Compra ahora', '#002951', '0:v', 21.0, 24.0, str(tmp_path), scale=0.5,
        )
        assert f'fontsize={_CTA_FONTSIZE}' in parts_full[0]
        assert f'fontsize={_CTA_FONTSIZE // 2}' in parts_scaled[0]


class TestGenerateSingleClip:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
    def test_negative_prompt_passed_via_config_not_appended_to_prompt(self):
        # Concatenar "NO icons/UI elements" al prompt afirmativo puede hacer que
        # el modelo los genere de todos modos (alucinacion real observada: icono
        # de boton de play incrustado en una escena). El canal correcto es el
        # parametro negative_prompt de la API, no el texto del prompt principal.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_video = b'fake-video-bytes'
        mock_video = MagicMock()
        mock_video.video_bytes = fake_video
        mock_generated = MagicMock()
        mock_generated.video = mock_video
        mock_op = MagicMock()
        mock_op.done = True
        mock_op.error = None
        mock_op.result.generated_videos = [mock_generated]
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_videos.return_value = mock_op
            result = gen._generate_single_clip('a workshop scene')

        assert result == fake_video
        call_kwargs = mock_vc.return_value.models.generate_videos.call_args.kwargs
        assert call_kwargs['prompt'] == 'a workshop scene'
        assert call_kwargs['config'].negative_prompt == gen._VEO_SAFE_CONSTRAINTS.strip()
        assert 'NO icons' not in call_kwargs['prompt']


class TestGenerateVideoClips:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
    def test_first_scene_via_veo_rest_via_imagen_zoompan(self):
        from core.content_pipeline.generators.reel_generator import (
            ReelGenerator, _IMAGE_SHOT_DURATION_SECONDS,
        )
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_single_clip', return_value=b'veo-clip') as mock_veo, \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)) as mock_probe, \
             patch.object(gen, '_generate_scene_still', return_value=b'still-bytes') as mock_still, \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
            clips = gen._generate_video_clips(
                ['scene 1', 'scene 2', 'scene 3', 'scene 4', 'scene 5', 'scene 6']
            )

        assert clips == [b'veo-clip'] + [b'animated-clip'] * 5
        mock_veo.assert_called_once_with('scene 1')
        mock_probe.assert_called_once_with(b'veo-clip')
        assert mock_still.call_args_list == [
            call('scene 2'), call('scene 3'), call('scene 4'), call('scene 5'), call('scene 6'),
        ]
        assert mock_animate.call_args_list == [
            call(b'still-bytes', 720, 1280, 24.0, duration=_IMAGE_SHOT_DURATION_SECONDS),
        ] * 5

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
    def test_falls_back_to_imagen_when_veo_scene_fails_completely(self):
        from core.content_pipeline.generators.reel_generator import (
            ReelGenerator, _VIDEO_WIDTH, _VIDEO_HEIGHT, _DEFAULT_CLIP_FPS,
            _VEO_CLIP_DURATION_SECONDS, _IMAGE_SHOT_DURATION_SECONDS,
        )
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_single_clip', return_value=None) as mock_veo, \
             patch.object(gen, '_probe_clip_dimensions') as mock_probe, \
             patch.object(gen, '_generate_scene_still', return_value=b'still-bytes'), \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
            clips = gen._generate_video_clips(
                ['scene 1', 'scene 2', 'scene 3', 'scene 4', 'scene 5', 'scene 6']
            )

        assert mock_veo.call_count == 2  # 1 intento + 1 reintento, ambos fallan
        mock_probe.assert_not_called()
        assert clips == [b'animated-clip'] * 6
        assert mock_animate.call_count == 6
        assert mock_animate.call_args_list[0] == call(
            b'still-bytes', _VIDEO_WIDTH, _VIDEO_HEIGHT, _DEFAULT_CLIP_FPS,
            duration=_VEO_CLIP_DURATION_SECONDS,
        )
        assert mock_animate.call_args_list[1] == call(
            b'still-bytes', _VIDEO_WIDTH, _VIDEO_HEIGHT, _DEFAULT_CLIP_FPS,
            duration=_IMAGE_SHOT_DURATION_SECONDS,
        )

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
    def test_skips_imagen_scene_that_fails_completely(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_single_clip', return_value=b'veo-clip'), \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)), \
             patch.object(gen, '_generate_scene_still', return_value=None) as mock_still, \
             patch.object(gen, '_animate_still_to_clip') as mock_animate:
            clips = gen._generate_video_clips(
                ['scene 1', 'scene 2', 'scene 3', 'scene 4', 'scene 5', 'scene 6']
            )

        assert clips == [b'veo-clip']
        assert mock_still.call_count == 10  # 5 escenas x (1 intento + 1 reintento)
        mock_animate.assert_not_called()

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
    def test_single_clip_returns_none_if_operation_never_completes_within_timeout(self):
        # La operacion de larga duracion (LRO) de Veo puede quedarse en done=False
        # indefinidamente sin devolver error — sin un limite de tiempo el polling
        # espera para siempre. Se simula tiempo avanzando mas alla del limite sin
        # dormir de verdad (time.sleep y time.monotonic mockeados).
        from core.content_pipeline.generators.reel_generator import (
            ReelGenerator, _VEO_POLL_TIMEOUT_SECONDS,
        )
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_op = MagicMock()
        mock_op.done = False
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc, \
             patch('core.content_pipeline.generators.reel_generator.time.sleep') as mock_sleep, \
             patch('core.content_pipeline.generators.reel_generator.time.monotonic') as mock_monotonic:
            mock_vc.return_value.models.generate_videos.return_value = mock_op
            mock_vc.return_value.operations.get.return_value = mock_op
            # 2 llamadas de track_external_api (start/elapsed, comparten el mismo
            # modulo time con reel_generator) + poll_start + 1er chequeo de limite.
            mock_monotonic.side_effect = [0, 0, 0, _VEO_POLL_TIMEOUT_SECONDS + 1]
            result = gen._generate_single_clip('prompt')
        assert result is None
        mock_sleep.assert_not_called()

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
    def test_single_clip_keeps_polling_while_under_timeout(self):
        # Una operacion lenta pero real (ej. 24 min observados en produccion) NO
        # debe cortarse antes de tiempo — solo el limite duro debe detenerla.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_video = b'fake-video-bytes'
        mock_video = MagicMock()
        mock_video.video_bytes = fake_video
        mock_generated = MagicMock()
        mock_generated.video = mock_video
        mock_op_pending = MagicMock()
        mock_op_pending.done = False
        mock_op_done = MagicMock()
        mock_op_done.done = True
        mock_op_done.error = None
        mock_op_done.result.generated_videos = [mock_generated]
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc, \
             patch('core.content_pipeline.generators.reel_generator.time.sleep') as mock_sleep, \
             patch('core.content_pipeline.generators.reel_generator.time.monotonic') as mock_monotonic:
            mock_vc.return_value.models.generate_videos.return_value = mock_op_pending
            mock_vc.return_value.operations.get.return_value = mock_op_done
            # 2 llamadas de track_external_api (start/elapsed) + poll_start + 1er
            # chequeo de limite (20 min transcurridos, sigue bajo el limite de 30).
            mock_monotonic.side_effect = [0, 0, 0, 1200]
            result = gen._generate_single_clip('prompt')
        assert result == fake_video
        mock_sleep.assert_called_once_with(10)


class TestProbeVideoDimensions:
    def test_returns_width_height_fps(self):
        from core.content_pipeline.generators.reel_generator import _probe_video_dimensions
        fake_result = MagicMock()
        fake_result.stdout = '720,1280,24/1\n'
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    return_value=fake_result) as mock_run:
            width, height, fps = _probe_video_dimensions('/fake/path.mp4')
        assert (width, height, fps) == (720, 1280, 24.0)
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == 'ffprobe'
        assert '/fake/path.mp4' in cmd

    def test_handles_non_integer_frame_rate(self):
        from core.content_pipeline.generators.reel_generator import _probe_video_dimensions
        fake_result = MagicMock()
        fake_result.stdout = '1080,1920,25000/1001\n'
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    return_value=fake_result):
            _, _, fps = _probe_video_dimensions('/fake/path.mp4')
        assert round(fps, 3) == round(25000 / 1001, 3)


class TestProbeVideoDuration:
    def test_returns_duration_as_float(self):
        from core.content_pipeline.generators.reel_generator import _probe_video_duration
        fake_result = MagicMock()
        fake_result.stdout = '18.5\n'
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    return_value=fake_result) as mock_run:
            duration = _probe_video_duration('/fake/path.mp4')
        assert duration == 18.5
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == 'ffprobe'
        assert 'format=duration' in cmd
        assert '/fake/path.mp4' in cmd


class TestGenerateSceneStill:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_returns_image_bytes_on_success(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_image = b'fake-image-bytes'
        mock_generated = MagicMock()
        mock_generated.image.image_bytes = fake_image
        mock_resp = MagicMock()
        mock_resp.generated_images = [mock_generated]
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc, \
             patch('core.content_pipeline.generators.reel_generator.record_imagen_generation') as mock_record:
            mock_vc.return_value.models.generate_images.return_value = mock_resp
            result = gen._generate_scene_still('a workshop scene')
        assert result == fake_image
        mock_record.assert_called_once_with('reel_scene')
        call_kwargs = mock_vc.return_value.models.generate_images.call_args.kwargs
        assert call_kwargs['model'] == 'imagen-3.0-generate-001'
        assert call_kwargs['config'].aspect_ratio == '9:16'
        # negative_prompt via el parametro dedicado de la API, NO concatenado al
        # prompt afirmativo (mencionar "icons"/"UI elements" en el prompt
        # principal, aunque sea para negarlos, puede hacer que Imagen los genere
        # de todos modos — alucinacion real: icono de boton de play incrustado).
        assert call_kwargs['prompt'] == 'a workshop scene'
        assert call_kwargs['config'].negative_prompt == gen._VEO_SAFE_CONSTRAINTS.strip()
        assert 'NO icons' not in call_kwargs['prompt']

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_returns_none_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_images.side_effect = Exception('rejected')
            result = gen._generate_scene_still('a workshop scene')
        assert result is None

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_returns_none_when_no_images_generated(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_resp = MagicMock()
        mock_resp.generated_images = []
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_images.return_value = mock_resp
            result = gen._generate_scene_still('a workshop scene')
        assert result is None


class TestChooseReelTemplate:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_template_chosen_by_gemini(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"template": "panel-wipe"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._choose_reel_template('Hook de prueba', 'CTA de prueba')
        assert result == 'panel-wipe'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_falls_back_to_random_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator, _REEL_TEMPLATES
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._choose_reel_template('Hook', 'CTA')
        assert result in _REEL_TEMPLATES

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_falls_back_to_random_on_invalid_template_name(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator, _REEL_TEMPLATES
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"template": "not-a-real-template"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._choose_reel_template('Hook', 'CTA')
        assert result in _REEL_TEMPLATES


class TestAnimateStillToClip:
    def test_builds_zoompan_command_with_exact_dimensions(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-animated-mp4-bytes'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=fake_run) as mock_run:
            result = gen._animate_still_to_clip(b'fake-image-bytes', width=720, height=1280, fps=24.0, duration=8)

        assert result == fake_output
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == 'ffmpeg'
        assert cmd[cmd.index('-t') + 1] == '8'
        vf_idx = cmd.index('-vf')
        assert 's=720x1280:fps=24.0' in cmd[vf_idx + 1]
        assert 'zoompan' in cmd[vf_idx + 1]

    def test_uses_default_duration_of_8_seconds(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(b'out')
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=fake_run) as mock_run:
            gen._animate_still_to_clip(b'fake-image-bytes', width=1080, height=1920, fps=24.0)

        cmd = mock_run.call_args.args[0]
        assert cmd[cmd.index('-t') + 1] == '8'


class TestGenerateBrandedSegment:
    def test_portada_builds_variables_and_renders(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-portada-mp4'

        captured = {}

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[cmd.index('--variables-file') + 1]) as f:
                captured['variables'] = json.load(f)
            captured['cmd'] = cmd
            captured['cwd'] = kwargs.get('cwd')
            output_path = cmd[cmd.index('-o') + 1]
            with open(output_path, 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=fake_run), \
             patch('core.content_pipeline.generators.reel_generator.record_hyperframes_generation') as mock_record:
            result = gen._generate_branded_segment(
                'portada', 'Descubre algo nuevo', 'algo', 'Compra ahora', '#1a1a2e',
                'panel-wipe', "'Poppins', sans-serif",
            )

        assert result == fake_output
        assert captured['variables'] == {
            'hook_before': 'Descubre ', 'hook_highlight': 'algo', 'hook_after': ' nuevo',
            'primary_color': '#1a1a2e', 'text_color': 'white', 'font_family': "'Poppins', sans-serif",
        }
        assert '-c' in captured['cmd']
        assert captured['cmd'][captured['cmd'].index('-c') + 1] == 'compositions/portada-panel-wipe.html'
        assert '--fps' in captured['cmd']
        assert captured['cmd'][captured['cmd'].index('--fps') + 1] == '24'
        mock_record.assert_called_once_with('portada')

    def test_contraportada_builds_variables_and_renders(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-contraportada-mp4'

        captured = {}

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[cmd.index('--variables-file') + 1]) as f:
                captured['variables'] = json.load(f)
            captured['cmd'] = cmd
            output_path = cmd[cmd.index('-o') + 1]
            with open(output_path, 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=fake_run), \
             patch('core.content_pipeline.generators.reel_generator.record_hyperframes_generation'):
            result = gen._generate_branded_segment(
                'contraportada', 'Descubre algo nuevo', 'algo', 'Compra ahora', '#1a1a2e',
                'dynamic-background', "'Bebas Neue', sans-serif",
            )

        assert result == fake_output
        assert captured['variables'] == {
            'cta_text': 'Compra ahora', 'primary_color': '#1a1a2e',
            'text_color': 'white', 'font_family': "'Bebas Neue', sans-serif",
        }
        assert captured['cmd'][captured['cmd'].index('-c') + 1] == 'compositions/contraportada-dynamic-background.html'

    def test_returns_none_on_subprocess_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=subprocess.CalledProcessError(1, 'hyperframes')):
            result = gen._generate_branded_segment(
                'portada', 'Descubre algo nuevo', 'algo', 'Compra ahora', '#1a1a2e',
                'panel-wipe', "'Poppins', sans-serif",
            )
        assert result is None


class TestGenerateClipsWithBranding:
    def test_branding_success_prepends_and_appends_normalized_segments(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'v', b's1', b's2', b's3', b's4', b's5']), \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)), \
             patch.object(gen, '_choose_reel_template', return_value='panel-wipe') as mock_template, \
             patch('core.content_pipeline.generators.reel_generator.choose_font_preset',
                   return_value={'font_family': "'Poppins', sans-serif", 'font_import': 'Poppins'}) as mock_font, \
             patch.object(gen, '_generate_branded_segment', side_effect=[b'portada-raw', b'contra-raw']) as mock_branded, \
             patch.object(gen, '_normalize_branded_segment', side_effect=[b'portada-norm', b'contra-norm']) as mock_norm:
            clips, has_branding = gen._generate_clips_with_branding(
                ['scene 1', 'scene 2'], 'Hook', 'word', 'CTA', '#1a1a2e', 'job1-day1',
            )

        assert has_branding is True
        assert clips == [b'portada-norm', b'v', b's1', b's2', b's3', b's4', b's5', b'contra-norm']
        mock_font.assert_called_once_with('job1')
        mock_template.assert_called_once_with('Hook', 'CTA')
        assert mock_branded.call_args_list == [
            call('portada', 'Hook', 'word', 'CTA', '#1a1a2e', 'panel-wipe', "'Poppins', sans-serif"),
            call('contraportada', 'Hook', 'word', 'CTA', '#1a1a2e', 'panel-wipe', "'Poppins', sans-serif"),
        ]
        assert mock_norm.call_args_list == [
            call(b'portada-raw', 720, 1280, 24.0),
            call(b'contra-raw', 720, 1280, 24.0),
        ]

    def test_falls_back_when_portada_fails_completely(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'v', b's1', b's2', b's3', b's4', b's5']), \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)), \
             patch.object(gen, '_choose_reel_template', return_value='panel-wipe'), \
             patch('core.content_pipeline.generators.reel_generator.choose_font_preset',
                   return_value={'font_family': "'Poppins', sans-serif", 'font_import': 'Poppins'}), \
             patch.object(gen, '_generate_branded_segment', return_value=None) as mock_branded, \
             patch.object(gen, '_normalize_branded_segment') as mock_norm, \
             patch('core.content_pipeline.generators.reel_generator.record_hyperframes_fallback') as mock_fallback:
            clips, has_branding = gen._generate_clips_with_branding(
                ['scene 1', 'scene 2'], 'Hook', 'word', 'CTA', '#1a1a2e', 'job1-day1',
            )

        assert has_branding is False
        assert clips == [b'v', b's1', b's2', b's3', b's4', b's5']
        mock_norm.assert_not_called()
        mock_fallback.assert_called_once()
        # 2 intentos de portada (1 + reintento) antes de rendirse
        assert mock_branded.call_count == 2

    def test_skips_branding_attempt_when_body_has_fewer_than_3_clips(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'v']), \
             patch.object(gen, '_generate_branded_segment') as mock_branded:
            clips, has_branding = gen._generate_clips_with_branding(
                ['scene 1'], 'Hook', 'word', 'CTA', '#1a1a2e', 'job1-day1',
            )

        assert clips == [b'v']
        assert has_branding is False
        mock_branded.assert_not_called()


class TestNormalizeBrandedSegment:
    def test_builds_scale_command_with_exact_dimensions(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'normalized-mp4'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=fake_run) as mock_run:
            result = gen._normalize_branded_segment(b'raw-mp4', 720, 1280, 24.0)

        assert result == fake_output
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == 'ffmpeg'
        vf_idx = cmd.index('-vf')
        assert cmd[vf_idx + 1] == 'scale=720:1280'
        r_idx = cmd.index('-r')
        assert cmd[r_idx + 1] == '24.0'


class TestProbeClipDimensions:
    def test_writes_bytes_to_temp_file_and_probes(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        captured = {}

        def fake_probe(path):
            with open(path, 'rb') as f:
                captured['content'] = f.read()
            return (720, 1280, 24.0)

        with patch('core.content_pipeline.generators.reel_generator._probe_video_dimensions',
                    side_effect=fake_probe):
            result = gen._probe_clip_dimensions(b'fake-video-bytes')

        assert result == (720, 1280, 24.0)
        assert captured['content'] == b'fake-video-bytes'


class TestGenerateMusic:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_returns_audio_bytes_on_success(self):
        import base64
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_audio = MagicMock()
        mock_audio.data = base64.b64encode(b'fake-music-bytes').decode()
        mock_interaction = MagicMock()
        mock_interaction.output_audio = mock_audio
        with patch('core.content_pipeline.generators.reel_generator.genai.Client') as mock_client_cls:
            mock_client_cls.return_value.interactions.create.return_value = mock_interaction
            result = gen._generate_music('upbeat corporate, optimistic')
        assert result == b'fake-music-bytes'
        mock_client_cls.assert_called_once_with(
            vertexai=True, project='agente-cosmic', location='global',
        )
        call_kwargs = mock_client_cls.return_value.interactions.create.call_args.kwargs
        assert 'response_modalities' not in call_kwargs
        assert 'response_format' not in call_kwargs

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_retries_once_and_succeeds_on_second_attempt(self):
        # El filtro de contenido de Lyria 3 Clip (preview) es no-determinista —
        # confirmado en produccion reintentando el mismo prompt sin cambios.
        import base64
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_audio = MagicMock()
        mock_audio.data = base64.b64encode(b'fake-music-bytes').decode()
        mock_interaction = MagicMock()
        mock_interaction.output_audio = mock_audio
        with patch('core.content_pipeline.generators.reel_generator.genai.Client') as mock_client_cls:
            mock_client_cls.return_value.interactions.create.side_effect = [
                Exception('content_blocked'), mock_interaction,
            ]
            result = gen._generate_music('upbeat corporate, optimistic')
        assert result == b'fake-music-bytes'
        assert mock_client_cls.return_value.interactions.create.call_count == 2

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_returns_none_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator.genai.Client') as mock_client_cls:
            mock_client_cls.return_value.interactions.create.side_effect = Exception('error')
            result = gen._generate_music('upbeat')
        assert result is None
        assert mock_client_cls.return_value.interactions.create.call_count == 3

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_falls_back_to_generic_prompt_on_third_attempt(self):
        # Si el mood del guion falla 2 veces (posible bloqueo del filtro de
        # contenido), el 3er intento usa un prompt generico "corporate stock
        # music" que no depende del guion, para no perder la musica del todo.
        import base64
        from core.content_pipeline.generators.reel_generator import (
            ReelGenerator, _MUSIC_FALLBACK_PROMPT,
        )
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_audio = MagicMock()
        mock_audio.data = base64.b64encode(b'fallback-music-bytes').decode()
        mock_interaction = MagicMock()
        mock_interaction.output_audio = mock_audio
        with patch('core.content_pipeline.generators.reel_generator.genai.Client') as mock_client_cls:
            mock_client_cls.return_value.interactions.create.side_effect = [
                Exception('content_blocked'), Exception('content_blocked'), mock_interaction,
            ]
            result = gen._generate_music('upbeat corporate, optimistic')
        assert result == b'fallback-music-bytes'
        assert mock_client_cls.return_value.interactions.create.call_count == 3
        third_call_kwargs = mock_client_cls.return_value.interactions.create.call_args_list[2].kwargs
        assert third_call_kwargs['input'] == _MUSIC_FALLBACK_PROMPT


class TestGenerateNarration:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TTS_MODEL='publishers/google/models/gemini-2.5-flash-tts',
    )
    def test_returns_audio_bytes_on_success(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-narration-bytes'
        mock_resp = MagicMock()
        mock_resp.candidates = [MagicMock(content=MagicMock(parts=[mock_part]))]
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_narration('Bienvenido a nuestra tienda.')
        assert result == b'fake-narration-bytes'
        call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
        assert call_kwargs['config'].speech_config is not None

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TTS_MODEL='publishers/google/models/gemini-2.5-flash-tts',
    )
    def test_returns_none_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('error')
            result = gen._generate_narration('texto')
        assert result is None


_FAKE_SCRIPT_FOR_ASSEMBLE = {
    'hook_text': 'Descubre algo nuevo', 'highlight_word': 'nuevo', 'tag_cta': 'Compra ahora',
}


def _fake_ffmpeg_run(fake_output: bytes, width: str = '1080', duration: str = '24.0'):
    # _assemble_reel llama ffprobe 2 veces: una para la duracion real del video
    # concatenado (_probe_video_duration, formato=duration) y otra para el ancho
    # (_probe_video_width, stream=width, usado al posicionar el hook). Ninguna de
    # las 2 escribe a un archivo de salida como los demas comandos, leen de stdout.
    def run(cmd, *args, **kwargs):
        if cmd[0] == 'ffprobe':
            if 'format=duration' in cmd:
                return MagicMock(returncode=0, stdout=f'{duration}\n')
            return MagicMock(returncode=0, stdout=f'{width}\n')
        with open(cmd[-1], 'wb') as f:
            f.write(fake_output)
        return MagicMock(returncode=0)
    return run


class TestAssembleReel:
    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_calls_ffmpeg_and_returns_output_bytes(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run:
            result = gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=b'music-bytes',
                narration=b'narration-bytes',
                script=_FAKE_SCRIPT_FOR_ASSEMBLE,
                colors=['#1a1a2e'],
            )
        assert result == fake_output
        assert mock_run.call_count == 5  # concat, ffprobe-duration, ffprobe-width, overlay-drawtext, audio-mix
        mix_cmd = mock_run.call_args_list[-1].args[0]
        assert '-f s16le -ar 24000 -ac 1 -i' in ' '.join(mix_cmd)
        assert '-filter_complex' in mix_cmd
        filter_complex_idx = mix_cmd.index('-filter_complex')
        expected_filter = '[1:a]volume=0.3[music];[2:a][music]amix=inputs=2:duration=longest[a]'
        assert mix_cmd[filter_complex_idx + 1] == expected_filter

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_works_without_music_or_narration(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)):
            result = gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None,
                narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE,
                colors=['#1a1a2e'],
            )
        assert result == fake_output

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_hook_and_cta_drawtext_filters_are_always_present(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )
        overlay_cmd = mock_run.call_args_list[3].args[0]
        assert overlay_cmd.count('-i') == 1  # solo concat_path, sin PNGs de hook/cta como input
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        # el texto ahora viaja por textfile= (ver TestBuildHookFilterParts/
        # TestBuildCtaFilterParts para la verificacion del contenido real
        # escrito a disco) — aqui solo se confirma que ambos filtros existen.
        assert 'textfile=' in filter_complex and 'cta0.txt' in filter_complex
        assert 'textfile=' in filter_complex and 'hook0b.txt' in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[cta0]'

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_hook_centering_uses_real_probed_width(self, tmp_path):
        # Veo no garantiza 1080px (en produccion real devolvio 720x1280) — el
        # cursor del segmento resaltado del hook debe usar el ancho real
        # detectado via ffprobe, no un valor fijo.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output, width='720')) as mock_run:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )
        overlay_cmd = mock_run.call_args_list[3].args[0]
        filter_complex = overlay_cmd[overlay_cmd.index('-filter_complex') + 1]
        # con ancho real 720 el cursor de 'nuevo' (resaltado, al final de la
        # linea) debe quedar bien a la izquierda de 720, nunca cerca de 1080
        highlight_filter = [p for p in filter_complex.split(';') if 'hook0b.txt' in p][0]
        x_value = int(highlight_filter.split('x=')[1].split(':')[0])
        assert x_value < 720

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_adds_drawtext_filters_for_subtitles(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        subtitles = [
            {'text': 'Tu negocio en linea.', 'start': 0.0, 'end': 2.5},
            {'text': 'Contactanos hoy.', 'start': 2.5, 'end': 5.0},
        ]
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run:
            result = gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
                subtitles=subtitles,
            )
        assert result == fake_output
        overlay_cmd = mock_run.call_args_list[3].args[0]
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert 'textfile=' in filter_complex and 'sub0.txt' in filter_complex
        assert 'textfile=' in filter_complex and 'sub1.txt' in filter_complex
        assert "enable='between(t,0.0,2.5)'" in filter_complex
        assert "enable='between(t,2.5,5.0)'" in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[sub1]'

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_omits_subtitle_filters_when_no_subtitles(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )
        overlay_cmd = mock_run.call_args_list[3].args[0]
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert 'sub0' not in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[cta0]'

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_skip_hook_cta_overlay_without_subtitles_uses_plain_map(self, tmp_path):
        # skip_hook_cta_overlay=True + sin subtitulos: filter_parts queda
        # vacio, no debe armarse -filter_complex (romperia -map '[0:v]' sin
        # ningun filtro que defina esa etiqueta) — usa -map 0:v directo.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run, \
             patch('core.content_pipeline.generators.reel_generator._build_hook_filter_parts') as mock_hook, \
             patch('core.content_pipeline.generators.reel_generator._build_cta_filter_parts') as mock_cta:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
                skip_hook_cta_overlay=True,
            )

        mock_hook.assert_not_called()
        mock_cta.assert_not_called()
        overlay_cmd = mock_run.call_args_list[3].args[0]
        assert '-filter_complex' not in overlay_cmd
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '0:v'

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_skip_hook_cta_overlay_with_subtitles_keeps_filter_complex(self, tmp_path):
        # skip_hook_cta_overlay=True + CON subtitulos: filter_parts no queda
        # vacio (los subtitulos si aportan filtros) — sigue el camino normal
        # de -filter_complex/-map '[subN]'.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'
        subtitles = [{'text': 'Hola.', 'start': 0.0, 'end': 1.0}]

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
                subtitles=subtitles, skip_hook_cta_overlay=True,
            )

        overlay_cmd = mock_run.call_args_list[3].args[0]
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert 'textfile=' in filter_complex and 'sub0.txt' in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[sub0]'


class TestAssembleReelPlaywrightEngine:
    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_drawtext_engine_never_calls_playwright_render(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)), \
             patch.object(gen, '_render_text_overlay_playwright') as mock_render:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )
        mock_render.assert_not_called()

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='playwright')
    def test_playwright_engine_composes_both_pngs_via_overlay(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run, \
             patch.object(gen, '_render_text_overlay_playwright',
                           side_effect=[b'hook-png-bytes', b'cta-png-bytes']) as mock_render:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )

        assert mock_render.call_count == 2
        hook_call, cta_call = mock_render.call_args_list
        assert hook_call.args == ('Descubre algo nuevo', 'nuevo', 'hook', '#1a1a2e')
        assert cta_call.args == ('', '', 'cta', '#1a1a2e')
        assert cta_call.kwargs == {'cta_text': 'Compra ahora'}

        overlay_cmd = mock_run.call_args_list[3].args[0]
        assert overlay_cmd.count('-i') == 3  # concat + hook.png + cta.png
        filter_complex = overlay_cmd[overlay_cmd.index('-filter_complex') + 1]
        assert filter_complex.count('overlay=0:0') == 2
        assert "text='nuevo'" not in filter_complex  # hook via PNG, no drawtext
        assert "text='Compra ahora'" not in filter_complex  # cta via PNG, no drawtext
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[ctaout]'

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='playwright')
    def test_playwright_engine_falls_back_to_drawtext_per_element(self, tmp_path):
        # Hook se desbordo en Playwright (devuelve None) -> cae a drawtext.
        # CTA si funciono en Playwright -> se compone via overlay de PNG.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run, \
             patch.object(gen, '_render_text_overlay_playwright',
                           side_effect=[None, b'cta-png-bytes']):
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )

        overlay_cmd = mock_run.call_args_list[3].args[0]
        assert overlay_cmd.count('-i') == 2  # concat + cta.png (hook no genero PNG)
        filter_complex = overlay_cmd[overlay_cmd.index('-filter_complex') + 1]
        assert 'hook0b.txt' in filter_complex  # hook cayo a drawtext
        assert filter_complex.count('overlay=0:0') == 1  # solo cta via PNG


class TestExtractPosterFrame:
    def test_calls_ffmpeg_and_returns_frame_bytes(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_frame = b'fake-frame-png-bytes'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_frame)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=fake_run):
            result = gen._extract_poster_frame(b'fake-video-bytes')
        assert result == fake_frame

    def test_uses_custom_offset_seconds(self):
        # HALLAZGO (analisisPipeline.md, 2026-07-22): con portada HyperFrames el
        # default de 1s caia dentro de la animacion de fade-in del hook (0.5-2.0s
        # en la plantilla mas lenta) -> poster palido. generate() pasa un offset
        # mayor cuando hay portada (has_branding=True), ver test en TestGenerate.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(b'frame')
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=fake_run) as mock_run:
            gen._extract_poster_frame(b'fake-video-bytes', offset_seconds=2.5)
        assert mock_run.call_args.args[0][mock_run.call_args.args[0].index('-ss') + 1] == '2.5'


_FAKE_SCRIPT = {
    'hook_text': 'Descubre algo nuevo', 'highlight_word': 'nuevo',
    'tag_cta': 'Compra ahora', 'narration_script': 'Bienvenido a nuestra tienda.',
    'scene_prompts': ['scene 1', 'scene 2', 'scene 3'],
    'music_mood': 'upbeat, optimistic',
}


class TestGenerate:
    def test_returns_video_and_poster_urls_on_success(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'c1', b'c2', b'c3'], False)) as mock_clips, \
             patch.object(gen, '_generate_music', return_value=b'music'), \
             patch.object(gen, '_generate_narration', return_value=b'narration'), \
             patch('core.content_pipeline.generators.reel_generator.SubtitleGenerator') as mock_sub_gen, \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4') as mock_assemble, \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png'), \
             patch.object(gen, '_upload_video_to_storage', return_value='https://storage.test/reel.mp4') as mock_up_video, \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/poster.png') as mock_up_poster:
            mock_sub_gen.return_value.generate.return_value = [{'text': 'Hola.', 'start': 0.0, 'end': 1.0}]
            video_url, poster_url = gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')

        assert video_url == 'https://storage.test/reel.mp4'
        assert poster_url == 'https://storage.test/poster.png'
        mock_clips.assert_called_once_with(
            _FAKE_SCRIPT['scene_prompts'], _FAKE_SCRIPT['hook_text'], _FAKE_SCRIPT['highlight_word'],
            _FAKE_SCRIPT['tag_cta'], '#1a1a2e', 'job1-day1',
        )
        mock_up_video.assert_called_once_with(b'final-mp4', 'job1-day1')
        mock_up_poster.assert_called_once_with(b'poster-png', 'job1-day1-poster')
        mock_assemble.assert_called_once_with(
            [b'c1', b'c2', b'c3'], b'music', b'narration', _FAKE_SCRIPT, ['#1a1a2e'],
            [{'text': 'Hola.', 'start': 0.0, 'end': 1.0}],
            skip_hook_cta_overlay=False,
        )

    def test_extracts_poster_later_when_branding_present(self):
        # HALLAZGO (analisisPipeline.md, 2026-07-22): con portada HyperFrames
        # (has_branding=True), 1s cae dentro de la animacion de fade-in del
        # hook -> poster palido. offset debe ser 2.5s (despues del fade-in mas
        # lento de las 3 plantillas, dentro de los 3s de la portada).
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'p', b'c1', b'c2', b'c3', b'c'], True)), \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4'), \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png') as mock_poster, \
             patch.object(gen, '_upload_video_to_storage', return_value='url1'), \
             patch.object(gen, '_upload_to_storage', return_value='url2'):
            gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')

        assert mock_poster.call_args.kwargs['offset_seconds'] == 2.5

    def test_extracts_poster_at_default_offset_without_branding(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'c1', b'c2', b'c3'], False)), \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4'), \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png') as mock_poster, \
             patch.object(gen, '_upload_video_to_storage', return_value='url1'), \
             patch.object(gen, '_upload_to_storage', return_value='url2'):
            gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')

        assert mock_poster.call_args.kwargs['offset_seconds'] == 1.0

    def test_passes_skip_flag_when_branding_succeeds(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'p', b'c1', b'c2', b'c3', b'c'], True)), \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4') as mock_assemble, \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png'), \
             patch.object(gen, '_upload_video_to_storage', return_value='url1'), \
             patch.object(gen, '_upload_to_storage', return_value='url2'):
            gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')

        assert mock_assemble.call_args.kwargs['skip_hook_cta_overlay'] is True

    def test_skips_subtitle_generation_when_narration_fails(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'c1', b'c2', b'c3'], False)), \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch('core.content_pipeline.generators.reel_generator.SubtitleGenerator') as mock_sub_gen, \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4') as mock_assemble, \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png'), \
             patch.object(gen, '_upload_video_to_storage', return_value='url1'), \
             patch.object(gen, '_upload_to_storage', return_value='url2'):
            gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')

        mock_sub_gen.return_value.generate.assert_not_called()
        assembled_args = mock_assemble.call_args.args
        assert assembled_args[-1] == []

    def test_returns_empty_strings_when_fewer_than_3_clips_generated(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'c1', b'c2'], False)):
            video_url, poster_url = gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')
        assert (video_url, poster_url) == ('', '')

    def test_returns_empty_strings_when_assembly_raises(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'c1', b'c2', b'c3'], False)), \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch.object(gen, '_assemble_reel', side_effect=Exception('ffmpeg error')):
            video_url, poster_url = gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')
        assert (video_url, poster_url) == ('', '')


class TestUploadVideoToStorage:
    def test_uploads_with_video_mimetype_and_cache_busting(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_blob = MagicMock()
        mock_blob.public_url = 'https://storage.googleapis.com/test-bucket/reels/job1-day1.mp4'
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        with patch('core.content_pipeline.generators.reel_generator.storage.Client', return_value=mock_client):
            url = gen._upload_video_to_storage(b'fake-mp4-bytes', 'job1-day1')
        mock_blob.upload_from_string.assert_called_once_with(b'fake-mp4-bytes', content_type='video/mp4')
        assert url.startswith('https://storage.googleapis.com/test-bucket/reels/job1-day1.mp4?v=')


class TestRenderTextOverlayPlaywright:
    def _make_mock_playwright(self, screenshot_bytes: bytes, evaluate_side_effect: list):
        mock_page = MagicMock()
        mock_page.screenshot.return_value = screenshot_bytes
        mock_page.evaluate.side_effect = evaluate_side_effect
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw_ctx = MagicMock()
        mock_pw_ctx.chromium.launch.return_value = mock_browser
        mock_pw = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw_ctx)
        mock_pw.__exit__ = MagicMock(return_value=False)
        return mock_pw, mock_page

    def test_returns_screenshot_when_no_overflow(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = b'fake-hook-png'
        # 3 evaluate() por intento: fonts.ready, offsetHeight, scrollWidth-clientWidth.
        # Solo el 3er valor importa (overflow_px); 0 = sin desborde.
        mock_pw, mock_page = self._make_mock_playwright(fake_png, [None, None, 0])

        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw), \
             patch('core.content_pipeline.generators.reel_generator.record_playwright_overlay_fallback') as mock_metric:
            result = gen._render_text_overlay_playwright(
                'Descubre algo nuevo', 'nuevo', 'hook', '#002951',
            )

        assert result == fake_png
        mock_metric.assert_not_called()
        html_arg = mock_page.set_content.call_args[0][0]
        assert '<span class="highlight">nuevo</span>' in html_arg
        assert '#002951' in html_arg
        assert '{{font_path}}' not in html_arg
        assert '{{text_color}}' not in html_arg
        # #002951 es oscuro (brillo ~33) -> texto de la palabra resaltada debe ser
        # blanco, no el color oscuro hardcodeado que antes hacia el texto invisible
        # cuando coincidia con el color primario (ver HALLAZGO 69).
        assert 'color: white' in html_arg

    def test_retries_once_and_succeeds_on_second_attempt(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = b'fake-hook-png'
        # 1er intento: overflow_px=10 (desborda). 2do intento: overflow_px=0 (ok).
        mock_pw, mock_page = self._make_mock_playwright(fake_png, [None, None, 10, None, None, 0])

        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw), \
             patch('core.content_pipeline.generators.reel_generator.record_playwright_overlay_fallback') as mock_metric:
            result = gen._render_text_overlay_playwright(
                'Descubre algo nuevo', 'nuevo', 'hook', '#002951',
            )

        assert result == fake_png
        mock_metric.assert_not_called()
        assert mock_pw.__enter__.call_count == 2

    def test_returns_none_and_records_fallback_after_second_overflow(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = b'fake-hook-png'
        # Ambos intentos desbordan (overflow_px=10 las 2 veces).
        mock_pw, mock_page = self._make_mock_playwright(fake_png, [None, None, 10, None, None, 10])

        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw), \
             patch('core.content_pipeline.generators.reel_generator.record_playwright_overlay_fallback') as mock_metric:
            result = gen._render_text_overlay_playwright(
                'Descubre algo nuevo', 'nuevo', 'hook', '#002951',
            )

        assert result is None
        mock_metric.assert_called_once_with('hook')

    def test_returns_none_and_records_fallback_on_exception(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')

        with patch('core.content_pipeline.generators.reel_generator.sync_playwright',
                    side_effect=Exception('chromium crashed')), \
             patch('core.content_pipeline.generators.reel_generator.record_playwright_overlay_fallback') as mock_metric:
            result = gen._render_text_overlay_playwright(
                '', '', 'cta', '#002951', cta_text='Compra ahora',
            )

        assert result is None
        mock_metric.assert_called_once_with('cta')

    def test_cta_style_injects_cta_text_not_hook(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = b'fake-cta-png'
        mock_pw, mock_page = self._make_mock_playwright(fake_png, [None, None, 0])

        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw), \
             patch('core.content_pipeline.generators.reel_generator.record_playwright_overlay_fallback'):
            result = gen._render_text_overlay_playwright(
                '', '', 'cta', '#002951', cta_text='Compra ahora',
            )

        assert result == fake_png
        html_arg = mock_page.set_content.call_args[0][0]
        assert 'Compra ahora' in html_arg
        assert 'class="hook"' not in html_arg

