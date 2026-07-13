import html as _html
import logging
import os
import re
import google.genai as genai
from google.cloud import storage
from django.conf import settings
from playwright.sync_api import sync_playwright
from core.shared.metrics import GCS_OPERATIONS
from core.shared.metrics_utils import track_external_api, record_tokens

logger = logging.getLogger(__name__)

_TEMPLATE_MAP = {
    'hook': 'reel_hook.html',
    'cta': 'reel_cta.html',
}


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


class ReelGenerator:
    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def _render_text_overlay(self, text: str, highlight_word: str, style: str, colors: list[str], cta_text: str = '') -> bytes:
        template_name = _TEMPLATE_MAP[style]
        template_path = os.path.normpath(os.path.join(
            os.path.dirname(__file__), '..', 'templates', 'content_pipeline', template_name,
        ))
        with open(template_path) as f:
            html = f.read()
        primary = colors[0] if colors else '#e94560'
        html = html.replace('{{primary_color}}', primary)

        if style == 'hook':
            escaped = _html.escape(text)
            if highlight_word:
                escaped_word = _html.escape(highlight_word)
                pattern = re.compile(re.escape(escaped_word), re.IGNORECASE)
                escaped = pattern.sub(f'<span class="highlight">{escaped_word}</span>', escaped, count=1)
            html = html.replace('{{hook_html}}', escaped)
        else:
            html = html.replace('{{cta_text}}', _html.escape(cta_text))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
            )
            page = browser.new_page(viewport={'width': 1080, 'height': 1920})
            page.set_content(html, wait_until='load')
            page.evaluate('document.fonts.ready')
            png_bytes = page.screenshot(omit_background=True)
            browser.close()

        return png_bytes
