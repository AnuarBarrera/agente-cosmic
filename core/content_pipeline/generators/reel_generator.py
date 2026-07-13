import base64
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

    # Mismo patron que _SAFE_CONSTRAINTS en image_generator.py: se aplica siempre,
    # sin depender de que el guion (Gemini) lo incluya por su cuenta — Veo alucina
    # texto/UI legible especialmente cuando la escena describe pantallas, laptops,
    # monitores o interfaces (comun en negocios de tecnologia/web), por eso el
    # bloqueo va reforzado y al final del prompt, no solo como sugerencia inicial.
    _VEO_SAFE_CONSTRAINTS = (
        " Absolutely NO text, NO letters, NO words, NO numbers, NO captions, NO subtitles, "
        "NO UI elements, NO icons, NO logos, NO readable screen content anywhere in the video. "
        "If a screen or monitor appears, it must be blank, off, or showing only abstract "
        "blurred light — never legible text or interface elements."
    )

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
            prompt = prompt + self._VEO_SAFE_CONSTRAINTS

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
        # El filtro de contenido de Lyria 3 Clip (preview) es no-determinista —
        # confirmado reintentando el MISMO prompt: falla y luego funciona sin
        # cambiar nada. 1 reintento antes de degradar a "reel sin musica".
        result = self._generate_music_attempt(music_mood)
        if result is None:
            result = self._generate_music_attempt(music_mood)
        return result

    def _generate_music_attempt(self, music_mood: str) -> bytes | None:
        try:
            # Lyria 3 solo esta disponible en la ubicacion 'global' de Vertex AI (no
            # en una region como us-central1) y rechaza la peticion si se especifica
            # response_modalities/response_format explicito — el modelo devuelve
            # audio implicitamente, sin necesidad de pedirlo.
            client = genai.Client(
                vertexai=True,
                project=settings.GOOGLE_CLOUD_PROJECT,
                location='global',
            )
            with track_external_api('lyria', operation='music_generate'):
                interaction = client.interactions.create(
                    model=settings.VERTEX_MUSIC_MODEL,
                    input=f"Instrumental only, no vocals. {music_mood}",
                )
            audio = getattr(interaction, 'output_audio', None)
            if audio is not None and getattr(audio, 'data', None):
                # La API de Interactions (a diferencia de generate_content) devuelve
                # AudioContent.data como string base64, no bytes crudos.
                return base64.b64decode(audio.data)
            return None
        except Exception as e:
            logger.warning(f"Lyria music generation failed (reintentando o degradando): {e}")
            return None

    def _generate_narration(self, narration_script: str) -> bytes | None:
        try:
            client = _vertex_client()
            with track_external_api('gemini', operation='tts_generate'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TTS_MODEL,
                    contents=narration_script,
                    config=types.GenerateContentConfig(
                        response_modalities=['AUDIO'],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Kore')
                            )
                        ),
                    ),
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

            audio_input_flags = []
            audio_stream_count = 0
            if music is not None:
                music_path = os.path.join(tmp, 'music.mp3')
                with open(music_path, 'wb') as f:
                    f.write(music)
                audio_input_flags += ['-i', music_path]
                audio_stream_count += 1
            if narration is not None:
                # TTS (gemini-2.5-flash-tts) devuelve PCM crudo (audio/L16, 24kHz,
                # mono, sin contenedor/cabecera) — sin estas flags de INPUT (deben ir
                # antes del -i de este archivo) ffmpeg no puede adivinar el formato.
                narration_path = os.path.join(tmp, 'narration.pcm')
                with open(narration_path, 'wb') as f:
                    f.write(narration)
                audio_input_flags += ['-f', 's16le', '-ar', '24000', '-ac', '1', '-i', narration_path]
                audio_stream_count += 1

            output_path = os.path.join(tmp, 'output.mp4')
            if audio_stream_count == 0:
                subprocess.run(['ffmpeg', '-y', '-i', overlay_path, '-c', 'copy', output_path],
                                check=True, capture_output=True)
            else:
                cmd = ['ffmpeg', '-y', '-i', overlay_path] + audio_input_flags
                if audio_stream_count == 2:
                    filter_complex = '[1:a][2:a]amix=inputs=2:duration=shortest[a]'
                    cmd += ['-filter_complex', filter_complex, '-map', '0:v', '-map', '[a]']
                else:
                    cmd += ['-map', '0:v', '-map', '1:a']
                # SIN -shortest: la duracion del video (fijada por -t) manda. La
                # narracion (TTS, ~15-20s hablados) suele ser mas corta que el video
                # (24s) — con -shortest el output entero se recortaba a la pista de
                # audio mas corta, perdiendo el CTA de los ultimos 3s. Sin ese flag,
                # el audio simplemente termina en silencio y el video sigue completo.
                cmd += ['-t', str(duration), '-c:v', 'copy', '-c:a', 'aac', output_path]
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

    def generate(self, script: dict, colors: list[str], filename_prefix: str) -> tuple[str, str]:
        try:
            clips = self._generate_video_clips(script['scene_prompts'])
            if len(clips) < 3:
                logger.warning(f"Reel abortado: solo {len(clips)}/3 clips de Veo generados")
                return '', ''

            music = self._generate_music(script['music_mood'])
            narration = self._generate_narration(script['narration_script'])
            hook_png = self._render_text_overlay(script['hook_text'], script['highlight_word'], 'hook', colors)
            cta_png = self._render_text_overlay('', '', 'cta', colors, cta_text=script['tag_cta'])

            final_video = self._assemble_reel(clips, music, narration, hook_png, cta_png)
            poster = self._extract_poster_frame(final_video)

            video_url = self._upload_video_to_storage(final_video, filename_prefix)
            poster_url = self._upload_to_storage(poster, f'{filename_prefix}-poster')
            return video_url, poster_url
        except Exception as e:
            logger.error(f"ReelGenerator.generate error: {e}")
            return '', ''

    def _upload_to_storage(self, image_bytes: bytes, filename: str) -> str:
        with track_external_api('gcs'):
            client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
            bucket = client.bucket(self._bucket)
            blob = bucket.blob(f'posts/{filename}.png')
            blob.upload_from_string(image_bytes, content_type='image/png')
        GCS_OPERATIONS.labels(operation='upload').inc()
        return f'{blob.public_url}?v={int(time.time())}'

    def _upload_video_to_storage(self, video_bytes: bytes, filename: str) -> str:
        with track_external_api('gcs'):
            client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
            bucket = client.bucket(self._bucket)
            blob = bucket.blob(f'reels/{filename}.mp4')
            blob.upload_from_string(video_bytes, content_type='video/mp4')
        GCS_OPERATIONS.labels(operation='upload').inc()
        return f'{blob.public_url}?v={int(time.time())}'



