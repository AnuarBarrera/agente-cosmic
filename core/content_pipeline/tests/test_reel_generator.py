from unittest.mock import patch, MagicMock
from django.test import override_settings

from core.content_pipeline.generators.reel_generator import (
    _escape_drawtext, _wrap_text, _hex_to_ffmpeg_color, _measure_text_width,
    _build_hook_filter_parts, _build_cta_filter_parts, _CTA_FONTSIZE,
)


class TestEscapeDrawtext:
    def test_escapes_colon(self):
        assert _escape_drawtext('Hola: bienvenido') == 'Hola\\: bienvenido'

    def test_escapes_single_quote(self):
        assert _escape_drawtext("Tu 'mejor' opcion") == "Tu \\'mejor\\' opcion"

    def test_escapes_percent(self):
        assert _escape_drawtext('50% de descuento') == '50\\% de descuento'

    def test_escapes_backslash_first(self):
        assert _escape_drawtext('a\\b') == 'a\\\\b'


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


class TestMeasureTextWidth:
    def test_empty_string_is_zero(self):
        assert _measure_text_width('', 64) == 0

    def test_longer_text_is_wider(self):
        short = _measure_text_width('Hola', 64)
        long_ = _measure_text_width('Hola mundo entero', 64)
        assert short > 0
        assert long_ > short


class TestBuildHookFilterParts:
    def test_plain_line_when_highlight_not_found(self):
        parts, last_label = _build_hook_filter_parts(
            'Texto sin resaltado', 'inexistente', '#002951', '0:v',
        )
        assert len(parts) == 1
        assert parts[0].startswith('[0:v]drawtext=')
        assert "text='Texto sin resaltado'" in parts[0]
        assert 'box=1' not in parts[0]
        assert "enable='between(t,0,3)'" in parts[0]
        assert last_label == 'hook0'

    def test_splits_line_around_highlight_word(self):
        # 'nuevo' esta al final de la frase (20 caracteres, no se envuelve) ->
        # queda un segmento 'before' + el resaltado, sin segmento 'after'.
        parts, last_label = _build_hook_filter_parts(
            'Descubre algo nuevo', 'nuevo', '#002951', '0:v',
        )
        assert any("text='Descubre algo '" in p for p in parts)
        highlight_parts = [p for p in parts if "text='nuevo'" in p]
        assert len(highlight_parts) == 1
        assert 'box=1' in highlight_parts[0]
        assert 'boxcolor=0x002951@1.0' in highlight_parts[0]
        assert last_label == 'hook0b'

    def test_wraps_long_hook_into_multiple_lines(self):
        long_hook = 'Una frase mucho mas larga que el limite de caracteres permitido'
        parts, last_label = _build_hook_filter_parts(long_hook, 'inexistente', '#002951', '0:v')
        assert len(parts) >= 2

    def test_all_filters_enabled_only_during_first_three_seconds(self):
        parts, _ = _build_hook_filter_parts('Descubre algo nuevo', 'nuevo', '#002951', '0:v')
        assert all("enable='between(t,0,3)'" in p for p in parts)


class TestBuildCtaFilterParts:
    def test_builds_single_filter_with_box_and_enable_window(self):
        parts, last_label = _build_cta_filter_parts(
            'Compra ahora', '#002951', 'hook0b', 21.0, 24.0,
        )
        assert len(parts) == 1
        assert parts[0].startswith('[hook0b]drawtext=')
        assert "text='Compra ahora'" in parts[0]
        assert 'box=1' in parts[0]
        assert 'boxcolor=0x002951@1.0' in parts[0]
        assert "enable='between(t,21.0,24.0)'" in parts[0]
        assert last_label == 'cta0'

    def test_scale_shrinks_fontsize_and_box_border(self):
        parts_full, _ = _build_cta_filter_parts('Compra ahora', '#002951', '0:v', 21.0, 24.0, scale=1.0)
        parts_scaled, _ = _build_cta_filter_parts('Compra ahora', '#002951', '0:v', 21.0, 24.0, scale=0.5)
        assert f'fontsize={_CTA_FONTSIZE}' in parts_full[0]
        assert f'fontsize={_CTA_FONTSIZE // 2}' in parts_scaled[0]


class TestGenerateVideoClips:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
    def test_returns_one_clip_per_scene_prompt(self):
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
            clips = gen._generate_video_clips(['scene 1', 'scene 2', 'scene 3'])
        assert clips == [fake_video, fake_video, fake_video]
        sent_prompt = mock_vc.return_value.models.generate_videos.call_args_list[0].kwargs['prompt']
        assert sent_prompt.startswith('scene 1')
        assert 'Absolutely NO text' in sent_prompt

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
    def test_skips_clip_that_fails_after_retry(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_videos.side_effect = Exception('rejected')
            clips = gen._generate_video_clips(['scene 1'])
        assert clips == []


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


def _fake_ffmpeg_run(fake_output: bytes):
    # _assemble_reel ahora llama ffprobe (via _probe_video_width) para saber el
    # ancho real del video de Veo antes de posicionar el hook — ese subprocess
    # no escribe a un archivo de salida como los demas, lee de stdout.
    def run(cmd, *args, **kwargs):
        if cmd[0] == 'ffprobe':
            return MagicMock(returncode=0, stdout='1080\n')
        with open(cmd[-1], 'wb') as f:
            f.write(fake_output)
        return MagicMock(returncode=0)
    return run


class TestAssembleReel:
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
        assert mock_run.call_count == 4  # concat, ffprobe, overlay-drawtext, audio-mix
        mix_cmd = mock_run.call_args_list[-1].args[0]
        assert '-f s16le -ar 24000 -ac 1 -i' in ' '.join(mix_cmd)
        assert '-filter_complex' in mix_cmd
        filter_complex_idx = mix_cmd.index('-filter_complex')
        expected_filter = '[1:a]volume=0.3[music];[2:a][music]amix=inputs=2:duration=longest[a]'
        assert mix_cmd[filter_complex_idx + 1] == expected_filter

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
        overlay_cmd = mock_run.call_args_list[2].args[0]
        assert overlay_cmd.count('-i') == 1  # solo concat_path, sin PNGs de hook/cta como input
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert "text='Compra ahora'" in filter_complex
        assert "text='nuevo'" in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[cta0]'

    def test_hook_centering_uses_real_probed_width(self, tmp_path):
        # Veo no garantiza 1080px (en produccion real devolvio 720x1280) — el
        # cursor del segmento resaltado del hook debe usar el ancho real
        # detectado via ffprobe, no un valor fijo.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        def run(cmd, *args, **kwargs):
            if cmd[0] == 'ffprobe':
                return MagicMock(returncode=0, stdout='720\n')
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=run) as mock_run:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )
        overlay_cmd = mock_run.call_args_list[2].args[0]
        filter_complex = overlay_cmd[overlay_cmd.index('-filter_complex') + 1]
        # con ancho real 720 el cursor de 'nuevo' (resaltado, al final de la
        # linea) debe quedar bien a la izquierda de 720, nunca cerca de 1080
        highlight_filter = [p for p in filter_complex.split(';') if "text='nuevo'" in p][0]
        x_value = int(highlight_filter.split('x=')[1].split(':')[0])
        assert x_value < 720

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
        overlay_cmd = mock_run.call_args_list[2].args[0]
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert "text='Tu negocio en linea.'" in filter_complex
        assert "text='Contactanos hoy.'" in filter_complex
        assert "enable='between(t,0.0,2.5)'" in filter_complex
        assert "enable='between(t,2.5,5.0)'" in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[sub1]'

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
        overlay_cmd = mock_run.call_args_list[2].args[0]
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert 'sub0' not in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[cta0]'


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
        with patch.object(gen, '_generate_video_clips', return_value=[b'c1', b'c2', b'c3']), \
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
        mock_up_video.assert_called_once_with(b'final-mp4', 'job1-day1')
        mock_up_poster.assert_called_once_with(b'poster-png', 'job1-day1-poster')
        mock_assemble.assert_called_once_with(
            [b'c1', b'c2', b'c3'], b'music', b'narration', _FAKE_SCRIPT, ['#1a1a2e'],
            [{'text': 'Hola.', 'start': 0.0, 'end': 1.0}],
        )

    def test_skips_subtitle_generation_when_narration_fails(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'c1', b'c2', b'c3']), \
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
        with patch.object(gen, '_generate_video_clips', return_value=[b'c1', b'c2']):
            video_url, poster_url = gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')
        assert (video_url, poster_url) == ('', '')

    def test_returns_empty_strings_when_assembly_raises(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'c1', b'c2', b'c3']), \
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

