import io
from unittest.mock import patch, MagicMock
from django.test import override_settings
from PIL import Image


def _png_bytes(color=(30, 30, 60), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='PNG')
    return buf.getvalue()


from core.content_pipeline.generators.reel_generator import (
    _escape_drawtext, _wrap_subtitle_text,
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


class TestWrapSubtitleText:
    def test_returns_unchanged_when_short(self):
        assert _wrap_subtitle_text('Hola mundo') == 'Hola mundo'

    def test_wraps_long_text_into_two_lines(self):
        text = 'Tu negocio en linea en menos de 48 horas'
        result = _wrap_subtitle_text(text, max_chars=20)
        assert result == 'Tu negocio en linea\nen menos de 48 horas'


class TestRenderTextOverlay:
    def _make_mock_playwright(self, screenshot_bytes: bytes, overflow_values=None):
        # page.evaluate() se llama 3 veces por intento: 'document.fonts.ready'
        # y el reflow forzado ('document.body.offsetHeight') — ambos con valor
        # ignorado — y la medicion scrollWidth-clientWidth (el que importa).
        overflow_values = overflow_values or [0, 0]
        evaluate_side_effect = []
        for val in overflow_values:
            evaluate_side_effect.append(None)  # document.fonts.ready
            evaluate_side_effect.append(None)  # document.body.offsetHeight (reflow forzado)
            evaluate_side_effect.append(val)   # scrollWidth - clientWidth
        mock_page = MagicMock()
        mock_page.screenshot.return_value = screenshot_bytes
        mock_page.evaluate.side_effect = evaluate_side_effect
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium.launch.return_value = mock_browser
        mock_pw_context = MagicMock()
        mock_pw_context.__enter__.return_value = mock_pw_instance
        mock_pw_context.__exit__.return_value = False
        return mock_pw_context, mock_page

    def test_returns_screenshot_bytes_for_hook_style(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = _png_bytes()
        mock_pw_context, mock_page = self._make_mock_playwright(fake_png)
        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw_context):
            result = gen._render_text_overlay('Descubre algo nuevo', 'nuevo', 'hook', ['#e94560'])
        assert result == fake_png
        mock_page.screenshot.assert_called_once_with(omit_background=True)

    def test_returns_screenshot_bytes_for_cta_style(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = _png_bytes()
        mock_pw_context, mock_page = self._make_mock_playwright(fake_png)
        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw_context):
            result = gen._render_text_overlay('', '', 'cta', ['#e94560'], cta_text='Compra ahora')
        assert result == fake_png

    def test_highlight_word_is_wrapped_in_span(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = _png_bytes()
        mock_pw_context, mock_page = self._make_mock_playwright(fake_png)
        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw_context):
            gen._render_text_overlay('Descubre algo nuevo', 'nuevo', 'hook', ['#e94560'])
        html_sent = mock_page.set_content.call_args.args[0]
        assert '<span class="highlight">nuevo</span>' in html_sent

    def test_retries_once_when_overlay_overflows_the_frame(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = _png_bytes()
        mock_pw_context, mock_page = self._make_mock_playwright(
            fake_png, overflow_values=[40, 0],
        )
        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw_context):
            result = gen._render_text_overlay('Descubre algo nuevo', 'nuevo', 'hook', ['#e94560'])
        assert result == fake_png
        assert mock_page.screenshot.call_count == 2

    def test_returns_last_screenshot_when_both_attempts_overflow(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = _png_bytes()
        mock_pw_context, mock_page = self._make_mock_playwright(
            fake_png, overflow_values=[40, 40],
        )
        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw_context):
            result = gen._render_text_overlay('Descubre algo nuevo', 'nuevo', 'hook', ['#e94560'])
        assert result == fake_png
        assert mock_page.screenshot.call_count == 2


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


class TestAssembleReel:
    def test_calls_ffmpeg_and_returns_output_bytes(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        def fake_run(cmd, *args, **kwargs):
            output_path = cmd[-1]
            with open(output_path, 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=fake_run) as mock_run:
            result = gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=b'music-bytes',
                narration=b'narration-bytes',
                hook_png=b'hook-png-bytes',
                cta_png=b'cta-png-bytes',
            )
        assert result == fake_output
        assert mock_run.call_count == 3
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

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=fake_run):
            result = gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None,
                narration=None,
                hook_png=b'hook-png-bytes',
                cta_png=b'cta-png-bytes',
            )
        assert result == fake_output

    def test_adds_drawtext_filters_for_subtitles(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        subtitles = [
            {'text': 'Tu negocio en linea.', 'start': 0.0, 'end': 2.5},
            {'text': 'Contactanos hoy.', 'start': 2.5, 'end': 5.0},
        ]
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=fake_run) as mock_run:
            result = gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                hook_png=b'hook-png-bytes', cta_png=b'cta-png-bytes',
                subtitles=subtitles,
            )
        assert result == fake_output
        overlay_cmd = mock_run.call_args_list[1].args[0]
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert 'drawtext=fontfile=' in filter_complex
        assert "text='Tu negocio en linea.'" in filter_complex
        assert "text='Contactanos hoy.'" in filter_complex
        assert "enable='between(t,0.0,2.5)'" in filter_complex
        assert "enable='between(t,2.5,5.0)'" in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[sub1]'

    def test_omits_drawtext_filters_when_no_subtitles(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=fake_run) as mock_run:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                hook_png=b'hook-png-bytes', cta_png=b'cta-png-bytes',
            )
        overlay_cmd = mock_run.call_args_list[1].args[0]
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert 'drawtext' not in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[v2]'


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
             patch.object(gen, '_render_text_overlay', return_value=b'overlay-png'), \
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
            [b'c1', b'c2', b'c3'], b'music', b'narration', b'overlay-png', b'overlay-png',
            [{'text': 'Hola.', 'start': 0.0, 'end': 1.0}],
        )

    def test_skips_subtitle_generation_when_narration_fails(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'c1', b'c2', b'c3']), \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch('core.content_pipeline.generators.reel_generator.SubtitleGenerator') as mock_sub_gen, \
             patch.object(gen, '_render_text_overlay', return_value=b'overlay-png'), \
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
             patch.object(gen, '_render_text_overlay', return_value=b'overlay-png'), \
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
