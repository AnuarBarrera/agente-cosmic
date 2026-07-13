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
