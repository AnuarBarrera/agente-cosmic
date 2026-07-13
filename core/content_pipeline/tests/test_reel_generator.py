import io
from unittest.mock import patch, MagicMock
from django.test import override_settings
from PIL import Image


def _png_bytes(color=(30, 30, 60), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='PNG')
    return buf.getvalue()


class TestRenderTextOverlay:
    def _make_mock_playwright(self, screenshot_bytes: bytes):
        mock_page = MagicMock()
        mock_page.screenshot.return_value = screenshot_bytes
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
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_audio = MagicMock()
        mock_audio.data = b'fake-music-bytes'
        mock_interaction = MagicMock()
        mock_interaction.output_audio = mock_audio
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.interactions.create.return_value = mock_interaction
            result = gen._generate_music('upbeat corporate, optimistic')
        assert result == b'fake-music-bytes'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_returns_none_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.interactions.create.side_effect = Exception('error')
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

