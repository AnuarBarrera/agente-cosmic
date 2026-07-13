import html as _html
import logging
import os
import re
import subprocess
import tempfile
import time
import google.genai as genai
from google.genai import types
from google.cloud import storage
from django.conf import settings
from playwright.sync_api import sync_playwright
from core.shared.metrics import GCS_OPERATIONS
from core.shared.metrics_utils import track_external_api, record_tokens
from core.shared.rate_limiter import call_with_429_retry


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

    def _generate_video_clips(self, scene_prompts: list[str]) -> list[bytes]:
        clips = []
        for prompt in scene_prompts:
            clip = self._generate_single_clip(prompt)
            if clip is None:
                clip = self._generate_single_clip(prompt)  # 1 reintento
            if clip is not None:
                clips.append(clip)
            else:
                logger.warning(f"Clip de Veo fallido tras reintento, se omite: {prompt[:80]}")
        return clips

    def _generate_single_clip(self, prompt: str) -> bytes | None:
        try:
            client = _vertex_client()

            def _call():
                with track_external_api('veo', operation='video_generate'):
                    return client.models.generate_videos(
                        model=settings.VERTEX_VIDEO_MODEL,
                        prompt=prompt,
                        config=types.GenerateVideosConfig(
                            aspect_ratio='9:16',
                            duration_seconds=8,
                            number_of_videos=1,
                            generate_audio=False,
                        ),
                    )
            operation = call_with_429_retry(_call, settings.VERTEX_VIDEO_MODEL)
            client = _vertex_client()
            while not operation.done:
                time.sleep(10)
                operation = client.operations.get(operation)
            if operation.error:
                logger.warning(f"Veo devolvió error: {operation.error}")
                return None
            generated = operation.result.generated_videos
            if not generated:
                return None
            return generated[0].video.video_bytes
        except Exception as e:
            logger.warning(f"Veo clip generation failed: {e}")
            return None

    def _generate_music(self, music_mood: str) -> bytes | None:
        try:
            client = _vertex_client()
            with track_external_api('lyria', operation='music_generate'):
                interaction = client.interactions.create(
                    model=settings.VERTEX_MUSIC_MODEL,
                    input=f"Instrumental only, no vocals. {music_mood}",
                    response_modalities=['audio'],
                )
            audio = getattr(interaction, 'output_audio', None)
            if audio is not None and getattr(audio, 'data', None):
                return audio.data
            return None
        except Exception as e:
            logger.warning(f"Lyria music generation failed (reel sin musica): {e}")
            return None

    def _generate_narration(self, narration_script: str) -> bytes | None:
        try:
            client = _vertex_client()
            with track_external_api('gemini', operation='tts_generate'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TTS_MODEL,
                    contents=narration_script,
                    config=types.GenerateContentConfig(response_modalities=['AUDIO']),
                )
            for part in resp.candidates[0].content.parts:
                if part.inline_data:
                    return part.inline_data.data
            return None
        except Exception as e:
            logger.warning(f"TTS narration generation failed (reel sin narracion): {e}")
            return None

    def _assemble_reel(self, clips: list[bytes], music: bytes | None, narration: bytes | None,
                        hook_png: bytes, cta_png: bytes) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            clip_paths = []
            for i, clip_bytes in enumerate(clips):
                path = os.path.join(tmp, f'clip{i}.mp4')
                with open(path, 'wb') as f:
                    f.write(clip_bytes)
                clip_paths.append(path)

            concat_list_path = os.path.join(tmp, 'concat.txt')
            with open(concat_list_path, 'w') as f:
                for p in clip_paths:
                    f.write(f"file '{p}'\n")

            concat_path = os.path.join(tmp, 'concat.mp4')
            subprocess.run(
                ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_list_path,
                 '-c', 'copy', concat_path],
                check=True, capture_output=True,
            )

            hook_path = os.path.join(tmp, 'hook.png')
            with open(hook_path, 'wb') as f:
                f.write(hook_png)
            cta_path = os.path.join(tmp, 'cta.png')
            with open(cta_path, 'wb') as f:
                f.write(cta_png)

            duration = len(clips) * 8
            cta_start = max(0, duration - 3)
            overlay_path = os.path.join(tmp, 'overlay.mp4')
            subprocess.run(
                ['ffmpeg', '-y', '-i', concat_path, '-i', hook_path, '-i', cta_path,
                 '-filter_complex',
                 f"[0:v][1:v]overlay=0:0:enable='between(t,0,3)'[v1];"
                 f"[v1][2:v]overlay=0:0:enable='between(t,{cta_start},{duration})'[v2]",
                 '-map', '[v2]', '-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                 overlay_path],
                check=True, capture_output=True,
            )

            audio_inputs = []
            audio_paths = []
            if music is not None:
                music_path = os.path.join(tmp, 'music.mp3')
                with open(music_path, 'wb') as f:
                    f.write(music)
                audio_paths.append(music_path)
            if narration is not None:
                narration_path = os.path.join(tmp, 'narration.mp3')
                with open(narration_path, 'wb') as f:
                    f.write(narration)
                audio_paths.append(narration_path)

            output_path = os.path.join(tmp, 'output.mp4')
            if not audio_paths:
                subprocess.run(['ffmpeg', '-y', '-i', overlay_path, '-c', 'copy', output_path],
                                check=True, capture_output=True)
            else:
                cmd = ['ffmpeg', '-y', '-i', overlay_path]
                for p in audio_paths:
                    cmd += ['-i', p]
                if len(audio_paths) == 2:
                    filter_complex = '[1:a][2:a]amix=inputs=2:duration=shortest[a]'
                    cmd += ['-filter_complex', filter_complex, '-map', '0:v', '-map', '[a]']
                else:
                    cmd += ['-map', '0:v', '-map', '1:a']
                cmd += ['-t', str(duration), '-c:v', 'copy', '-c:a', 'aac', '-shortest', output_path]
                subprocess.run(cmd, check=True, capture_output=True)

            with open(output_path, 'rb') as f:
                return f.read()

    def _extract_poster_frame(self, video_bytes: bytes) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            video_path = os.path.join(tmp, 'video.mp4')
            with open(video_path, 'wb') as f:
                f.write(video_bytes)
            frame_path = os.path.join(tmp, 'frame.png')
            subprocess.run(
                ['ffmpeg', '-y', '-ss', '1', '-i', video_path, '-vframes', '1', frame_path],
                check=True, capture_output=True,
            )
            with open(frame_path, 'rb') as f:
                return f.read()


