# Agente Cosmic v2 Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Añadir tres features al pipeline de contenido: (1) texto del caption embebido en la imagen vía PIL, (2) scheduling inteligente por industria, (3) interfaz de revisión del calendario donde el usuario aprueba/edita/regenera cada post.

**Architecture:** La imagen se genera con Vertex AI y luego se le aplica un overlay de texto con PIL antes de subir a GCS. El scheduling usa una tabla de benchmarks de engagement por industria detectada del BrandDNA (sin llamadas externas). El calendar review es una nueva página Django con endpoints JSON para tres acciones por post (approve/edit/regenerate); la regeneración llama a Vertex Text para reescribir el caption con feedback del usuario.

**Tech Stack:** Django 5.2, Pillow 12.2.0, Vertex AI (genai SDK), Google Cloud Storage, RQ/django-rq, PostgreSQL, vanilla JS (fetch API)

---

## Mapa de archivos

| Archivo | Acción | Propósito |
|---------|--------|-----------|
| `core/content_pipeline/generators/image_generator.py` | Modificar | Añadir `_overlay_text()` con PIL |
| `core/content_pipeline/smart_scheduler.py` | Crear | Tabla de benchmarks + función `smart_schedule_dates()` |
| `core/content_pipeline/tasks.py` | Modificar | Usar `smart_schedule_dates()` en lugar de 7 AM fijo |
| `core/content_pipeline/models.py` | Modificar | Añadir `user_status` y `user_note` a `ContentPost` |
| `core/content_pipeline/migrations/0003_contentpost_feedback.py` | Crear | Migración para los nuevos campos |
| `core/brand_dna/views.py` | Modificar | Añadir `calendar_review_view` y `post_action_api` |
| `core/brand_dna/urls.py` | Modificar | Añadir rutas `/calendar/<job_id>/` y `/api/post/<post_id>/action/` |
| `core/brand_dna/templates/brand_dna/calendar_review.html` | Crear | UI de revisión con 7 cards de posts |
| `core/brand_dna/templates/brand_dna/dashboard.html` | Modificar | Añadir link "Revisar calendario" cuando job está done |
| `core/brand_dna/templates/brand_dna/results.html` | Modificar | Añadir link "Revisar calendario" al completarse |

---

## Contexto crítico para subagentes

### Modelo ContentPost (actual)
```python
# core/content_pipeline/models.py
class ContentPost(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    calendar = models.ForeignKey(ContentCalendar, on_delete=models.CASCADE, related_name='posts')
    day_number = models.IntegerField()
    caption = models.TextField()
    image_url = models.URLField(max_length=1000, blank=True, default='')
    suggested_time = models.TimeField()
    hashtags = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    scheduled_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
```

### ImageGenerator.generate() (actual)
```python
def generate(self, caption: str, colors: list[str], tone: str, filename: str) -> str:
    prompt = self._build_prompt(caption, colors, tone)
    image_bytes = self._generate_with_retry(prompt)
    return self._upload_to_storage(image_bytes, filename)
```
`_upload_to_storage` sube a GCS y retorna la URL pública.

### Commit style
```bash
GIT_EDITOR=true git add <files> && GIT_EDITOR=true git commit -m "tipo: descripción"
```
Nunca usar heredoc — se cuelga en este entorno.

### Restart requerido
Cambios en `.py` requieren: `docker compose restart backend rqworker`
Templates `.html` no requieren restart.

---

## Task 1: PIL text overlay en imágenes generadas

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py`
- Test: `core/content_pipeline/tests/test_image_generator.py`

El objetivo es que cada imagen generada lleve el caption embebido como texto en una barra semi-transparente en la parte inferior. Esto convierte la imagen en un post listo para publicar sin edición adicional.

- [ ] **Step 1: Escribir el test**

```python
# core/content_pipeline/tests/test_image_generator.py
# Añadir al final del archivo existente (no reemplazar los tests que ya hay)

from unittest.mock import patch, MagicMock
from PIL import Image
import io

class TestOverlayText:
    def test_overlay_produces_valid_png(self):
        """_overlay_text debe devolver bytes PNG válidos con las dimensiones originales."""
        gen = ImageGenerator(bucket_name='test-bucket')
        # Crear imagen de prueba 1024x1024 azul
        img = Image.new('RGB', (1024, 1024), color=(30, 30, 60))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        original_bytes = buf.getvalue()

        result = gen._overlay_text(original_bytes, "Este es un caption de prueba para redes sociales")

        out = Image.open(io.BytesIO(result))
        assert out.size == (1024, 1024)
        assert result != original_bytes  # debe haber cambiado

    def test_overlay_handles_long_caption(self):
        """Captions largos deben truncarse/envolverse sin crash."""
        gen = ImageGenerator(bucket_name='test-bucket')
        img = Image.new('RGB', (1024, 1024), color=(30, 30, 60))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        long_caption = "A" * 300  # caption muy largo

        result = gen._overlay_text(buf.getvalue(), long_caption)
        assert len(result) > 0
```

- [ ] **Step 2: Verificar que el test falla**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_image_generator.py::TestOverlayText -v 2>&1 | tail -20
```
Esperado: FAIL con `AttributeError: 'ImageGenerator' object has no attribute '_overlay_text'`

- [ ] **Step 3: Implementar `_overlay_text` en `ImageGenerator`**

Reemplazar el contenido completo de `core/content_pipeline/generators/image_generator.py`:

```python
import io
import logging
import textwrap
import time

import google.genai as genai
from google.cloud import storage
from google.genai import types
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = [10, 20, 40]


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


class ImageGenerator:
    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def generate(self, caption: str, colors: list[str], tone: str, filename: str) -> str:
        try:
            prompt = self._build_prompt(caption, colors, tone)
            image_bytes = self._generate_with_retry(prompt)
            image_bytes = self._overlay_text(image_bytes, caption)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''

    def _build_prompt(self, caption: str, colors: list[str], tone: str) -> str:
        color_str = ', '.join(colors[:3]) if colors else 'modern vibrant colors'
        return (
            f"Professional social media post image. Concept: {caption[:120]}. "
            f"Use brand colors: {color_str}. Visual style: {tone}, clean, "
            f"high quality, square format 1:1, photographic or illustrated."
        )

    def _overlay_text(self, image_bytes: bytes, caption: str) -> bytes:
        img = Image.open(io.BytesIO(image_bytes)).convert('RGBA')
        w, h = img.size

        # Barra semi-transparente en la parte inferior (25% de la altura)
        bar_h = int(h * 0.25)
        overlay = Image.new('RGBA', (w, bar_h), (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)
        for y in range(bar_h):
            alpha = int(180 * (y / bar_h))
            draw_overlay.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

        img.paste(overlay, (0, h - bar_h), overlay)

        # Texto del caption
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=max(20, w // 32))
        except TypeError:
            font = ImageFont.load_default()

        padding = w // 20
        max_chars = max(20, w // (max(20, w // 32) // 2))
        lines = textwrap.wrap(caption[:240], width=max_chars)[:4]
        text = '\n'.join(lines)

        text_y = h - bar_h + padding
        # Sombra
        draw.text((padding + 2, text_y + 2), text, font=font, fill=(0, 0, 0, 200))
        # Texto blanco
        draw.text((padding, text_y), text, font=font, fill=(255, 255, 255, 255))

        out = io.BytesIO()
        img.convert('RGB').save(out, format='PNG', optimize=True)
        return out.getvalue()

    def _generate_with_retry(self, prompt: str) -> bytes:
        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                return self._generate_with_vertex(prompt)
            except Exception as e:
                last_error = e
                if '429' in str(e) and attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(f"Rate limit en imagen, reintento {attempt + 1} en {delay}s")
                    time.sleep(delay)
                else:
                    raise
        raise last_error

    def _generate_with_vertex(self, prompt: str) -> bytes:
        client = _vertex_client()
        resp = client.models.generate_content(
            model=settings.VERTEX_IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=['IMAGE', 'TEXT']
            ),
        )
        for part in resp.candidates[0].content.parts:
            if part.inline_data:
                return part.inline_data.data
        raise ValueError("No image returned by Vertex AI")

    def _upload_to_storage(self, image_bytes: bytes, filename: str) -> str:
        client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        bucket = client.bucket(self._bucket)
        blob = bucket.blob(f'posts/{filename}.png')
        blob.upload_from_string(image_bytes, content_type='image/png')
        blob.make_public()
        return blob.public_url
```

- [ ] **Step 4: Correr los tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_image_generator.py::TestOverlayText -v 2>&1 | tail -20
```
Esperado: 2 tests PASS

- [ ] **Step 5: Reiniciar y commit**

```bash
docker compose restart backend rqworker
GIT_EDITOR=true git add core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_image_generator.py && GIT_EDITOR=true git commit -m "feat: add PIL text overlay on generated images"
```

---

## Task 2: Smart scheduling por industria

**Files:**
- Create: `core/content_pipeline/smart_scheduler.py`
- Modify: `core/content_pipeline/tasks.py` (líneas 34-44)
- Test: `core/content_pipeline/tests/test_smart_scheduler.py`

El objetivo es que los 7 posts se programen en días y horas óptimas según el giro del negocio (detectado de `brand_dna.tone` y `brand_dna.description`), en lugar de siempre mandar a las 7 AM sin importar el día.

- [ ] **Step 1: Escribir los tests**

Crear `core/content_pipeline/tests/test_smart_scheduler.py`:

```python
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock
from core.content_pipeline.smart_scheduler import detect_industry, smart_schedule_dates


class TestDetectIndustry:
    def test_restaurant_keywords(self):
        dna = MagicMock()
        dna.tone = 'casual'
        dna.description = 'Restaurante de comida italiana con pastas artesanales'
        assert detect_industry(dna) == 'food'

    def test_fitness_keywords(self):
        dna = MagicMock()
        dna.tone = 'motivacional'
        dna.description = 'Gimnasio y entrenamiento personal para atletas'
        assert detect_industry(dna) == 'fitness'

    def test_retail_keywords(self):
        dna = MagicMock()
        dna.tone = 'elegante'
        dna.description = 'Tienda de ropa y accesorios de moda para mujer'
        assert detect_industry(dna) == 'retail'

    def test_default_fallback(self):
        dna = MagicMock()
        dna.tone = 'profesional'
        dna.description = 'Empresa de servicios generales'
        assert detect_industry(dna) == 'default'


class TestSmartScheduleDates:
    def test_returns_7_datetimes(self):
        dna = MagicMock()
        dna.tone = 'profesional'
        dna.description = 'Empresa de software'
        base = date(2026, 6, 2)  # lunes
        result = smart_schedule_dates(dna, base_date=base, count=7)
        assert len(result) == 7

    def test_first_slot_is_today(self):
        dna = MagicMock()
        dna.tone = 'casual'
        dna.description = 'Empresa de software'
        base = date(2026, 6, 2)
        result = smart_schedule_dates(dna, base_date=base, count=7)
        assert result[0].date() == base

    def test_no_duplicate_dates(self):
        dna = MagicMock()
        dna.tone = 'casual'
        dna.description = 'Empresa de software'
        base = date(2026, 6, 2)
        result = smart_schedule_dates(dna, base_date=base, count=7)
        dates_only = [r.date() for r in result]
        assert len(dates_only) == len(set(dates_only))
```

- [ ] **Step 2: Verificar que los tests fallan**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_smart_scheduler.py -v 2>&1 | tail -10
```
Esperado: ERROR / ModuleNotFoundError

- [ ] **Step 3: Crear `core/content_pipeline/smart_scheduler.py`**

```python
from datetime import datetime, timedelta, timezone as dt_timezone, date
from core.brand_dna.models import BrandDNA

MEXICO_TZ = dt_timezone(timedelta(hours=-6))

# Benchmarks de engagement por industria
# Cada entrada: lista de (weekday, hour) en orden de prioridad
# weekday: 0=lunes, 6=domingo
_INDUSTRY_SCHEDULE = {
    'food': [
        (4, 11),   # viernes 11am (almuerzo)
        (5, 11),   # sábado 11am
        (3, 18),   # jueves 6pm (cena anticipada)
        (0, 12),   # lunes 12pm
        (2, 11),   # miércoles 11am
        (1, 19),   # martes 7pm
        (6, 10),   # domingo 10am
    ],
    'fitness': [
        (0, 6),    # lunes 6am (inicio de semana)
        (2, 6),    # miércoles 6am
        (4, 6),    # viernes 6am
        (6, 9),    # domingo 9am
        (1, 7),    # martes 7am
        (3, 6),    # jueves 6am
        (5, 9),    # sábado 9am
    ],
    'retail': [
        (5, 10),   # sábado 10am
        (4, 15),   # viernes 3pm
        (3, 12),   # jueves 12pm
        (2, 11),   # miércoles 11am
        (0, 10),   # lunes 10am
        (6, 11),   # domingo 11am
        (1, 12),   # martes 12pm
    ],
    'beauty': [
        (4, 10),   # viernes 10am
        (5, 10),   # sábado 10am
        (2, 11),   # miércoles 11am
        (1, 10),   # martes 10am
        (0, 9),    # lunes 9am
        (3, 10),   # jueves 10am
        (6, 11),   # domingo 11am
    ],
    'tech': [
        (1, 9),    # martes 9am
        (2, 9),    # miércoles 9am
        (3, 10),   # jueves 10am
        (0, 10),   # lunes 10am
        (4, 9),    # viernes 9am
        (1, 14),   # martes 2pm
        (2, 14),   # miércoles 2pm
    ],
    'default': [
        (1, 9),    # martes 9am
        (3, 9),    # jueves 9am
        (2, 10),   # miércoles 10am
        (0, 9),    # lunes 9am
        (4, 9),    # viernes 9am
        (5, 10),   # sábado 10am
        (2, 15),   # miércoles 3pm
    ],
}

_INDUSTRY_KEYWORDS = {
    'food': ['restaurant', 'restaurante', 'comida', 'food', 'cocina', 'chef', 'menu',
             'cafeteria', 'cafe', 'café', 'pizz', 'taco', 'sushi', 'bakery', 'panaderia'],
    'fitness': ['gym', 'gimnasio', 'fitness', 'entrenamiento', 'workout', 'crossfit',
                'yoga', 'pilates', 'deporte', 'atleta', 'nutricion'],
    'retail': ['tienda', 'store', 'ropa', 'moda', 'fashion', 'boutique', 'accesorios',
               'calzado', 'zapatos', 'joyeria', 'retail', 'shop'],
    'beauty': ['salon', 'salón', 'spa', 'belleza', 'beauty', 'peluqueria', 'estetica',
               'cosmetica', 'makeup', 'skincare', 'nail'],
    'tech': ['software', 'tecnologia', 'tech', 'digital', 'app', 'desarrollo', 'startup',
             'saas', 'programacion', 'web', 'ia', 'ai'],
}


def detect_industry(brand_dna) -> str:
    text = f"{brand_dna.tone} {brand_dna.description}".lower()
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return industry
    return 'default'


def smart_schedule_dates(brand_dna, base_date: date, count: int = 7) -> list[datetime]:
    industry = detect_industry(brand_dna)
    slots = _INDUSTRY_SCHEDULE[industry]

    result = []
    current = base_date

    # Día 1 siempre es hoy (se envía de inmediato)
    hour = slots[0][1]
    result.append(datetime(current.year, current.month, current.day, hour, 0, 0, tzinfo=MEXICO_TZ))

    # Días 2-7: siguiente día con slot óptimo para ese día de la semana
    slot_idx = 1
    days_ahead = 1
    while len(result) < count:
        candidate = base_date + timedelta(days=days_ahead)
        candidate_weekday = candidate.weekday()
        # Buscar el slot que corresponde a este día de la semana
        for i, (weekday, hour) in enumerate(slots[slot_idx:], start=slot_idx):
            if weekday == candidate_weekday:
                result.append(
                    datetime(candidate.year, candidate.month, candidate.day, hour, 0, 0, tzinfo=MEXICO_TZ)
                )
                slot_idx = i + 1
                break
        days_ahead += 1
        if days_ahead > 30:  # fallback anti-bucle infinito
            # Completar con días consecutivos a las 9am
            while len(result) < count:
                d = base_date + timedelta(days=len(result))
                result.append(datetime(d.year, d.month, d.day, 9, 0, 0, tzinfo=MEXICO_TZ))
            break

    return result[:count]
```

- [ ] **Step 4: Correr los tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_smart_scheduler.py -v 2>&1 | tail -20
```
Esperado: 7 tests PASS

- [ ] **Step 5: Integrar `smart_schedule_dates` en `tasks.py`**

En `core/content_pipeline/tasks.py`, reemplazar el bloque de scheduling dentro del loop `for i, post_data in enumerate(posts_data, start=1)`:

```python
# Al inicio del archivo, añadir el import:
from core.content_pipeline.smart_scheduler import smart_schedule_dates

# Dentro de content_generation_task, ANTES del loop, añadir:
        mexico_today = now.astimezone(MEXICO_TZ).date()
        scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

# Reemplazar el loop completo (líneas 34-61 actuales) con:
        for i, post_data in enumerate(posts_data, start=1):
            hour, minute = map(int, post_data.get('suggested_time', '19:00').split(':'))
            scheduled = scheduled_dates[i - 1]

            if i == 1:
                image_url = image_gen.generate(
                    caption=post_data['caption'],
                    colors=brand_dna.primary_colors,
                    tone=brand_dna.tone,
                    filename=f"{job_id}-day{i}",
                )
            else:
                image_url = ''

            ContentPost.objects.create(
                calendar=calendar,
                day_number=i,
                caption=post_data['caption'],
                image_url=image_url,
                suggested_time=f"{hour:02d}:{minute:02d}",
                hashtags=post_data.get('hashtags', []),
                scheduled_at=scheduled,
            )
```

El archivo `tasks.py` completo después de los cambios:

```python
import logging
from datetime import datetime as dt_datetime, timedelta, timezone as dt_timezone
from django.conf import settings
from django.utils import timezone

MEXICO_TZ = dt_timezone(timedelta(hours=-6))
from core.brand_dna.models import AnalysisJob
from core.content_pipeline.models import ContentCalendar, ContentPost
from core.content_pipeline.generators.text_generator import TextGenerator
from core.content_pipeline.generators.image_generator import ImageGenerator
from core.content_pipeline.email_sender import EmailSender
from core.content_pipeline.scheduler import schedule_daily_emails
from core.content_pipeline.smart_scheduler import smart_schedule_dates

logger = logging.getLogger(__name__)


def content_generation_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    brand_dna = job.brand_dna

    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()
        scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

        for i, post_data in enumerate(posts_data, start=1):
            hour, minute = map(int, post_data.get('suggested_time', '19:00').split(':'))
            scheduled = scheduled_dates[i - 1]

            if i == 1:
                image_url = image_gen.generate(
                    caption=post_data['caption'],
                    colors=brand_dna.primary_colors,
                    tone=brand_dna.tone,
                    filename=f"{job_id}-day{i}",
                )
            else:
                image_url = ''

            ContentPost.objects.create(
                calendar=calendar,
                day_number=i,
                caption=post_data['caption'],
                image_url=image_url,
                suggested_time=f"{hour:02d}:{minute:02d}",
                hashtags=post_data.get('hashtags', []),
                scheduled_at=scheduled,
            )

        job.update_progress(AnalysisJob.STAGE_CONTENT, 95)

        try:
            EmailSender().send_initial(job=job, brand_dna=brand_dna, calendar=calendar)
            schedule_daily_emails(calendar)
        except Exception as email_err:
            logger.error(f"Email falló para job {job_id} (no fatal): {email_err}")

        job.stage = AnalysisJob.STAGE_COMPLETE
        job.progress = 100
        job.status = AnalysisJob.STATUS_DONE
        job.save(update_fields=['stage', 'progress', 'status'])
        logger.info(f"Job {job_id} completado exitosamente")

    except Exception as e:
        logger.error(f"content_generation_task error para job {job_id}: {e}")
        job.mark_failed(str(e))


def send_daily_email_task(post_id: str) -> None:
    post = ContentPost.objects.select_related('calendar__brand_dna__job').get(id=post_id)
    if not post.image_url:
        brand_dna = post.calendar.brand_dna
        job_id = str(brand_dna.job.id)
        try:
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            post.image_url = image_gen.generate(
                caption=post.caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day{post.day_number}",
            )
            post.save(update_fields=['image_url'])
        except Exception as img_err:
            logger.warning(f"Imagen día {post.day_number} falló (no fatal): {img_err}")
    EmailSender().send_daily(post=post)
```

- [ ] **Step 6: Correr tests de smart scheduler otra vez para confirmar**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_smart_scheduler.py -v 2>&1 | tail -10
```
Esperado: 7 PASS

- [ ] **Step 7: Reiniciar y commit**

```bash
docker compose restart backend rqworker
GIT_EDITOR=true git add core/content_pipeline/smart_scheduler.py core/content_pipeline/tasks.py core/content_pipeline/tests/test_smart_scheduler.py && GIT_EDITOR=true git commit -m "feat: smart scheduling based on industry benchmarks"
```

---

## Task 3: Modelo ContentPost — campos de feedback

**Files:**
- Modify: `core/content_pipeline/models.py`
- Create: `core/content_pipeline/migrations/0003_contentpost_feedback.py`
- Test: (verificación vía shell Django)

Añadir `user_status` y `user_note` a `ContentPost` para que el usuario pueda marcar cada post.

- [ ] **Step 1: Añadir campos al modelo**

En `core/content_pipeline/models.py`, dentro de la clase `ContentPost`, añadir después de `sent_at`:

```python
    # Feedback del usuario desde la interfaz de revisión
    USER_STATUS_PENDING = 'pending'
    USER_STATUS_APPROVED = 'approved'
    USER_STATUS_EDITED = 'edited'
    USER_STATUS_CHANGE_REQUESTED = 'change_requested'
    USER_STATUS_CHOICES = [
        ('pending', 'Pendiente revisión'),
        ('approved', 'Aprobado'),
        ('edited', 'Editado por usuario'),
        ('change_requested', 'Cambio solicitado'),
    ]
    user_status = models.CharField(
        max_length=20, choices=USER_STATUS_CHOICES, default='pending'
    )
    user_note = models.TextField(blank=True, default='')
```

El modelo completo después del cambio:

```python
import uuid
from django.db import models
from core.brand_dna.models import BrandDNA


class ContentCalendar(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand_dna = models.OneToOneField(BrandDNA, on_delete=models.CASCADE, related_name='calendar')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'content_pipeline_calendar'

    def __str__(self):
        return f"Calendar — {self.brand_dna.business_name}"


class ContentPost(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendiente'),
        (STATUS_SENT, 'Enviado'),
        (STATUS_FAILED, 'Fallido'),
    ]
    USER_STATUS_PENDING = 'pending'
    USER_STATUS_APPROVED = 'approved'
    USER_STATUS_EDITED = 'edited'
    USER_STATUS_CHANGE_REQUESTED = 'change_requested'
    USER_STATUS_CHOICES = [
        ('pending', 'Pendiente revisión'),
        ('approved', 'Aprobado'),
        ('edited', 'Editado por usuario'),
        ('change_requested', 'Cambio solicitado'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    calendar = models.ForeignKey(ContentCalendar, on_delete=models.CASCADE, related_name='posts')
    day_number = models.IntegerField()
    caption = models.TextField()
    image_url = models.URLField(max_length=1000, blank=True, default='')
    suggested_time = models.TimeField()
    hashtags = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    scheduled_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    user_status = models.CharField(
        max_length=20, choices=USER_STATUS_CHOICES, default='pending'
    )
    user_note = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'content_pipeline_post'
        ordering = ['day_number']

    def __str__(self):
        return f"Día {self.day_number} — {self.calendar.brand_dna.business_name}"
```

- [ ] **Step 2: Crear la migración**

```bash
docker exec agente-cosmic-backend-1 python manage.py makemigrations content_pipeline --name add_user_feedback_to_post 2>&1
```
Esperado: `Migrations for 'content_pipeline': core/content_pipeline/migrations/0003_contentpost_add_user_feedback_to_post.py`

- [ ] **Step 3: Aplicar la migración**

```bash
docker exec agente-cosmic-backend-1 python manage.py migrate content_pipeline 2>&1
```
Esperado: `OK`

- [ ] **Step 4: Verificar en Django shell**

```bash
docker exec agente-cosmic-backend-1 python manage.py shell -c "
from core.content_pipeline.models import ContentPost
p = ContentPost.objects.first()
if p:
    print('user_status:', p.user_status)
    print('user_note:', repr(p.user_note))
else:
    print('No hay posts, OK — campos creados correctamente')
"
```
Esperado: imprime campos sin error

- [ ] **Step 5: Commit**

```bash
GIT_EDITOR=true git add core/content_pipeline/models.py core/content_pipeline/migrations/ && GIT_EDITOR=true git commit -m "feat: add user_status and user_note fields to ContentPost"
```

---

## Task 4: Vista de revisión del calendario

**Files:**
- Modify: `core/brand_dna/views.py` — añadir `calendar_review_view` y `post_action_api`
- Modify: `core/brand_dna/urls.py` — nuevas rutas
- Create: `core/brand_dna/templates/brand_dna/calendar_review.html`

Esta vista muestra los 7 posts de un job en formato card, con botones de acción por post.

- [ ] **Step 1: Añadir vistas en `views.py`**

Añadir al final de `core/brand_dna/views.py` (no reemplazar el archivo):

```python
import json
import re
import google.genai as genai
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

@login_required
def calendar_review_view(request, job_id):
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    calendar = getattr(brand_dna, 'calendar', None) if brand_dna else None
    posts = list(calendar.posts.order_by('day_number')) if calendar else []
    return render(request, 'brand_dna/calendar_review.html', {
        'job': job,
        'brand_dna': brand_dna,
        'posts': posts,
    })


@login_required
@require_POST
def post_action_api(request, post_id):
    from core.content_pipeline.models import ContentPost
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    action = data.get('action')
    value = data.get('value', '').strip()

    post = get_object_or_404(
        ContentPost.objects.select_related('calendar__brand_dna'),
        id=post_id,
        calendar__brand_dna__job__user=request.user,
    )

    if action == 'approve':
        post.user_status = ContentPost.USER_STATUS_APPROVED
        post.save(update_fields=['user_status'])
        return JsonResponse({'status': 'ok'})

    if action == 'edit':
        if not value:
            return JsonResponse({'error': 'Caption vacío'}, status=400)
        post.caption = value
        post.user_status = ContentPost.USER_STATUS_EDITED
        post.save(update_fields=['caption', 'user_status'])
        return JsonResponse({'status': 'ok', 'caption': post.caption})

    if action == 'regenerate':
        if not value:
            return JsonResponse({'error': 'Feedback vacío'}, status=400)
        new_caption = _regenerate_caption(post, value)
        post.caption = new_caption
        post.user_note = value
        post.user_status = ContentPost.USER_STATUS_CHANGE_REQUESTED
        post.save(update_fields=['caption', 'user_note', 'user_status'])
        return JsonResponse({'status': 'ok', 'caption': new_caption})

    return JsonResponse({'error': 'Acción desconocida'}, status=400)


def _regenerate_caption(post, feedback: str) -> str:
    brand_dna = post.calendar.brand_dna
    prompt = (
        f"Eres un experto en marketing de contenidos. Reescribe el siguiente post de redes sociales "
        f"para la marca '{brand_dna.business_name}' considerando el feedback del cliente.\n\n"
        f"Post original:\n{post.caption}\n\n"
        f"Feedback del cliente: {feedback}\n\n"
        f"Tono de la marca: {brand_dna.tone}\n"
        f"Audiencia: {brand_dna.audience}\n\n"
        f"Responde ÚNICAMENTE con el nuevo texto del post, sin comillas, sin explicaciones. "
        f"Máximo {brand_dna.avg_caption_length} caracteres."
    )
    try:
        client = genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
        resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
        new_caption = resp.text.strip().strip('"').strip("'")
        raw = re.sub(r'^```.*?\n', '', new_caption, flags=re.DOTALL)
        raw = re.sub(r'\n?```$', '', raw)
        return raw.strip() or post.caption
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Caption regeneration error: {e}")
        return post.caption
```

- [ ] **Step 2: Añadir las URLs**

En `core/brand_dna/urls.py`, añadir al final del `urlpatterns`:

```python
    path('calendar/<uuid:job_id>/', views.calendar_review_view, name='calendar_review'),
    path('api/post/<uuid:post_id>/action/', views.post_action_api, name='post_action_api'),
```

El archivo completo después del cambio:

```python
from django.urls import path
from . import views, auth_views

urlpatterns = [
    path('', views.landing, name='landing'),
    path('analizar/', views.analyze_submit, name='analyze_submit'),
    path('resultados/<uuid:job_id>/', views.results, name='results'),
    path('api/brand-dna/status/<uuid:job_id>/', views.status_api, name='status_api'),

    # Auth
    path('auth/login/', auth_views.login_view, name='login'),
    path('auth/register/', auth_views.register_view, name='register'),
    path('auth/logout/', auth_views.logout_view, name='logout'),
    path('auth/google/', auth_views.google_login_view, name='google_login'),
    path('auth/google/callback/', auth_views.google_callback_view, name='google_callback'),
    path('dashboard/', auth_views.dashboard_view, name='dashboard'),

    # Calendar review
    path('calendar/<uuid:job_id>/', views.calendar_review_view, name='calendar_review'),
    path('api/post/<uuid:post_id>/action/', views.post_action_api, name='post_action_api'),
]
```

- [ ] **Step 3: Reiniciar backend**

```bash
docker compose restart backend
```

- [ ] **Step 4: Verificar que las rutas responden**

```bash
# Debe dar 302 redirect a login (no 404 ni 500)
curl -s -o /dev/null -w "%{http_code}" http://localhost:3002/calendar/00000000-0000-0000-0000-000000000000/
```
Esperado: `302`

- [ ] **Step 5: Commit (sin template aún)**

```bash
GIT_EDITOR=true git add core/brand_dna/views.py core/brand_dna/urls.py && GIT_EDITOR=true git commit -m "feat: add calendar_review_view and post_action_api endpoints"
```

---

## Task 5: Template de revisión del calendario

**Files:**
- Create: `core/brand_dna/templates/brand_dna/calendar_review.html`
- Modify: `core/brand_dna/templates/brand_dna/dashboard.html` — link "Revisar calendario"
- Modify: `core/brand_dna/templates/brand_dna/results.html` — link al completarse

- [ ] **Step 1: Crear el template**

Crear `core/brand_dna/templates/brand_dna/calendar_review.html`:

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Revisar Calendario — Agente Cosmic</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #0d0d1a; color: #f0f0f0; min-height: 100vh; padding-top: 64px; }
    nav {
      position: fixed; top: 0; left: 0; right: 0; z-index: 100;
      background: #1a1a2e; border-bottom: 1px solid #2a2a4a;
      padding: 0 24px; height: 64px;
      display: flex; align-items: center; justify-content: space-between;
    }
    .nav-brand { font-size: 1.2rem; font-weight: 700; color: #e94560; }
    .nav-actions { display: flex; gap: 12px; align-items: center; }
    .nav-btn { padding: 7px 16px; border-radius: 8px; font-size: 0.85rem; font-weight: 600; text-decoration: none; border: none; cursor: pointer; transition: background 0.2s; }
    .nav-btn-ghost { background: transparent; color: #aaa; border: 1px solid #333; }
    .nav-btn-ghost:hover { background: #2a2a4a; color: #f0f0f0; }
    .nav-btn-primary { background: #e94560; color: #fff; }
    .nav-btn-primary:hover { background: #c73652; }
    .container { max-width: 900px; margin: 0 auto; padding: 40px 20px 80px; }
    h1 { font-size: 1.4rem; margin-bottom: 4px; }
    .subtitle { color: #777; font-size: 0.9rem; margin-bottom: 32px; }
    .legend { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
    .legend-item { display: flex; align-items: center; gap: 6px; font-size: 0.8rem; color: #888; }
    .dot { width: 10px; height: 10px; border-radius: 50%; }
    .dot-pending { background: #555; }
    .dot-approved { background: #4caf7d; }
    .dot-edited { background: #4a9eff; }
    .dot-change { background: #f0c040; }
    .posts-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 20px; }
    .post-card { background: #1a1a2e; border-radius: 14px; overflow: hidden; transition: box-shadow 0.2s; }
    .post-card.approved { box-shadow: 0 0 0 2px #4caf7d; }
    .post-card.edited { box-shadow: 0 0 0 2px #4a9eff; }
    .post-card.change_requested { box-shadow: 0 0 0 2px #f0c040; }
    .post-img { width: 100%; aspect-ratio: 1; object-fit: cover; background: #111; display: flex; align-items: center; justify-content: center; color: #333; font-size: 2rem; }
    .post-img img { width: 100%; height: 100%; object-fit: cover; }
    .post-body { padding: 16px; }
    .post-day { font-size: 0.78rem; color: #e94560; font-weight: 700; margin-bottom: 4px; }
    .post-schedule { font-size: 0.75rem; color: #555; margin-bottom: 10px; }
    .post-caption { font-size: 0.85rem; color: #ccc; line-height: 1.5; margin-bottom: 12px; }
    .post-caption[contenteditable="true"] {
      border: 1px solid #e94560; border-radius: 6px; padding: 6px 8px;
      outline: none; background: #0d0d1a; min-height: 60px;
    }
    .post-hashtags { font-size: 0.75rem; color: #666; margin-bottom: 12px; }
    .post-status { font-size: 0.75rem; font-weight: 600; margin-bottom: 10px; }
    .status-pending { color: #666; }
    .status-approved { color: #4caf7d; }
    .status-edited { color: #4a9eff; }
    .status-change_requested { color: #f0c040; }
    .actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .btn-action {
      flex: 1; min-width: 70px; padding: 7px 8px; border: none; border-radius: 8px;
      font-size: 0.78rem; font-weight: 600; cursor: pointer; transition: opacity 0.2s;
    }
    .btn-approve { background: #1a3a2a; color: #4caf7d; }
    .btn-approve:hover { background: #2a4a3a; }
    .btn-edit { background: #1a2a3a; color: #4a9eff; }
    .btn-edit:hover { background: #2a3a4a; }
    .btn-regen { background: #3a3a1a; color: #f0c040; }
    .btn-regen:hover { background: #4a4a2a; }
    .btn-save { background: #e94560; color: #fff; display: none; }
    .btn-save:hover { background: #c73652; }
    .feedback-area { margin-top: 10px; display: none; }
    .feedback-area textarea {
      width: 100%; padding: 8px; border-radius: 6px; border: 1px solid #333;
      background: #0d0d1a; color: #f0f0f0; font-size: 0.82rem; resize: vertical; min-height: 60px;
    }
    .btn-send-regen {
      margin-top: 6px; width: 100%; padding: 7px; background: #f0c040; color: #1a1a1a;
      border: none; border-radius: 6px; font-size: 0.82rem; font-weight: 700; cursor: pointer;
    }
    .toast {
      position: fixed; bottom: 32px; left: 50%; transform: translateX(-50%);
      background: #1a3a2a; color: #4caf7d; padding: 10px 24px; border-radius: 8px;
      font-size: 0.88rem; opacity: 0; transition: opacity 0.3s; pointer-events: none;
      z-index: 200;
    }
    .toast.show { opacity: 1; }
    #backToTop {
      position: fixed; bottom: 28px; right: 24px;
      background: #e94560; color: #fff; border: none; border-radius: 50%;
      width: 44px; height: 44px; font-size: 1.2rem; cursor: pointer;
      display: none; align-items: center; justify-content: center;
      box-shadow: 0 4px 16px rgba(233,69,96,0.4); z-index: 99;
    }
    #backToTop.visible { display: flex; }
  </style>
</head>
<body>
<nav>
  <span class="nav-brand">Agente Cosmic</span>
  <div class="nav-actions">
    <a href="{% url 'dashboard' %}" class="nav-btn nav-btn-ghost">Dashboard</a>
    <a href="{% url 'landing' %}" class="nav-btn nav-btn-primary">+ Nuevo análisis</a>
    <a href="{% url 'logout' %}" class="nav-btn nav-btn-ghost" style="font-size:0.8rem;">Salir</a>
  </div>
</nav>

<div class="container">
  <h1>Revisa tu calendario de contenido</h1>
  <p class="subtitle">
    {{ brand_dna.business_name }} · Aprueba, edita o pide cambios en cada post antes de que se envíe.
  </p>

  <div class="legend">
    <div class="legend-item"><div class="dot dot-pending"></div> Sin revisar</div>
    <div class="legend-item"><div class="dot dot-approved"></div> Aprobado</div>
    <div class="legend-item"><div class="dot dot-edited"></div> Editado</div>
    <div class="legend-item"><div class="dot dot-change"></div> Cambio solicitado</div>
  </div>

  <div class="posts-grid">
    {% for post in posts %}
    <div class="post-card {{ post.user_status }}" id="card-{{ post.id }}">
      <div class="post-img">
        {% if post.image_url %}
          <img src="{{ post.image_url }}" alt="Día {{ post.day_number }}" loading="lazy">
        {% else %}
          📸
        {% endif %}
      </div>
      <div class="post-body">
        <div class="post-day">Día {{ post.day_number }}</div>
        <div class="post-schedule">
          📅 {{ post.scheduled_at|date:"D d M" }} · {{ post.scheduled_at|date:"H:i" }}
        </div>
        <div class="post-caption" id="caption-{{ post.id }}">{{ post.caption }}</div>
        {% if post.hashtags %}
        <div class="post-hashtags">{{ post.hashtags|join:" " }}</div>
        {% endif %}
        <div class="post-status status-{{ post.user_status }}" id="status-{{ post.id }}">
          {% if post.user_status == 'approved' %}✓ Aprobado
          {% elif post.user_status == 'edited' %}✏ Editado
          {% elif post.user_status == 'change_requested' %}↺ Cambio solicitado
          {% else %}○ Sin revisar{% endif %}
        </div>
        <div class="actions">
          <button class="btn-action btn-approve" onclick="approvePost('{{ post.id }}')">✓ Aprobar</button>
          <button class="btn-action btn-edit" onclick="startEdit('{{ post.id }}')">✏ Editar</button>
          <button class="btn-action btn-regen" onclick="toggleRegen('{{ post.id }}')">↺ Cambio</button>
          <button class="btn-action btn-save" id="save-{{ post.id }}" onclick="saveEdit('{{ post.id }}')">Guardar</button>
        </div>
        <div class="feedback-area" id="regen-{{ post.id }}">
          <textarea id="note-{{ post.id }}" placeholder="Describe el cambio que quieres..."></textarea>
          <button class="btn-send-regen" onclick="requestRegen('{{ post.id }}')">Regenerar con IA ✨</button>
        </div>
      </div>
    </div>
    {% endfor %}
  </div>
</div>

<div class="toast" id="toast"></div>
<button id="backToTop" title="Volver arriba">↑</button>

<script>
  const CSRF = '{{ csrf_token }}';

  function showToast(msg, color) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.style.background = color || '#1a3a2a';
    t.style.color = color ? '#1a1a1a' : '#4caf7d';
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2500);
  }

  async function postAction(postId, payload) {
    const res = await fetch(`/api/post/${postId}/action/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify(payload),
    });
    return res.json();
  }

  function setCardStatus(postId, status, label) {
    const card = document.getElementById('card-' + postId);
    card.className = 'post-card ' + status;
    const statusEl = document.getElementById('status-' + postId);
    statusEl.className = 'post-status status-' + status;
    statusEl.textContent = label;
  }

  async function approvePost(postId) {
    const data = await postAction(postId, { action: 'approve' });
    if (data.status === 'ok') {
      setCardStatus(postId, 'approved', '✓ Aprobado');
      showToast('Post aprobado ✓');
    }
  }

  function startEdit(postId) {
    const cap = document.getElementById('caption-' + postId);
    cap.contentEditable = 'true';
    cap.focus();
    document.getElementById('save-' + postId).style.display = 'block';
  }

  async function saveEdit(postId) {
    const cap = document.getElementById('caption-' + postId);
    const newCaption = cap.innerText.trim();
    const data = await postAction(postId, { action: 'edit', value: newCaption });
    if (data.status === 'ok') {
      cap.contentEditable = 'false';
      document.getElementById('save-' + postId).style.display = 'none';
      setCardStatus(postId, 'edited', '✏ Editado');
      showToast('Caption guardado ✏');
    }
  }

  function toggleRegen(postId) {
    const area = document.getElementById('regen-' + postId);
    area.style.display = area.style.display === 'block' ? 'none' : 'block';
  }

  async function requestRegen(postId) {
    const note = document.getElementById('note-' + postId).value.trim();
    if (!note) { showToast('Escribe el feedback primero', '#f0c040'); return; }
    const btn = document.querySelector(`#regen-${postId} .btn-send-regen`);
    btn.textContent = 'Generando...';
    btn.disabled = true;
    const data = await postAction(postId, { action: 'regenerate', value: note });
    btn.textContent = 'Regenerar con IA ✨';
    btn.disabled = false;
    if (data.status === 'ok') {
      document.getElementById('caption-' + postId).textContent = data.caption;
      document.getElementById('regen-' + postId).style.display = 'none';
      setCardStatus(postId, 'change_requested', '↺ Cambio solicitado');
      showToast('Caption regenerado ✨', '#f0c040');
    }
  }

  // Back to top
  const btn = document.getElementById('backToTop');
  window.addEventListener('scroll', () => btn.classList.toggle('visible', scrollY > 300));
  btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
</script>
</body>
</html>
```

- [ ] **Step 2: Añadir link en dashboard.html**

En `core/brand_dna/templates/brand_dna/dashboard.html`, dentro del bloque `{% if job.status == 'done' %}`, añadir el link al calendario:

Reemplazar:
```html
        {% if job.status == 'done' %}
        <a href="{% url 'results' job.id %}" class="job-link">Ver resultados →</a>
        {% endif %}
```
Con:
```html
        {% if job.status == 'done' %}
        <div style="display:flex;gap:10px;flex-direction:column;align-items:flex-end;">
          <a href="{% url 'results' job.id %}" class="job-link">Ver resultados →</a>
          <a href="{% url 'calendar_review' job.id %}" class="job-link" style="color:#4a9eff;">Revisar calendario →</a>
        </div>
        {% endif %}
```

- [ ] **Step 3: Añadir link en results.html al completarse**

En `core/brand_dna/templates/brand_dna/results.html`, dentro de la `email-note` div, añadir el botón de revisión. Reemplazar:

```html
    <div class="email-note">
      📬 Revisa tu correo — te enviamos el ADN completo y el contenido del Día 1.<br>
      Recibirás un email cada mañana a las 7 AM durante 7 días.
    </div>
```
Con:
```html
    <div class="email-note">
      📬 Revisa tu correo — te enviamos el ADN completo y el contenido del Día 1.<br>
      Recibirás un email cada mañana a las 7 AM durante 7 días.
    </div>
    <div style="text-align:center;margin-top:24px;">
      <a id="calendarLink" href="#" class="nav-btn nav-btn-primary" style="display:inline-block;padding:14px 32px;font-size:1rem;text-decoration:none;">
        Revisar y aprobar mi calendario →
      </a>
    </div>
```

Y en el JavaScript de results.html, dentro del bloque `if (data.status === 'done')`, añadir después de `renderCalendar`:
```javascript
        const calLink = document.getElementById('calendarLink');
        if (calLink) calLink.href = '/calendar/{{ job.id }}/';
```

- [ ] **Step 4: Verificar manualmente**

1. Abrir `http://localhost:3002/dashboard/` en el navegador
2. Para un job completado, verificar que aparecen los dos links: "Ver resultados" y "Revisar calendario"
3. Click en "Revisar calendario" — debe abrir la vista con los 7 cards de posts
4. Probar botón "Aprobar" en un post — debe cambiar el borde a verde y mostrar "✓ Aprobado"
5. Probar "Editar" — debe activar edición inline; "Guardar" actualiza el caption

- [ ] **Step 5: Commit final**

```bash
GIT_EDITOR=true git add core/brand_dna/templates/ core/brand_dna/views.py core/brand_dna/urls.py && GIT_EDITOR=true git commit -m "feat: calendar review UI with approve/edit/regenerate per post"
```

---

## Self-Review

### Cobertura de spec
- ✅ Texto dentro de la imagen — Task 1 (PIL overlay)
- ✅ Calendario inteligente por día — Task 2 (smart_scheduler.py)
- ✅ Calendar feedback loop — Tasks 3, 4, 5 (modelo + vistas + template)
- ✅ Aprobar post — `approvePost()` → endpoint `action=approve`
- ✅ Editar directamente — `startEdit()` + `saveEdit()` → `action=edit`
- ✅ Pedir cambio con feedback → LLM regenera → `action=regenerate`
- ✅ Nav header con Dashboard y Nuevo análisis en todas las páginas
- ✅ Back to top button en calendar review

### Consistencia de tipos
- `ContentPost.USER_STATUS_*` constantes definidas en Task 3, usadas en Tasks 4 y 5
- `smart_schedule_dates()` firma: `(brand_dna, base_date: date, count: int) -> list[datetime]` — consistente entre Task 2 y tasks.py
- `_regenerate_caption(post, feedback: str) -> str` — definida en views.py Task 4, solo se llama ahí

### Sin placeholders
- Todos los steps tienen código completo
- No hay TBD ni TODOs
