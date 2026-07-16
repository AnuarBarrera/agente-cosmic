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
from PIL import ImageFont
from core.shared.metrics import GCS_OPERATIONS
from core.shared.metrics_utils import (
    track_external_api, record_tokens, record_veo_generation,
    record_lyria_generation, record_tts_generation,
    record_playwright_overlay_fallback, record_imagen_generation,
)
from core.shared.rate_limiter import call_with_429_retry
from core.content_pipeline.generators.subtitle_generator import SubtitleGenerator


logger = logging.getLogger(__name__)

_VEO_CLIP_DURATION_SECONDS = 8
_IMAGE_SHOT_DURATION_SECONDS = 2.0  # duracion de cada shot corto de imagen (escenas 1-5)
# La LRO (long-running operation) de Veo puede quedar en done=False indefinidamente
# sin devolver error — el polling sin limite espera para siempre. 30 min (no los 5
# min sugeridos por una fuente externa) porque en produccion real un clip tardo 24
# min y SI completo con exito; un limite mas corto lo habria descartado igual.
_VEO_POLL_TIMEOUT_SECONDS = 1800
_VIDEO_WIDTH = 1080

# Fuente compartida por hook, CTA y subtitulos — todo el texto de los reels se
# compone con el filtro drawtext de ffmpeg (fontfile= apunta directo al .ttf).
# Playwright/Chromium se elimino del pipeline de reels: bajo CPU cargado
# (proceso corriendo varios minutos junto a Veo/Lyria/TTS) el texto se
# desbordaba en produccion de forma no reproducible en aislado, incluso tras
# fuente local, fuente base64, position:absolute y reflow forzado — el propio
# pintado de Chromium usaba metricas de glifo distintas a las que reportaba
# el DOM (scrollWidth/clientWidth no detectaban el desborde real). drawtext
# nunca fallo en ninguna prueba real, por eso reemplaza a Playwright entero.
_DRAWTEXT_FONT_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', 'static', 'content_pipeline', 'fonts', 'Poppins-Bold.ttf',
))

_VIDEO_HEIGHT = 1920  # alto fijo del canvas de Playwright (viewport 1080x1920)

_DEFAULT_CLIP_FPS = 24.0  # usado solo cuando no hay clip real de Veo del cual medir fps

_OVERLAY_TEMPLATE_MAP = {'hook': 'reel_hook.html', 'cta': 'reel_cta.html'}


def _load_font_data_uri() -> str:
    with open(_DRAWTEXT_FONT_PATH, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('ascii')
    return f'data:font/ttf;base64,{encoded}'


_OVERLAY_FONT_DATA_URI = _load_font_data_uri()


_HOOK_FONTSIZE = 64
_HOOK_MAX_CHARS = 20
_HOOK_LINE_HEIGHT = 90
_HOOK_TOP_Y = 220
_HOOK_END_SECONDS = 3
_HOOK_BOX_BORDERW = 10

_CTA_FONTSIZE = 54
_CTA_MAX_CHARS = 24
_CTA_BOX_BORDERW = 24

_SUBTITLE_FONTSIZE = 42

# Prompt de emergencia para cuando el filtro de contenido de Lyria bloquea el
# music_mood generado por el guionista (ver _generate_music) — generico y
# neutro (estilo "corporate stock music") para no chocar con el tono de
# ningun negocio.
_MUSIC_FALLBACK_PROMPT = (
    "instrumental, corporate uplifting background music, soft piano and "
    "light percussion, warm and motivational, 100 BPM"
)

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def _escape_drawtext(text: str) -> str:
    text = text.replace('\\', '\\\\')
    text = text.replace(':', '\\:')
    text = text.replace("'", "\\'")
    text = text.replace('%', '\\%')
    return text


def _wrap_text(text: str, max_chars: int = 22) -> str:
    if len(text) <= max_chars:
        return text
    words = text.split()
    lines = []
    current = ''
    for word in words:
        candidate = f'{current} {word}'.strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return '\n'.join(lines)


def _hex_to_ffmpeg_color(hex_color: str) -> str:
    return '0x' + hex_color.lstrip('#')


def _readable_text_color(hex_color: str) -> str:
    # El texto resaltado del hook y el CTA se pintan sobre una caja del color
    # primario de la marca — un fontcolor fijo asume que ese color siempre es
    # oscuro (o siempre claro), lo cual no es cierto para marcas con paleta
    # clara. Se calcula el brillo percibido (formula YIQ) del color de fondo
    # y se elige texto blanco o negro segun contraste real.
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    return 'black' if brightness > 150 else 'white'


def _write_tmp_png(tmp_dir: str, filename: str, data: bytes) -> str:
    path = os.path.join(tmp_dir, filename)
    with open(path, 'wb') as f:
        f.write(data)
    return path


def _measure_text_width(text: str, fontsize: int) -> int:
    if not text:
        return 0
    if fontsize not in _font_cache:
        _font_cache[fontsize] = ImageFont.truetype(_DRAWTEXT_FONT_PATH, fontsize)
    return int(_font_cache[fontsize].getlength(text))


def _probe_video_width(video_path: str) -> int:
    # Veo NO garantiza 1080px de ancho para aspect_ratio='9:16' — en la practica
    # veo-3.0-fast-generate-001 devolvio 720x1280 en produccion real. El
    # centrado nativo de ffmpeg ((w-text_w)/2, usado por CTA/subtitulos/lineas
    # de hook sin resaltado) ya se ajusta solo a esto porque 'w' es dinamico,
    # pero el posicionamiento manual de los 3 segmentos del hook resaltado
    # necesita el ancho real para calcular el cursor, no un valor fijo.
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=width', '-of', 'csv=p=0', video_path],
        check=True, capture_output=True, text=True,
    )
    return int(result.stdout.strip())


def _probe_video_dimensions(video_path: str) -> tuple[int, int, float]:
    # Extiende _probe_video_width (que solo mide ancho, para el centrado del hook)
    # con alto y fps reales — necesarios para normalizar los clips de Imagen+zoompan
    # exactamente al mismo formato que produjo Veo, para que el concat -c copy de
    # _assemble_reel siga funcionando sin cambios.
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=width,height,r_frame_rate', '-of', 'csv=p=0', video_path],
        check=True, capture_output=True, text=True,
    )
    width_str, height_str, fps_str = result.stdout.strip().split(',')
    num, den = fps_str.split('/')
    fps = float(num) / float(den) if float(den) != 0 else float(num)
    return int(width_str), int(height_str), fps


def _probe_video_duration(video_path: str) -> float:
    # Con clips de duracion mixta (Veo 8s + shots de imagen de 2s) la formula
    # anterior duration = len(clips) * _VEO_CLIP_DURATION_SECONDS ya no es
    # valida — se mide la duracion real del video ya concatenado.
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', video_path],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def _build_hook_filter_parts(hook_text: str, highlight_word: str, primary_color: str,
                              source_label: str, video_width: int = _VIDEO_WIDTH,
                              scale: float = 1.0) -> tuple[list[str], str]:
    box_color = _hex_to_ffmpeg_color(primary_color)
    highlight_fontcolor = _readable_text_color(primary_color)
    fontsize = max(1, int(_HOOK_FONTSIZE * scale))
    line_height = int(_HOOK_LINE_HEIGHT * scale)
    top_y = int(_HOOK_TOP_Y * scale)
    box_borderw = max(1, int(_HOOK_BOX_BORDERW * scale))
    lines = _wrap_text(hook_text, max_chars=_HOOK_MAX_CHARS).split('\n')
    highlight_lower = highlight_word.strip().lower()
    filter_parts = []
    last_label = source_label
    enable = f"between(t,0,{_HOOK_END_SECONDS})"

    for i, line in enumerate(lines):
        y = top_y + i * line_height
        idx = line.lower().find(highlight_lower) if highlight_lower else -1

        if idx == -1:
            next_label = f'hook{i}'
            filter_parts.append(
                f"[{last_label}]drawtext=fontfile={_DRAWTEXT_FONT_PATH}:text='{_escape_drawtext(line)}':"
                f"fontsize={fontsize}:fontcolor=white:borderw=3:bordercolor=black:"
                f"x=(w-text_w)/2:y={y}:enable='{enable}'[{next_label}]"
            )
            last_label = next_label
            continue

        before = line[:idx]
        highlight = line[idx:idx + len(highlight_word)]
        after = line[idx + len(highlight_word):]
        before_w = _measure_text_width(before, fontsize)
        highlight_w = _measure_text_width(highlight, fontsize)
        after_w = _measure_text_width(after, fontsize)
        total_w = before_w + highlight_w + 2 * box_borderw + after_w
        cursor = (video_width - total_w) // 2

        if before:
            next_label = f'hook{i}a'
            filter_parts.append(
                f"[{last_label}]drawtext=fontfile={_DRAWTEXT_FONT_PATH}:text='{_escape_drawtext(before)}':"
                f"fontsize={fontsize}:fontcolor=white:borderw=3:bordercolor=black:"
                f"x={cursor}:y={y}:enable='{enable}'[{next_label}]"
            )
            last_label = next_label
        cursor += before_w

        next_label = f'hook{i}b'
        filter_parts.append(
            f"[{last_label}]drawtext=fontfile={_DRAWTEXT_FONT_PATH}:text='{_escape_drawtext(highlight)}':"
            f"fontsize={fontsize}:fontcolor={highlight_fontcolor}:box=1:boxcolor={box_color}@1.0:"
            f"boxborderw={box_borderw}:x={cursor + box_borderw}:y={y}:"
            f"enable='{enable}'[{next_label}]"
        )
        last_label = next_label
        cursor += highlight_w + 2 * box_borderw

        if after:
            next_label = f'hook{i}c'
            filter_parts.append(
                f"[{last_label}]drawtext=fontfile={_DRAWTEXT_FONT_PATH}:text='{_escape_drawtext(after)}':"
                f"fontsize={fontsize}:fontcolor=white:borderw=3:bordercolor=black:"
                f"x={cursor}:y={y}:enable='{enable}'[{next_label}]"
            )
            last_label = next_label

    return filter_parts, last_label


def _build_cta_filter_parts(cta_text: str, primary_color: str, source_label: str,
                             cta_start: float, duration: float, scale: float = 1.0) -> tuple[list[str], str]:
    box_color = _hex_to_ffmpeg_color(primary_color)
    fontcolor = _readable_text_color(primary_color)
    fontsize = max(1, int(_CTA_FONTSIZE * scale))
    box_borderw = max(1, int(_CTA_BOX_BORDERW * scale))
    text = _escape_drawtext(_wrap_text(cta_text, max_chars=_CTA_MAX_CHARS))
    next_label = 'cta0'
    filter_part = (
        f"[{source_label}]drawtext=fontfile={_DRAWTEXT_FONT_PATH}:text='{text}':"
        f"fontsize={fontsize}:fontcolor={fontcolor}:box=1:boxcolor={box_color}@1.0:"
        f"boxborderw={box_borderw}:x=(w-text_w)/2:y=(h-text_h)/2:"
        f"enable='between(t,{cta_start},{duration})'[{next_label}]"
    )
    return [filter_part], next_label


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


class ReelGenerator:
    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

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

    def _render_text_overlay_playwright(self, text: str, highlight_word: str,
                                         style: str, primary_color: str,
                                         cta_text: str = '') -> bytes | None:
        try:
            template_path = os.path.normpath(os.path.join(
                os.path.dirname(__file__), '..', 'templates', 'content_pipeline',
                _OVERLAY_TEMPLATE_MAP[style],
            ))
            with open(template_path) as f:
                html = f.read()
            html = html.replace('{{primary_color}}', primary_color)
            html = html.replace('{{text_color}}', _readable_text_color(primary_color))
            html = html.replace('{{font_path}}', _OVERLAY_FONT_DATA_URI)

            if style == 'hook':
                escaped = _html.escape(text)
                if highlight_word:
                    escaped_word = _html.escape(highlight_word)
                    pattern = re.compile(re.escape(escaped_word), re.IGNORECASE)
                    escaped = pattern.sub(f'<span class="highlight">{escaped_word}</span>', escaped, count=1)
                html = html.replace('{{hook_html}}', escaped)
            else:
                html = html.replace('{{cta_text}}', _html.escape(cta_text))

            selector = '.hook' if style == 'hook' else '.cta'
            for attempt in range(2):
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        headless=True,
                        args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
                    )
                    page = browser.new_page(viewport={'width': 1080, 'height': 1920})
                    page.set_content(html, wait_until='load')
                    page.evaluate('document.fonts.ready')
                    page.evaluate('document.body.offsetHeight')
                    page.wait_for_timeout(300)
                    overflow_px = page.evaluate(
                        f"() => {{ const el = document.querySelector('{selector}'); "
                        f"return el.scrollWidth - el.clientWidth; }}"
                    )
                    overflows = overflow_px is not None and overflow_px > 2
                    png_bytes = page.screenshot(omit_background=True)
                    browser.close()
                if not overflows:
                    return png_bytes
                logger.warning(f"Overlay Playwright '{style}' se salio del cuadro (intento {attempt + 1})")
        except Exception as e:
            logger.warning(f"Overlay Playwright '{style}' fallo con excepcion (cae a drawtext): {e}")

        record_playwright_overlay_fallback(style)
        return None

    def _probe_clip_dimensions(self, video_bytes: bytes) -> tuple[int, int, float]:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'probe.mp4')
            with open(path, 'wb') as f:
                f.write(video_bytes)
            return _probe_video_dimensions(path)

    def _generate_still_scene_clip(self, prompt: str, width: int, height: int, fps: float,
                                    duration: float = _VEO_CLIP_DURATION_SECONDS) -> bytes | None:
        still = self._generate_scene_still(prompt)
        if still is None:
            still = self._generate_scene_still(prompt)  # 1 reintento
        if still is None:
            return None
        return self._animate_still_to_clip(still, width, height, fps, duration=duration)

    def _generate_video_clips(self, scene_prompts: list[str]) -> list[bytes]:
        # scene_prompts[0] va a Veo (video real, _VEO_CLIP_DURATION_SECONDS=8s).
        # scene_prompts[1:] (5 shots cortos) se generan como imagen fija (Imagen) +
        # animacion zoompan de ffmpeg, cada uno de _IMAGE_SHOT_DURATION_SECONDS=2s —
        # ritmo de corte rapido tipo publicidad, costo marginal (Imagen $0.04/imagen).
        # Ver docs/superpowers/specs/2026-07-15-reels-short-image-shots-design.md
        clips = []

        veo_clip = self._generate_single_clip(scene_prompts[0])
        if veo_clip is None:
            veo_clip = self._generate_single_clip(scene_prompts[0])  # 1 reintento

        if veo_clip is not None:
            clips.append(veo_clip)
            width, height, fps = self._probe_clip_dimensions(veo_clip)
        else:
            logger.warning(
                f"Clip de Veo fallido tras reintento, escena 0 tambien se genera via "
                f"Imagen: {scene_prompts[0][:80]}"
            )
            width, height, fps = _VIDEO_WIDTH, _VIDEO_HEIGHT, _DEFAULT_CLIP_FPS
            still_clip = self._generate_still_scene_clip(
                scene_prompts[0], width, height, fps, duration=_VEO_CLIP_DURATION_SECONDS,
            )
            if still_clip is not None:
                clips.append(still_clip)

        for prompt in scene_prompts[1:]:
            still_clip = self._generate_still_scene_clip(
                prompt, width, height, fps, duration=_IMAGE_SHOT_DURATION_SECONDS,
            )
            if still_clip is not None:
                clips.append(still_clip)
            else:
                logger.warning(f"Escena de Imagen fallida tras reintento, se omite: {prompt[:80]}")

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
                            duration_seconds=_VEO_CLIP_DURATION_SECONDS,
                            number_of_videos=1,
                            generate_audio=False,
                        ),
                    )
            operation = call_with_429_retry(_call, settings.VERTEX_VIDEO_MODEL)
            client = _vertex_client()
            poll_start = time.monotonic()
            while not operation.done:
                if time.monotonic() - poll_start > _VEO_POLL_TIMEOUT_SECONDS:
                    logger.warning(f"Veo no completo en {_VEO_POLL_TIMEOUT_SECONDS}s, abandonando esta operacion")
                    return None
                time.sleep(10)
                operation = client.operations.get(operation)
            if operation.error:
                logger.warning(f"Veo devolvió error: {operation.error}")
                return None
            generated = operation.result.generated_videos
            if not generated:
                return None
            record_veo_generation(duration_seconds=_VEO_CLIP_DURATION_SECONDS)
            return generated[0].video.video_bytes
        except Exception as e:
            logger.warning(f"Veo clip generation failed: {e}")
            return None

    def _generate_scene_still(self, prompt: str) -> bytes | None:
        try:
            client = _vertex_client()
            with track_external_api('imagen3', operation='image_generate'):
                resp = client.models.generate_images(
                    model=settings.VERTEX_IMAGE_MODEL,
                    prompt=prompt + self._VEO_SAFE_CONSTRAINTS,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio='9:16',
                    ),
                )
            if resp.generated_images:
                record_imagen_generation('reel_scene')
                return resp.generated_images[0].image.image_bytes
            return None
        except Exception as e:
            logger.warning(f"Imagen scene generation failed: {e}")
            return None

    def _animate_still_to_clip(self, image_bytes: bytes, width: int, height: int,
                                fps: float, duration: float = _VEO_CLIP_DURATION_SECONDS) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            image_path = os.path.join(tmp, 'still.png')
            with open(image_path, 'wb') as f:
                f.write(image_bytes)
            output_path = os.path.join(tmp, 'animated.mp4')
            subprocess.run(
                ['ffmpeg', '-y', '-loop', '1', '-i', image_path, '-t', str(duration),
                 '-vf', (
                     "scale=8000:-1,"
                     "zoompan=z='min(zoom+0.0015,1.08)':d=1:"
                     "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                     f"s={width}x{height}:fps={fps}"
                 ),
                 '-c:v', 'libx264', '-pix_fmt', 'yuv420p', output_path],
                check=True, capture_output=True,
            )
            with open(output_path, 'rb') as f:
                return f.read()

    def _generate_music(self, music_mood: str) -> bytes | None:
        # El filtro de contenido de Lyria 3 Clip (preview) es no-determinista —
        # confirmado reintentando el MISMO prompt: falla y luego funciona sin
        # cambiar nada. 1 reintento con el mismo mood y, si ambos fallan, un
        # ultimo intento con un prompt generico "corporate stock music" que
        # no depende del guion — antes de degradar a "reel sin musica".
        result = self._generate_music_attempt(f"Instrumental only, no vocals. {music_mood}")
        if result is None:
            result = self._generate_music_attempt(f"Instrumental only, no vocals. {music_mood}")
        if result is None:
            result = self._generate_music_attempt(_MUSIC_FALLBACK_PROMPT)
        return result

    def _generate_music_attempt(self, prompt: str) -> bytes | None:
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
                    input=prompt,
                )
            audio = getattr(interaction, 'output_audio', None)
            if audio is not None and getattr(audio, 'data', None):
                # La API de Interactions (a diferencia de generate_content) devuelve
                # AudioContent.data como string base64, no bytes crudos.
                record_lyria_generation()
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
                    record_tts_generation(char_count=len(narration_script))
                    return part.inline_data.data
            return None
        except Exception as e:
            logger.warning(f"TTS narration generation failed (reel sin narracion): {e}")
            return None

    def _assemble_reel(self, clips: list[bytes], music: bytes | None, narration: bytes | None,
                        script: dict, colors: list[str], subtitles: list[dict] | None = None) -> bytes:
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

            duration = _probe_video_duration(concat_path)
            cta_start = max(0, duration - 3)
            primary_color = colors[0] if colors else '#e94560'
            video_width = _probe_video_width(concat_path)
            # Todas las constantes de tamano/posicion (fontsize, box borders, Y)
            # estan calibradas para un video de 1080px de ancho — Veo no
            # garantiza esa resolucion (en produccion real devolvio 720x1280),
            # asi que se escalan proporcionalmente al ancho real detectado.
            scale = video_width / _VIDEO_WIDTH

            hook_png = cta_png = None
            if settings.REEL_TEXT_OVERLAY_ENGINE == 'playwright':
                hook_png = self._render_text_overlay_playwright(
                    script['hook_text'], script['highlight_word'], 'hook', primary_color,
                )
                cta_png = self._render_text_overlay_playwright(
                    '', '', 'cta', primary_color, cta_text=script['tag_cta'],
                )

            scaled_w = max(1, int(_VIDEO_WIDTH * scale))
            scaled_h = max(1, int(_VIDEO_HEIGHT * scale))
            extra_inputs = []
            filter_parts = []
            last_label = '0:v'

            if hook_png is not None:
                extra_inputs += ['-i', _write_tmp_png(tmp, 'hook.png', hook_png)]
                idx = len(extra_inputs) // 2
                filter_parts.append(
                    f"[{idx}:v]scale={scaled_w}:{scaled_h}[hookscaled];"
                    f"[{last_label}][hookscaled]overlay=0:0:enable='between(t,0,{_HOOK_END_SECONDS})'[hookout]"
                )
                last_label = 'hookout'
            else:
                filter_parts_h, last_label = _build_hook_filter_parts(
                    script['hook_text'], script['highlight_word'], primary_color, last_label,
                    video_width=video_width, scale=scale,
                )
                filter_parts += filter_parts_h

            if cta_png is not None:
                extra_inputs += ['-i', _write_tmp_png(tmp, 'cta.png', cta_png)]
                idx = len(extra_inputs) // 2
                filter_parts.append(
                    f"[{idx}:v]scale={scaled_w}:{scaled_h}[ctascaled];"
                    f"[{last_label}][ctascaled]overlay=0:0:enable='between(t,{cta_start},{duration})'[ctaout]"
                )
                last_label = 'ctaout'
            else:
                cta_parts, last_label = _build_cta_filter_parts(
                    script['tag_cta'], primary_color, last_label, cta_start, duration, scale=scale,
                )
                filter_parts += cta_parts

            subtitle_fontsize = max(1, int(_SUBTITLE_FONTSIZE * scale))
            subtitle_y_offset = int(300 * scale)
            for i, sub in enumerate(subtitles or []):
                next_label = f'sub{i}'
                text = _escape_drawtext(_wrap_text(sub['text']))
                filter_parts.append(
                    f"[{last_label}]drawtext=fontfile={_DRAWTEXT_FONT_PATH}:text='{text}':"
                    f"fontcolor=white:fontsize={subtitle_fontsize}:borderw=3:bordercolor=black:"
                    f"x=(w-text_w)/2:y=h-{subtitle_y_offset}:"
                    f"enable='between(t,{sub['start']},{sub['end']})'[{next_label}]"
                )
                last_label = next_label

            filter_complex = ';'.join(filter_parts)
            overlay_path = os.path.join(tmp, 'overlay.mp4')
            subprocess.run(
                ['ffmpeg', '-y', '-i', concat_path] + extra_inputs +
                ['-filter_complex', filter_complex,
                 '-map', f'[{last_label}]', '-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
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
                # -movflags +faststart mueve el atomo moov (indice del archivo) al
                # inicio del MP4 — sin esto queda al final (comportamiento default
                # del muxer), y el navegador necesita descargar casi todo el
                # archivo antes de poder reproducirlo en streaming (funciona al
                # descargar completo, falla en el reproductor <video> de la UI).
                subprocess.run(['ffmpeg', '-y', '-i', overlay_path, '-c', 'copy',
                                 '-movflags', '+faststart', output_path],
                                check=True, capture_output=True)
            else:
                cmd = ['ffmpeg', '-y', '-i', overlay_path] + audio_input_flags
                if audio_stream_count == 2:
                    filter_complex = '[1:a]volume=0.3[music];[2:a][music]amix=inputs=2:duration=longest[a]'
                    cmd += ['-filter_complex', filter_complex, '-map', '0:v', '-map', '[a]']
                else:
                    cmd += ['-map', '0:v', '-map', '1:a']
                # SIN -shortest: la duracion del video (fijada por -t) manda. La
                # narracion (TTS, ~15-20s hablados) suele ser mas corta que el video
                # (24s) — con -shortest el output entero se recortaba a la pista de
                # audio mas corta, perdiendo el CTA de los ultimos 3s. Sin ese flag,
                # el audio simplemente termina en silencio y el video sigue completo.
                # +faststart: ver comentario arriba (mismo motivo, streaming en <video>).
                cmd += ['-t', str(duration), '-c:v', 'copy', '-c:a', 'aac',
                        '-movflags', '+faststart', output_path]
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
            subtitles = []
            if narration is not None:
                subtitles = SubtitleGenerator().generate(narration, script['narration_script'])

            final_video = self._assemble_reel(clips, music, narration, script, colors, subtitles)
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
