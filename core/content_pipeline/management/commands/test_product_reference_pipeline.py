import os
import tempfile
import time
import traceback

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from google.genai import types

from core.content_pipeline.generators.image_generator import _detect_mime, _vertex_client

# Modelo de imagen con generacion nativa de Gemini que SI acepta imagenes de
# referencia dentro de `contents` (confirmado real via `client.models.list()`
# contra el proyecto de Vertex AI de Cosmic, 2026-07-27) — distinto del Imagen 3
# clasico (`settings.VERTEX_IMAGE_MODEL`) que el resto del pipeline usa hoy.
_REFERENCE_IMAGE_MODEL = 'publishers/google/models/gemini-2.5-flash-image'

_VEO_POLL_TIMEOUT_SECONDS = 300
_VEO_POLL_INTERVAL_SECONDS = 10


class Command(BaseCommand):
    help = (
        'Prueba exploratoria (solo admin, no toca el pipeline de produccion): '
        'valida con una llamada REAL si Imagen (via Gemini nativo) y Veo aceptan '
        'una foto de producto real como referencia/ingrediente para GENERAR una '
        'imagen/video nuevos que la incorporen — distinto de editar/rellenar el '
        'fondo de la foto original (eso era BGSWAP, HALLAZGO 65, eliminado).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--photo', required=True, help='Ruta local a una foto real de producto.')
        parser.add_argument('--business-name', default='', help='Nombre del negocio, opcional, para el prompt.')
        default_out_dir = os.path.join(tempfile.gettempdir(), 'product_reference_test')
        parser.add_argument(
            '--out-dir', default=default_out_dir,
            help=f'Carpeta donde guardar los resultados (default: {default_out_dir}).',
        )
        parser.add_argument(
            '--skip-video', action='store_true',
            help='Omite la prueba de Veo (mas lenta, ~1-3 min) y solo corre la de imagen.',
        )

    def handle(self, *args, **options):
        photo_path = options['photo']
        if not os.path.isfile(photo_path):
            raise CommandError(f'No existe el archivo: {photo_path}')

        with open(photo_path, 'rb') as f:
            photo_bytes = f.read()
        mime = _detect_mime(photo_bytes)
        business_name = options['business_name'] or 'este negocio'

        out_dir = options['out_dir']
        os.makedirs(out_dir, exist_ok=True)

        self.stdout.write(f'Foto cargada: {photo_path} ({len(photo_bytes)} bytes, {mime})')
        self.stdout.write(f'Resultados en: {out_dir}\n')

        new_scene_bytes = self._test_image_reference(photo_bytes, mime, business_name, out_dir)
        self.stdout.write('')
        if options['skip_video']:
            self.stdout.write(self.style.WARNING('Pruebas de video omitidas (--skip-video).'))
            return

        self._test_video_reference(photo_bytes, mime, business_name, out_dir)
        self.stdout.write('')
        if new_scene_bytes is not None:
            self._test_video_chained(new_scene_bytes, business_name, out_dir)
        else:
            self.stdout.write(self.style.WARNING(
                'Prueba C omitida — la Prueba A no genero una imagen para encadenar.'
            ))

    def _test_image_reference(self, photo_bytes: bytes, mime: str, business_name: str, out_dir: str) -> bytes | None:
        self.stdout.write(self.style.HTTP_INFO('=== PRUEBA A: imagen con referencia (Gemini nativo) ==='))
        self.stdout.write(f'Modelo: {_REFERENCE_IMAGE_MODEL}')
        prompt = (
            f'Using the product shown in this reference image, generate a brand-new professional '
            f'product photograph for {business_name}: a completely new scene, new background, new '
            f'lighting and composition — NOT an edit of the input image. Incorporate this exact '
            f'product as it appears (same shape, color, texture, any visible branding) as the subject '
            f'of the new photograph. Photorealistic, studio-quality, natural lighting.'
        )
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=photo_bytes, mime_type=mime)
            resp = client.models.generate_content(
                model=_REFERENCE_IMAGE_MODEL,
                contents=[image_part, prompt],
                config=types.GenerateContentConfig(response_modalities=['IMAGE', 'TEXT']),
            )
            result_bytes = None
            for part in resp.candidates[0].content.parts:
                if part.inline_data:
                    result_bytes = part.inline_data.data
                    out_path = os.path.join(out_dir, 'image_reference_result.png')
                    with open(out_path, 'wb') as f:
                        f.write(result_bytes)
                    self.stdout.write(self.style.SUCCESS(f'OK — imagen generada, guardada en {out_path}'))
                if part.text:
                    self.stdout.write(f'Texto devuelto por el modelo: {part.text[:300]}')
            if result_bytes is None:
                self.stdout.write(self.style.ERROR(
                    'La llamada tuvo exito pero NO devolvio ninguna imagen (solo texto) — '
                    'revisa el texto de arriba, puede que el modelo haya descrito la imagen '
                    'en vez de generarla.'
                ))
            return result_bytes
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'FALLO: {type(e).__name__}: {e}'))
            self.stdout.write(traceback.format_exc())
            return None

    def _test_video_reference(self, photo_bytes: bytes, mime: str, business_name: str, out_dir: str) -> None:
        self.stdout.write(self.style.HTTP_INFO('=== PRUEBA B: video con referencia (Veo reference_images) ==='))
        self.stdout.write(f'Modelo: {settings.VERTEX_VIDEO_MODEL}')
        prompt = (
            f'Cinematic product showcase for {business_name}, featuring the reference product prominently. '
            f'Slow camera push-in, soft natural lighting, elegant real-world setting. Photorealistic, 4k.'
        )
        try:
            client = _vertex_client()
            resp = client.models.generate_videos(
                model=settings.VERTEX_VIDEO_MODEL,
                prompt=prompt,
                config=types.GenerateVideosConfig(
                    reference_images=[
                        types.VideoGenerationReferenceImage(
                            image=types.Image(image_bytes=photo_bytes, mime_type=mime),
                            reference_type=types.VideoGenerationReferenceType.ASSET,
                        ),
                    ],
                    aspect_ratio='9:16',
                    duration_seconds=8,
                    number_of_videos=1,
                    generate_audio=False,
                ),
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f'FALLO al INICIAR la generacion (el modelo probablemente no acepta '
                f'reference_images en esta version): {type(e).__name__}: {e}'
            ))
            self.stdout.write(traceback.format_exc())
            return

        self.stdout.write('Operacion iniciada, esperando resultado (hasta 5 min)...')
        poll_start = time.monotonic()
        operation = resp
        while not operation.done:
            if time.monotonic() - poll_start > _VEO_POLL_TIMEOUT_SECONDS:
                self.stdout.write(self.style.ERROR(
                    f'Timeout tras {_VEO_POLL_TIMEOUT_SECONDS}s esperando a Veo — la operacion '
                    f'sigue corriendo del lado de Google, pero este script se rinde.'
                ))
                return
            time.sleep(_VEO_POLL_INTERVAL_SECONDS)
            operation = client.operations.get(operation)

        if operation.error:
            self.stdout.write(self.style.ERROR(f'La operacion termino con error: {operation.error}'))
            return

        generated = operation.result.generated_videos
        if not generated:
            self.stdout.write(self.style.ERROR('La operacion termino sin error pero sin ningun video generado.'))
            return

        out_path = os.path.join(out_dir, 'video_reference_result.mp4')
        with open(out_path, 'wb') as f:
            f.write(generated[0].video.video_bytes)
        self.stdout.write(self.style.SUCCESS(f'OK — video generado, guardado en {out_path}'))

    def _test_video_chained(self, new_scene_bytes: bytes, business_name: str, out_dir: str) -> None:
        """Encadena Prueba A -> Prueba C: en vez de mandarle a Veo la foto CRUDA del
        usuario como reference_image (Prueba B, resulto ser casi un edit del original —
        mismo fondo, misma composicion), le manda la escena YA GENERADA por Gemini
        (Prueba A, escena 100% nueva) como PRIMER FRAME clasico (parametro `image=`,
        no `reference_images=`) para que Veo solo anime esa escena ya perfecta."""
        self.stdout.write(self.style.HTTP_INFO(
            '=== PRUEBA C: video encadenado (imagen de Prueba A como primer frame) ==='
        ))
        self.stdout.write(f'Modelo: {settings.VERTEX_VIDEO_MODEL}')
        prompt = (
            f'Cinematic slow push-in on this product photography scene for {business_name}. '
            f'Gentle ambient motion (light shifting, soft background movement) — keep the product '
            f'and composition stable. Photorealistic, 4k.'
        )
        try:
            client = _vertex_client()
            resp = client.models.generate_videos(
                model=settings.VERTEX_VIDEO_MODEL,
                prompt=prompt,
                image=types.Image(image_bytes=new_scene_bytes, mime_type='image/png'),
                config=types.GenerateVideosConfig(
                    aspect_ratio='9:16',
                    duration_seconds=8,
                    number_of_videos=1,
                    generate_audio=False,
                ),
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'FALLO al INICIAR la generacion: {type(e).__name__}: {e}'))
            self.stdout.write(traceback.format_exc())
            return

        self.stdout.write('Operacion iniciada, esperando resultado (hasta 5 min)...')
        poll_start = time.monotonic()
        operation = resp
        while not operation.done:
            if time.monotonic() - poll_start > _VEO_POLL_TIMEOUT_SECONDS:
                self.stdout.write(self.style.ERROR(f'Timeout tras {_VEO_POLL_TIMEOUT_SECONDS}s esperando a Veo.'))
                return
            time.sleep(_VEO_POLL_INTERVAL_SECONDS)
            operation = client.operations.get(operation)

        if operation.error:
            self.stdout.write(self.style.ERROR(f'La operacion termino con error: {operation.error}'))
            return

        generated = operation.result.generated_videos
        if not generated:
            self.stdout.write(self.style.ERROR('La operacion termino sin error pero sin ningun video generado.'))
            return

        out_path = os.path.join(out_dir, 'video_chained_result.mp4')
        with open(out_path, 'wb') as f:
            f.write(generated[0].video.video_bytes)
        self.stdout.write(self.style.SUCCESS(f'OK — video encadenado generado, guardado en {out_path}'))
