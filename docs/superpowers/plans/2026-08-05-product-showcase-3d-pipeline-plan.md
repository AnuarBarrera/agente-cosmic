# Pipeline de producto real: video-showcase 3D vía HyperFrames — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el pipeline de "producto real" solo-admin (hoy Gemini regenera la escena + Veo la anima) por una foto real sin tocar + una plantilla 3D animada (Three.js vía HyperFrames), eliminando las alucinaciones/rechazos falsos del pipeline anterior.

**Architecture:** Foto subida → 1 llamada a Vision (screenshot + contenido sensible) → `enhance_photo_classic()` (sin cambios) → render con HyperFrames/Three.js (1 template, foto real como textura + colores de marca) → poster (ffmpeg) → GCS. Cero cliente de Gemini/Vertex en este pipeline.

**Tech Stack:** Django, `google-cloud-vision`, HyperFrames (CLI Node ya instalado en `core/content_pipeline/hyperframes_reel/`), Three.js (nuevo), ffmpeg (ya en uso).

## Global Constraints

- Alcance: SOLO `core/content_pipeline/generators/product_reference_generator.py` → `product_showcase_generator.py`, su test file, `tasks.py` (un call site), `models.py` (1 choice de `AnalysisJob.generation_mode`), `new_analysis.html` (1 radio). Nada del pipeline de contenido normal (tenants reales, `ImageGenerator`/`ReelGenerator`) se toca.
- Dependencias npm en `core/content_pipeline/hyperframes_reel/package.json`: versión EXACTA, sin `^` ni `~` (mismo criterio que `hyperframes: "0.7.59"` y `gsap: "3.14.2"` ya presentes).
- Nada de red en render-time: todo asset (JS de Three.js, la foto del producto) se sirve desde archivos locales del proyecto HyperFrames, nunca CDN ni URL externa.
- `docker compose exec -T backend ...` / `docker compose exec -T rqworker ...` es la única forma correcta de correr comandos Python de este proyecto — nunca `pytest`/`python` en el host.
- Los contenedores deben estar arriba (`docker compose up -d`) y con credenciales de GCP válidas (`docker-compose.override.yml` local ya restaura esto en este entorno; si `gcloud auth application-default login` expiró, pedir al usuario que lo corra de nuevo).
- Cliente de Vision: `vision.ImageAnnotatorClient(client_options={'quota_project_id': settings.GOOGLE_CLOUD_PROJECT})` — mismo patrón ya arreglado en `logo_analyzer.py` (commit `a40fcc2`). Nunca instanciar sin ese `client_options`.
- No usar `pytest`/tests automatizados para juzgar el resultado VISUAL del template 3D — eso es responsabilidad de la Tarea 1 (verificación empírica manual, obligatoria antes de la Tarea 2).

---

### Task 1: Composición Three.js de HyperFrames + validación empírica real

**Files:**
- Modify: `core/content_pipeline/hyperframes_reel/package.json`
- Modify: `core/content_pipeline/hyperframes_reel/package-lock.json` (regenerado por `npm install`)
- Create: `core/content_pipeline/hyperframes_reel/assets/tmp/placeholder.png`
- Create: `core/content_pipeline/hyperframes_reel/compositions/product-showcase.html`

**Interfaces:**
- Produces: la composición `compositions/product-showcase.html`, con `data-composition-id="product-showcase"`, `data-duration="6"`, dimensiones `1080×1920`, variables declaradas `photo_src` (string, ruta relativa a una imagen), `primary_color` (color), `secondary_color` (color). La Tarea 2 invoca esta composición vía el binario de HyperFrames pasando esas 3 variables — el nombre de archivo y de variables debe coincidir EXACTO con lo de este task.

Este task NO toca Python/Django. Es 100% Node/HyperFrames. No hay "tests" en el sentido de pytest — la validación es un render real inspeccionado visualmente.

- [ ] **Step 1: Agregar la dependencia `three` al proyecto HyperFrames**

Editar `core/content_pipeline/hyperframes_reel/package.json` — agregar `three` a `dependencies`, versión exacta (sin `^`):

```json
{
  "name": "cosmic-reel-branding",
  "private": true,
  "type": "module",
  "dependencies": {
    "hyperframes": "0.7.59",
    "gsap": "3.14.2",
    "three": "0.181.2"
  }
}
```

- [ ] **Step 2: Instalar y regenerar el lockfile**

```bash
cd core/content_pipeline/hyperframes_reel && npm install && cd -
```

Verificar que el paquete quedó instalado en la ruta que la composición va a importar:

```bash
ls core/content_pipeline/hyperframes_reel/node_modules/three/build/three.module.js
```

Expected: el archivo existe (no error "No such file or directory"). Si la ruta real difiere (versiones futuras de `three` pueden reorganizar `build/`), ajustar el `importmap` del Step 4 a la ruta real reportada por este `ls` antes de continuar.

- [ ] **Step 3: Generar una imagen placeholder para el fallback de la variable `photo_src`**

```bash
mkdir -p core/content_pipeline/hyperframes_reel/assets/tmp
docker compose exec -T backend python -c "
from PIL import Image
Image.new('RGB', (800, 800), (26, 26, 46)).save('/app/core/content_pipeline/hyperframes_reel/assets/tmp/placeholder.png')
"
```

Expected: `core/content_pipeline/hyperframes_reel/assets/tmp/placeholder.png` existe (800×800, color sólido `#1a1a2e`).

- [ ] **Step 4: Crear la composición `compositions/product-showcase.html`**

```html
<!doctype html>
<html lang="es" data-composition-variables='[
  {"id":"photo_src","type":"string","label":"Foto del producto","default":"assets/tmp/placeholder.png"},
  {"id":"primary_color","type":"color","label":"Color primario","default":"#e94560"},
  {"id":"secondary_color","type":"color","label":"Color secundario","default":"#3ED694"}
]'>
<head>
  <meta charset="UTF-8" />
  <title>Product Showcase</title>
  <script type="importmap">
    { "imports": { "three": "node_modules/three/build/three.module.js" } }
  </script>
  <style>
    body { margin: 0; background: #0b0f14; overflow: hidden; }
    #root { position: relative; width: 1080px; height: 1920px; overflow: hidden; }
    .clip { position: absolute; inset: 0; }
    #three-canvas { width: 100%; height: 100%; display: block; }
    #product-photo { display: none; }
  </style>
</head>
<body>
  <div id="root" data-composition-id="product-showcase" data-start="0" data-width="1080" data-height="1920" data-duration="6">
    <section id="scene" class="clip" data-start="0" data-duration="6" data-track-index="0">
      <img id="product-photo" data-var-src="photo_src" src="assets/tmp/placeholder.png" crossorigin="anonymous" />
      <canvas id="three-canvas"></canvas>
    </section>
  </div>
  <script type="module">
    import * as THREE from "three";

    const DURATION = 6;
    const { primary_color, secondary_color } = window.__hyperframes.getVariables();

    const canvas = document.getElementById("three-canvas");
    const renderer = new THREE.WebGLRenderer({ canvas, alpha: false, antialias: true });
    renderer.setSize(1080, 1920, false);
    renderer.setPixelRatio(1);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b0f14);
    const camera = new THREE.PerspectiveCamera(35, 1080 / 1920, 0.1, 100);
    camera.position.set(0, 0, 7);

    scene.add(new THREE.HemisphereLight(0xffffff, 0x223344, 2.2));
    const keyLight = new THREE.DirectionalLight(0xffffff, 1.2);
    keyLight.position.set(2, 3, 4);
    scene.add(keyLight);

    const cardGroup = new THREE.Group();
    scene.add(cardGroup);

    const frameGeometry = new THREE.BoxGeometry(3.4, 3.4, 0.08);
    const frameMaterial = new THREE.MeshStandardMaterial({
      color: new THREE.Color(primary_color), roughness: 0.35, metalness: 0.15,
    });
    const frame = new THREE.Mesh(frameGeometry, frameMaterial);
    cardGroup.add(frame);

    const photoImg = document.getElementById("product-photo");
    const photoGeometry = new THREE.PlaneGeometry(3.0, 3.0);
    const photoMaterial = new THREE.MeshBasicMaterial({ color: 0xffffff });
    const photoMesh = new THREE.Mesh(photoGeometry, photoMaterial);
    photoMesh.position.z = 0.05;
    cardGroup.add(photoMesh);

    function applyPhotoTexture() {
      const texture = new THREE.Texture(photoImg);
      texture.needsUpdate = true;
      texture.colorSpace = THREE.SRGBColorSpace;
      photoMaterial.map = texture;
      photoMaterial.needsUpdate = true;
    }
    if (photoImg.complete && photoImg.naturalWidth > 0) {
      applyPhotoTexture();
    } else {
      photoImg.addEventListener("load", applyPhotoTexture, { once: true });
    }

    const PARTICLE_COUNT = 60;
    const particlePositions = new Float32Array(PARTICLE_COUNT * 3);
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const angle = (i / PARTICLE_COUNT) * Math.PI * 2;
      const radius = 2.6 + (i % 3) * 0.25;
      particlePositions[i * 3] = Math.cos(angle) * radius;
      particlePositions[i * 3 + 1] = Math.sin(angle) * radius;
      particlePositions[i * 3 + 2] = -0.5 - (i % 3) * 0.2;
    }
    const particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
    const particleMaterial = new THREE.PointsMaterial({
      color: new THREE.Color(secondary_color), size: 0.07, transparent: true, opacity: 0.85,
    });
    const particles = new THREE.Points(particleGeometry, particleMaterial);
    scene.add(particles);

    function renderAt(time) {
      const t = Math.min(time, DURATION);
      camera.position.z = 7 - (t / DURATION) * 1.5;
      cardGroup.rotation.y = Math.sin(t * 0.5) * 0.35;
      cardGroup.rotation.x = Math.sin(t * 0.35) * 0.08;
      cardGroup.position.y = Math.sin(t * 1.2) * 0.15;
      particles.rotation.z = t * 0.3;
      renderer.render(scene, camera);
    }

    window.addEventListener("hf-seek", (event) => {
      renderAt(event.detail.time);
    });

    renderAt(window.__hfThreeTime || 0);
  </script>
</body>
</html>
```

- [ ] **Step 5: Lint de la composición**

```bash
cd core/content_pipeline/hyperframes_reel && npx hyperframes lint && cd -
```

Expected: 0 errores. Si aparece `root_composition_missing_duration_source`, confirmar que `data-duration="6"` sigue en el `<div id="root">` (no se movió por error). Si aparece un error de resolución del import `three`, revisar que el Step 2 completó y la ruta del `importmap` coincide con el `ls` del Step 2.

- [ ] **Step 6: Render real con una foto de producto real de este repo**

```bash
cp .test-photos/gelatina_marba_1.jpg core/content_pipeline/hyperframes_reel/assets/tmp/test-photo-input.jpg
docker compose exec -T backend python -c "
from PIL import Image, ImageOps
img = Image.open('/app/core/content_pipeline/hyperframes_reel/assets/tmp/test-photo-input.jpg')
img = ImageOps.exif_transpose(img).convert('RGB')
side = min(img.width, img.height)
left, top = (img.width - side) // 2, (img.height - side) // 2
img.crop((left, top, left + side, top + side)).save('/app/core/content_pipeline/hyperframes_reel/assets/tmp/test-photo.png')
"
cd core/content_pipeline/hyperframes_reel
echo '{"photo_src":"assets/tmp/test-photo.png","primary_color":"#e94560","secondary_color":"#3ED694"}' > /tmp/vars.json
node_modules/.bin/hyperframes render . -c compositions/product-showcase.html -o /tmp/product-showcase-test.mp4 --variables-file /tmp/vars.json --fps 24
cd -
ffprobe -v error -show_entries format=duration -show_entries stream=width,height -of default=noprint_wrappers=1 /tmp/product-showcase-test.mp4
```

Expected: el render termina sin error, produce un archivo `.mp4` real, `ffprobe` reporta `width=1080`, `height=1920`, duración ≈6.0s.

- [ ] **Step 7: Punto de control obligatorio — NO avanzar a la Tarea 2 sin esto**

Enviar `/tmp/product-showcase-test.mp4` a Anuar (vía el mecanismo de envío de archivos disponible) para que lo juzgue visualmente. Si el resultado no convence (se ve "barato"/plano en vez de "wow"), este es el punto para iterar sobre la escena de Three.js (Step 4) — probar otra geometría/movimiento/lighting — y volver a renderizar (Step 6), NO para avanzar a la Tarea 2. El pipeline actual (Gemini+Veo) sigue intacto durante toda esta iteración — no hay nada que perder.

- [ ] **Step 8: Limpiar archivos de prueba, commit**

```bash
rm -f core/content_pipeline/hyperframes_reel/assets/tmp/test-photo-input.jpg core/content_pipeline/hyperframes_reel/assets/tmp/test-photo.png /tmp/vars.json /tmp/product-showcase-test.mp4
git add core/content_pipeline/hyperframes_reel/package.json core/content_pipeline/hyperframes_reel/package-lock.json \
  core/content_pipeline/hyperframes_reel/assets/tmp/placeholder.png core/content_pipeline/hyperframes_reel/compositions/product-showcase.html
git commit -m "feat(reels): agrega composicion HyperFrames/Three.js product-showcase (video-showcase 3D de producto real)"
```

---

### Task 2: `ProductShowcaseGenerator` — nuevo generador, sin tocar el pipeline viejo todavía

**Files:**
- Create: `core/content_pipeline/generators/product_showcase_generator.py`
- Create: `core/content_pipeline/tests/test_product_showcase_generator.py`

**Interfaces:**
- Consumes: `compositions/product-showcase.html` de la Tarea 1 (variables exactas: `photo_src`, `primary_color`, `secondary_color`); `enhance_photo_classic(image_bytes: bytes) -> bytes` de `core.content_pipeline.image_utils` (sin cambios); `record_hyperframes_generation(kind: str)` de `core.shared.metrics_utils` (sin cambios).
- Produces: clase `ProductShowcaseGenerator(bucket_name: str)` con método público `generate_reel(self, product_photo_bytes: bytes, filename_prefix: str, colors: list[str] = None) -> tuple[str, str, str]` (retorna `(video_url, poster_url, reason)`, mismo contrato de retorno que el `ProductReferenceGenerator.generate_reel` actual). La Tarea 3 importa y llama exactamente esta firma.

**IMPORTANTE**: este task NO toca `product_reference_generator.py`, `tasks.py`, ni sus tests — el archivo viejo se queda intacto y sigue siendo el que usa `tasks.py` hasta la Tarea 3. Al terminar este task, `core/content_pipeline/tests/test_product_reference_generator.py` sigue pasando sin cambios (verificarlo en el Step final).

- [ ] **Step 1: Escribir el archivo completo `product_showcase_generator.py`**

```python
import logging
import json
import os
import subprocess
import tempfile
import time
import uuid

from django.conf import settings
from google.cloud import storage, vision

from core.content_pipeline.image_utils import enhance_photo_classic
from core.shared.metrics import GCS_OPERATIONS
from core.shared.metrics_utils import track_external_api, record_hyperframes_generation

logger = logging.getLogger(__name__)

_HYPERFRAMES_PROJECT_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', 'hyperframes_reel',
))
_HYPERFRAMES_BINARY = os.path.join(_HYPERFRAMES_PROJECT_DIR, 'node_modules', '.bin', 'hyperframes')
_HYPERFRAMES_TIMEOUT_SECONDS = 120
_SHOWCASE_COMPOSITION = 'compositions/product-showcase.html'

_SCREENSHOT_LABELS = {'screenshot', 'user interface', 'software'}
_SCREENSHOT_LABEL_THRESHOLD = 0.5

_FALLBACK_PRIMARY_COLOR = '#e94560'
_FALLBACK_SECONDARY_COLOR = '#3ED694'

_REJECT_SCREENSHOT_MESSAGE = (
    'La foto que subiste parece ser una captura de pantalla (de una app o red social), '
    'no una foto directa del producto. Sube una foto tomada directamente del producto, '
    'no una captura de pantalla.'
)
_REJECT_UNSAFE_MESSAGE = 'El resultado fue rechazado por posible contenido sensible. Intenta con otra foto.'


class ProductShowcaseGenerator:
    """Pipeline solo-admin: toma una foto real de producto (sin regenerarla con IA) y la
    compone dentro de una plantilla 3D animada (HyperFrames/Three.js). Reemplaza el
    pipeline anterior (Gemini regenera la escena + Veo la anima) para eliminar
    alucinaciones y rechazos falsos-positivos de marca de agua (HALLAZGO IMG-13).
    Ver docs/superpowers/specs/2026-08-05-product-showcase-3d-pipeline-design.md."""

    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def _check_photo_safety(self, photo_bytes: bytes) -> str:
        try:
            client = vision.ImageAnnotatorClient(
                client_options={'quota_project_id': settings.GOOGLE_CLOUD_PROJECT},
            )
            image = vision.Image(content=photo_bytes)
            features = [
                vision.Feature(type_=vision.Feature.Type.SAFE_SEARCH_DETECTION),
                vision.Feature(type_=vision.Feature.Type.LABEL_DETECTION, max_results=10),
            ]
            request = vision.AnnotateImageRequest(image=image, features=features)
            with track_external_api('cloud_vision', operation='product_showcase_safety'):
                resp = client.annotate_image(request=request)
            ss = resp.safe_search_annotation
            unsafe_floor = vision.Likelihood.LIKELY
            if ss.adult >= unsafe_floor or ss.violence >= unsafe_floor or ss.racy >= unsafe_floor:
                logger.warning(f"ProductShowcaseGenerator: gate de seguridad rechazo (adult={ss.adult.name}, "
                                f"violence={ss.violence.name}, racy={ss.racy.name})")
                return _REJECT_UNSAFE_MESSAGE
            for label in resp.label_annotations:
                if label.description.lower() in _SCREENSHOT_LABELS and label.score >= _SCREENSHOT_LABEL_THRESHOLD:
                    logger.warning(f"ProductShowcaseGenerator: gate de screenshot rechazo (label={label.description}, "
                                    f"score={label.score:.2f})")
                    return _REJECT_SCREENSHOT_MESSAGE
            return ''
        except Exception as e:
            logger.warning(f"ProductShowcaseGenerator._check_photo_safety error (fail-open): {e}")
            return ''

    def _generate_showcase(self, enhanced_photo_bytes: bytes, primary_color: str, secondary_color: str) -> bytes | None:
        assets_tmp_dir = os.path.join(_HYPERFRAMES_PROJECT_DIR, 'assets', 'tmp')
        os.makedirs(assets_tmp_dir, exist_ok=True)
        photo_filename = f'{uuid.uuid4().hex}.png'
        photo_path = os.path.join(assets_tmp_dir, photo_filename)
        with open(photo_path, 'wb') as f:
            f.write(enhanced_photo_bytes)
        try:
            variables = {
                'photo_src': f'assets/tmp/{photo_filename}',
                'primary_color': primary_color,
                'secondary_color': secondary_color,
            }
            with tempfile.TemporaryDirectory() as tmp:
                vars_path = os.path.join(tmp, 'vars.json')
                with open(vars_path, 'w') as f:
                    json.dump(variables, f)
                output_path = os.path.join(tmp, 'output.mp4')
                try:
                    subprocess.run(
                        [_HYPERFRAMES_BINARY, 'render', '.', '-c', _SHOWCASE_COMPOSITION,
                         '-o', output_path, '--variables-file', vars_path, '--fps', '24', '--quiet'],
                        cwd=_HYPERFRAMES_PROJECT_DIR, check=True, capture_output=True,
                        timeout=_HYPERFRAMES_TIMEOUT_SECONDS,
                    )
                except Exception as e:
                    logger.warning(f"ProductShowcaseGenerator._generate_showcase fallo: {e}")
                    return None
                record_hyperframes_generation('product_showcase')
                with open(output_path, 'rb') as f:
                    return f.read()
        finally:
            try:
                os.remove(photo_path)
            except OSError:
                pass

    def _extract_frame(self, video_bytes: bytes, offset_seconds: float) -> bytes | None:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                video_path = os.path.join(tmp, 'video.mp4')
                with open(video_path, 'wb') as f:
                    f.write(video_bytes)
                frame_path = os.path.join(tmp, 'frame.png')
                subprocess.run(
                    ['ffmpeg', '-y', '-ss', str(offset_seconds), '-i', video_path, '-vframes', '1', frame_path],
                    check=True, capture_output=True,
                )
                with open(frame_path, 'rb') as f:
                    return f.read()
        except Exception as e:
            logger.warning(f"ProductShowcaseGenerator._extract_frame fallo en offset {offset_seconds}s: {e}")
            return None

    def _upload_to_storage(self, data: bytes, filename: str, content_type: str, folder: str) -> str:
        ext = 'mp4' if content_type == 'video/mp4' else 'png'
        with track_external_api('gcs'):
            client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
            bucket = client.bucket(self._bucket)
            blob = bucket.blob(f'{folder}/{filename}.{ext}')
            blob.upload_from_string(data, content_type=content_type)
        GCS_OPERATIONS.labels(operation='upload').inc()
        return f'{blob.public_url}?v={int(time.time())}'

    def generate_reel(self, product_photo_bytes: bytes, filename_prefix: str, colors: list[str] = None) -> tuple[str, str, str]:
        try:
            rejection = self._check_photo_safety(product_photo_bytes)
            if rejection:
                return '', '', rejection

            enhanced_bytes = enhance_photo_classic(product_photo_bytes)

            colors = colors or []
            primary_color = colors[0] if colors else _FALLBACK_PRIMARY_COLOR
            secondary_color = colors[1] if len(colors) > 1 else _FALLBACK_SECONDARY_COLOR

            video_bytes = self._generate_showcase(enhanced_bytes, primary_color, secondary_color)
            if video_bytes is None:
                video_bytes = self._generate_showcase(enhanced_bytes, primary_color, secondary_color)  # 1 reintento
            if video_bytes is None:
                return '', '', 'No se pudo generar el video. Vuelve a intentar.'

            poster_bytes = self._extract_frame(video_bytes, offset_seconds=1.0)
            poster_url = self._upload_to_storage(
                poster_bytes if poster_bytes else enhanced_bytes, f'{filename_prefix}-poster', 'image/png', 'product-samples',
            )
            video_url = self._upload_to_storage(video_bytes, filename_prefix, 'video/mp4', 'product-samples')
            return video_url, poster_url, ''
        except Exception as e:
            logger.warning(f"ProductShowcaseGenerator.generate_reel fallo: {e}")
            return '', '', 'Ocurrió un error inesperado generando el video. Vuelve a intentar.'
```

- [ ] **Step 2: Escribir los tests — gate de seguridad**

```python
import json
from unittest.mock import patch, MagicMock, call
import pytest
from django.test import override_settings
from google.cloud import vision


def _safe_search_response(adult='VERY_UNLIKELY', violence='VERY_UNLIKELY', racy='VERY_UNLIKELY', labels=None):
    likelihood = {
        'VERY_UNLIKELY': vision.Likelihood.VERY_UNLIKELY, 'UNLIKELY': vision.Likelihood.UNLIKELY,
        'POSSIBLE': vision.Likelihood.POSSIBLE, 'LIKELY': vision.Likelihood.LIKELY,
        'VERY_LIKELY': vision.Likelihood.VERY_LIKELY,
    }
    resp = MagicMock()
    resp.safe_search_annotation.adult = likelihood[adult]
    resp.safe_search_annotation.violence = likelihood[violence]
    resp.safe_search_annotation.racy = likelihood[racy]
    label_mocks = []
    for description, score in (labels or []):
        label_mock = MagicMock()
        label_mock.description = description
        label_mock.score = score
        label_mocks.append(label_mock)
    resp.label_annotations = label_mocks
    return resp


class TestCheckPhotoSafety:
    @override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic')
    def test_passes_clean_photo(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.vision.ImageAnnotatorClient') as mock_vc:
            mock_vc.return_value.annotate_image.return_value = _safe_search_response(
                labels=[('Food', 0.9), ('Ingredient', 0.8)],
            )
            result = gen._check_photo_safety(b'fake-photo-bytes')
        assert result == ''
        mock_vc.assert_called_once_with(client_options={'quota_project_id': 'agente-cosmic'})

    def test_rejects_screenshot_label(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.vision.ImageAnnotatorClient') as mock_vc:
            mock_vc.return_value.annotate_image.return_value = _safe_search_response(
                labels=[('Screenshot', 0.83), ('Text', 0.95)],
            )
            result = gen._check_photo_safety(b'fake-photo-bytes')
        assert 'captura de pantalla' in result.lower()

    def test_rejects_adult_content(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.vision.ImageAnnotatorClient') as mock_vc:
            mock_vc.return_value.annotate_image.return_value = _safe_search_response(adult='VERY_LIKELY')
            result = gen._check_photo_safety(b'fake-photo-bytes')
        assert 'sensible' in result.lower()

    def test_does_not_reject_heavy_text_overlay_without_screenshot_label(self):
        # Caso real de HALLAZGO IMG-13 (gelopaleta_stitch.jpg): mucho texto/graficos
        # superpuestos pero SIN la etiqueta Screenshot -- no debe rechazarse.
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.vision.ImageAnnotatorClient') as mock_vc:
            mock_vc.return_value.annotate_image.return_value = _safe_search_response(
                labels=[('Plastic', 0.59), ('Toy', 0.57), ('Party Supply', 0.57)],
            )
            result = gen._check_photo_safety(b'fake-photo-bytes')
        assert result == ''

    def test_fails_open_on_exception(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.vision.ImageAnnotatorClient',
                   side_effect=Exception('API error')):
            result = gen._check_photo_safety(b'fake-photo-bytes')
        assert result == ''
```

- [ ] **Step 3: Correr los tests del gate de seguridad**

```bash
docker compose exec -T backend pytest core/content_pipeline/tests/test_product_showcase_generator.py -v
```

Expected: los 5 tests de `TestCheckPhotoSafety` pasan.

- [ ] **Step 4: Escribir los tests de `_generate_showcase` (mismo patrón que `TestGenerateBrandedSegment` de `test_reel_generator.py`)**

Agregar al mismo archivo:

```python
import os


class TestGenerateShowcase:
    def test_builds_variables_and_renders(self, tmp_path):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        fake_output = b'fake-showcase-mp4'
        captured = {}

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[cmd.index('--variables-file') + 1]) as f:
                captured['variables'] = json.load(f)
            captured['cmd'] = cmd
            captured['cwd'] = kwargs.get('cwd')
            output_path = cmd[cmd.index('-o') + 1]
            with open(output_path, 'wb') as f:
                f.write(fake_output)

        with patch('core.content_pipeline.generators.product_showcase_generator.subprocess.run', side_effect=fake_run), \
             patch('core.content_pipeline.generators.product_showcase_generator.record_hyperframes_generation') as mock_record:
            result = gen._generate_showcase(b'fake-enhanced-photo', '#1a1a2e', '#3ED694')

        assert result == fake_output
        assert captured['variables']['primary_color'] == '#1a1a2e'
        assert captured['variables']['secondary_color'] == '#3ED694'
        assert captured['variables']['photo_src'].startswith('assets/tmp/')
        assert captured['cmd'][captured['cmd'].index('-c') + 1] == 'compositions/product-showcase.html'
        mock_record.assert_called_once_with('product_showcase')
        # El archivo temporal de la foto se limpia despues del render
        from core.content_pipeline.generators.product_showcase_generator import _HYPERFRAMES_PROJECT_DIR
        photo_filename = captured['variables']['photo_src'].split('/')[-1]
        assert not os.path.exists(os.path.join(_HYPERFRAMES_PROJECT_DIR, 'assets', 'tmp', photo_filename))

    def test_returns_none_on_subprocess_error(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_showcase_generator.subprocess.run',
                   side_effect=Exception('render failed')):
            result = gen._generate_showcase(b'fake-enhanced-photo', '#1a1a2e', '#3ED694')
        assert result is None
```

- [ ] **Step 5: Correr, verificar que pasan**

```bash
docker compose exec -T backend pytest core/content_pipeline/tests/test_product_showcase_generator.py -v
```

Expected: 7/7 tests pasan hasta aquí.

- [ ] **Step 6: Escribir los tests de `generate_reel()` (flujo completo, todo mockeado)**

```python
class TestGenerateReel:
    def test_rejects_via_safety_gate(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value='mensaje de rechazo'):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo', 'job1-sample')
        assert video_url == '' and poster_url == ''
        assert reason == 'mensaje de rechazo'

    def test_happy_path_uploads_video_and_poster(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_generate_showcase', return_value=b'video-bytes') as mock_showcase, \
             patch.object(gen, '_extract_frame', return_value=b'poster-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://poster.url', 'https://video.url']) as mock_upload:
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo', 'job1-sample', colors=['#111111', '#222222'])

        assert reason == ''
        assert poster_url == 'https://poster.url'
        assert video_url == 'https://video.url'
        mock_showcase.assert_called_once_with(b'enhanced', '#111111', '#222222')

    def test_retries_once_when_showcase_generation_fails(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_generate_showcase', side_effect=[None, b'video-bytes']) as mock_showcase, \
             patch.object(gen, '_extract_frame', return_value=b'poster-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://poster.url', 'https://video.url']):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo', 'job1-sample')
        assert reason == ''
        assert mock_showcase.call_count == 2

    def test_gives_up_after_retry_fails(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_generate_showcase', return_value=None) as mock_showcase:
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo', 'job1-sample')
        assert video_url == '' and poster_url == ''
        assert 'no se pudo generar' in reason.lower()
        assert mock_showcase.call_count == 2

    def test_uses_fallback_colors_when_none_provided(self):
        from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
        gen = ProductShowcaseGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_check_photo_safety', return_value=''), \
             patch('core.content_pipeline.generators.product_showcase_generator.enhance_photo_classic',
                   return_value=b'enhanced'), \
             patch.object(gen, '_generate_showcase', return_value=b'video-bytes') as mock_showcase, \
             patch.object(gen, '_extract_frame', return_value=b'poster-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://poster.url', 'https://video.url']):
            gen.generate_reel(b'fake-photo', 'job1-sample')
        mock_showcase.assert_called_once_with(b'enhanced', '#e94560', '#3ED694')
```

- [ ] **Step 7: Correr toda la suite del archivo nuevo**

```bash
docker compose exec -T backend pytest core/content_pipeline/tests/test_product_showcase_generator.py -v
```

Expected: 12/12 tests pasan.

- [ ] **Step 8: Verificar que el pipeline viejo sigue intacto (no se tocó nada de él)**

```bash
docker compose exec -T backend pytest core/content_pipeline/tests/test_product_reference_generator.py -v
```

Expected: todos los tests existentes siguen pasando exactamente igual que antes de este task (mismo conteo).

- [ ] **Step 9: Commit**

```bash
git add core/content_pipeline/generators/product_showcase_generator.py core/content_pipeline/tests/test_product_showcase_generator.py
git commit -m "feat(reels): agrega ProductShowcaseGenerator (foto real + template 3D, sin IA generativa)"
```

---

### Task 3: Wiring — `tasks.py`, `models.py`, template, borrar el pipeline viejo

**Files:**
- Modify: `core/content_pipeline/tasks.py:19,58-69`
- Modify: `core/brand_dna/models.py` (choices de `generation_mode`)
- Create: `core/brand_dna/migrations/0011_remove_sample_product_image_mode.py` (o el siguiente número disponible — correr `ls core/brand_dna/migrations/ | tail -1` para confirmar el último número antes de nombrar el archivo)
- Modify: `core/brand_dna/templates/brand_dna/new_analysis.html:120`
- Delete: `core/content_pipeline/generators/product_reference_generator.py`
- Delete: `core/content_pipeline/tests/test_product_reference_generator.py`

**Interfaces:**
- Consumes: `ProductShowcaseGenerator.generate_reel(product_photo_bytes: bytes, filename_prefix: str, colors: list[str] = None) -> tuple[str, str, str]` de la Tarea 2 (firma exacta).

- [ ] **Step 1: Confirmar el siguiente número de migración**

```bash
ls core/brand_dna/migrations/ | grep -E "^[0-9]" | sort | tail -3
```

Usar el siguiente número entero disponible para el nombre del archivo del Step 3 (este plan asume `0011`; ajustar si `ls` muestra un número mayor ya existente).

- [ ] **Step 2: Actualizar `core/brand_dna/models.py` — quitar `MODE_SAMPLE_PRODUCT_IMAGE`**

Buscar el bloque actual (aprox. líneas 30-40):

```python
    MODE_SAMPLE_PRODUCT_IMAGE = 'sample_product_image'
    MODE_SAMPLE_PRODUCT_REEL = 'sample_product_reel'
```

y su entrada en `MODE_CHOICES`:

```python
        (MODE_SAMPLE_PRODUCT_IMAGE, 'Muestra: imagen con producto real (solo admin)'),
        (MODE_SAMPLE_PRODUCT_REEL, 'Muestra: reel con producto real (solo admin)'),
```

Eliminar la línea de `MODE_SAMPLE_PRODUCT_IMAGE = 'sample_product_image'` y su entrada en `MODE_CHOICES`. Dejar `MODE_SAMPLE_PRODUCT_REEL` intacto.

- [ ] **Step 3: Generar y revisar la migración**

```bash
docker compose exec -T backend python manage.py makemigrations brand_dna
```

Expected: genera una migración `AlterField` sobre `generation_mode` (solo cambia `choices`, no hay `AlterField` de datos). Verificar el archivo generado no incluye ningún `RunPython`/`RemoveField` inesperado — si `makemigrations` genera algo distinto a un simple `AlterField` de `choices`, detenerse y revisar por qué antes de continuar.

- [ ] **Step 4: Actualizar `core/content_pipeline/tasks.py`**

Cambiar el import (línea 19):

```python
from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
```

por:

```python
from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
```

Reemplazar el cuerpo de `_generate_product_reference_sample` (líneas 51-69) por:

```python
def _generate_product_reference_sample(job, brand_dna) -> None:
    if not job.product_reference_image_path:
        job.mark_failed('Modo de producto real seleccionado pero no se subió ninguna foto.')
        return

    photo_bytes = read_upload(job.product_reference_image_path)
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    product_gen = ProductShowcaseGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

    video_url, poster_url, reason = product_gen.generate_reel(
        photo_bytes, filename_prefix=f"{job.id}-product-sample", colors=brand_dna.primary_colors,
    )

    if not video_url:
        calendar.delete()
        job.mark_failed(reason or 'El control de calidad rechazó el resultado. Reintenta.')
        return

    ContentPost.objects.create(
        calendar=calendar,
        day_number=1,
        caption='Prueba: producto real como referencia (solo admin)',
        image_url=poster_url,
        image_urls=[],
        video_url=video_url,
        format=ContentPost.FORMAT_REEL,
        suggested_time='09:00',
        hashtags=[],
        scheduled_at=timezone.now(),
    )

    job.stage = AnalysisJob.STAGE_COMPLETE
    job.progress = 100
    job.status = AnalysisJob.STATUS_DONE
    job.save(update_fields=['stage', 'progress', 'status'])
    logger.info(f"Muestra de producto real generada para job {job.id}")
```

Estas últimas 4 líneas (`job.status`, `job.save`, `logger.info`) ya existen en el archivo actual sin cambios — solo se muestran aquí completas para que el bloque reemplazado quede exacto.

Buscar también la otra referencia al modo eliminado, dentro de `generate_sample_task` (línea ~157):

```python
        if job.generation_mode in (AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE, AnalysisJob.MODE_SAMPLE_PRODUCT_REEL):
            _generate_product_reference_sample(job, brand_dna)
            return
```

Reemplazar por:

```python
        if job.generation_mode == AnalysisJob.MODE_SAMPLE_PRODUCT_REEL:
            _generate_product_reference_sample(job, brand_dna)
            return
```

- [ ] **Step 5: Actualizar `core/brand_dna/views.py`**

Buscar la línea (~154):

```python
    valid_modes = {
        AnalysisJob.MODE_FULL, AnalysisJob.MODE_SAMPLE_IMAGE, AnalysisJob.MODE_SAMPLE_REEL,
        AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE, AnalysisJob.MODE_SAMPLE_PRODUCT_REEL,
    }
```

y quitar `AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE,` de ese set.

- [ ] **Step 6: Actualizar la plantilla `new_analysis.html`**

Eliminar la línea 120:

```html
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_product_image" class="mode-product"> [ADMIN] Imagen con producto real
          </label>
```

Dejar intacto el radio de `sample_product_reel` (líneas 122-124).

- [ ] **Step 7: Correr la suite completa de `brand_dna` y `content_pipeline` para detectar cualquier referencia rota**

```bash
docker compose exec -T backend pytest core/brand_dna/ core/content_pipeline/ -v 2>&1 | tail -60
```

Expected: 0 fallos. Si algo referencia `MODE_SAMPLE_PRODUCT_IMAGE` o `ProductReferenceGenerator` y falla, es una referencia que este plan no anticipó — buscarla con `grep -rn "MODE_SAMPLE_PRODUCT_IMAGE\|ProductReferenceGenerator" --include="*.py" core/` y actualizarla antes de continuar (no se documenta aquí porque no debería existir tras los Steps 1-6, pero verificar es obligatorio).

- [ ] **Step 8: Borrar el pipeline viejo**

```bash
git rm core/content_pipeline/generators/product_reference_generator.py core/content_pipeline/tests/test_product_reference_generator.py
```

- [ ] **Step 9: Correr la suite completa del proyecto**

```bash
docker compose exec -T backend pytest -v 2>&1 | tail -30
```

Expected: todos los tests pasan (mismo conteo que antes de este plan, menos los tests borrados del archivo viejo, más los tests nuevos de la Tarea 2).

- [ ] **Step 10: Commit**

```bash
git add core/content_pipeline/tasks.py core/brand_dna/models.py core/brand_dna/views.py \
  core/brand_dna/migrations/ core/brand_dna/templates/brand_dna/new_analysis.html
git commit -m "feat(reels): conecta ProductShowcaseGenerator, elimina modo imagen-producto y el pipeline Gemini/Veo viejo"
```

---

## Verificación final end-to-end (manual, no delegable)

Con `ventas@anuarbarrera.dev` ya en el grupo `admin` (hecho en esta sesión): iniciar sesión en el entorno de desarrollo, correr un análisis real con modo "[ADMIN] Reel con producto real" y una foto real, confirmar que el video/poster suben a GCS y el `ContentPost` se crea con `format=FORMAT_REEL`. Revisar visualmente el resultado una vez más en el contexto real de la app (no solo el render aislado de la Tarea 1).
