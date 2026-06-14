# Encuesta de fin de semana + continuación automática — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Al completar cada semana de contenido (día 7, 14, 21...), pedir feedback al usuario (rating + comentario) y, si acepta, generar y programar automáticamente la siguiente semana de posts reutilizando el `BrandDNA` existente — sin re-scrapear nada.

**Architecture:** Nuevo modelo `WeeklyFeedback` (1 por calendario+semana) creado por `send_daily_email_task` cuando `day_number % 7 == 0`. El email del día 7/14/... incluye un CTA hacia `calendar_review`, donde se muestra un banner de encuesta si hay feedback `pending`. El endpoint `calendar_feedback_api` guarda la respuesta y, si `continue_decision == 'yes'`, llama a `generate_next_week()` (reutiliza `TextGenerator`, `smart_schedule_dates`, `schedule_daily_emails`) para crear los 7 `ContentPost` de la siguiente semana de forma síncrona.

**Tech Stack:** Django 5.2, PostgreSQL, RQ/django-rq, pytest + pytest-django (`--nomigrations`, `--reuse-db --create-db`).

**Spec:** `docs/superpowers/specs/2026-06-13-weekly-feedback-continuation-design.md` (fuente de verdad — leer antes de implementar cualquier tarea).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `core/content_pipeline/models.py` | Modify | Agrega `WeeklyFeedback` model y `ContentCalendar.active_product_images` |
| `core/content_pipeline/migrations/0004_weeklyfeedback_active_product_images.py` | Create | Migración para el modelo/campo nuevos |
| `core/content_pipeline/tests/test_models.py` | Modify | Tests de constraints/defaults de `WeeklyFeedback` y `active_product_images` |
| `saas_chatbot/settings.py` | Modify | Nuevo setting `COSMIC_BASE_URL` |
| `.env` | Modify | `COSMIC_BASE_URL=https://cosmic.anuarbarrera.dev` |
| `.env.example` | Modify | Documenta `COSMIC_BASE_URL` |
| `core/content_pipeline/tasks.py` | Modify | Generaliza `_load_product_images`/`_product_image_for_day`, fija `active_product_images` en `content_generation_task`, crea `WeeklyFeedback` en `send_daily_email_task`, nueva `generate_next_week()` |
| `core/content_pipeline/tests/test_tasks.py` | Modify | Tests de los cambios anteriores |
| `core/content_pipeline/scheduler.py` | Modify | Filtro `status=PENDING` en `schedule_daily_emails` |
| `core/content_pipeline/tests/test_scheduler.py` | Modify | Test de no-reprogramación de posts ya enviados |
| `core/content_pipeline/email_sender.py` | Modify | `send_daily` construye `calendar_review_url` con `COSMIC_BASE_URL` |
| `core/content_pipeline/templates/content_pipeline/email_daily.html` | Modify | Bloque CTA de fin de semana |
| `core/content_pipeline/tests/test_email_sender.py` | Modify | Tests del CTA y la URL |
| `core/brand_dna/views.py` | Modify | `calendar_review_view` expone `pending_feedback`/`product_pool`; nueva vista `calendar_feedback_api` + helper `_update_active_product_images` |
| `core/brand_dna/urls.py` | Modify | Nueva ruta `api/calendar/<uuid:job_id>/feedback/` |
| `core/brand_dna/templates/brand_dna/calendar_review.html` | Modify | Banner de encuesta + sección de imágenes + JS |
| `core/brand_dna/tests/test_views.py` | Modify | Tests de `pending_feedback`, banner, `calendar_feedback_api`, `_update_active_product_images` |

---

## Task 1: Modelo `WeeklyFeedback` + campo `active_product_images` + migración

**Files:**
- Modify: `core/content_pipeline/models.py`
- Create: `core/content_pipeline/migrations/0004_weeklyfeedback_active_product_images.py`
- Test: `core/content_pipeline/tests/test_models.py`

- [ ] **Step 1: Escribe los tests que fallan**

Edita `core/content_pipeline/tests/test_models.py`. El archivo actual es:

```python
import pytest
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db


@pytest.fixture
def brand_dna():
    job = AnalysisJob.objects.create(email='test@example.com', business_url='https://tuwebmx.com')
    return BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseño'], audience='PYMEs',
        tone='profesional', primary_colors=['#1A1A2E'],
    )


def test_content_calendar_creation(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    assert calendar.brand_dna == brand_dna
    assert calendar.id is not None


def test_content_post_creation(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    scheduled = timezone.now() + timedelta(days=1)
    post = ContentPost.objects.create(
        calendar=calendar,
        day_number=1,
        caption='Post de prueba para redes sociales.',
        image_url='https://storage.googleapis.com/agente-cosmic/img1.jpg',
        suggested_time='19:00',
        hashtags=['#diseñoweb', '#mexico'],
        scheduled_at=scheduled,
    )
    assert post.status == 'pending'
    assert post.day_number == 1
    assert post.sent_at is None


def test_calendar_has_7_posts(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    for i in range(1, 8):
        ContentPost.objects.create(
            calendar=calendar, day_number=i,
            caption=f'Post día {i}', image_url='https://example.com/img.jpg',
            suggested_time='19:00', hashtags=[],
            scheduled_at=timezone.now() + timedelta(days=i),
        )
    assert calendar.posts.count() == 7
```

Reemplázalo por (agrega imports de `IntegrityError`/`transaction`, `WeeklyFeedback`, y 3 tests nuevos al final):

```python
import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback

pytestmark = pytest.mark.django_db


@pytest.fixture
def brand_dna():
    job = AnalysisJob.objects.create(email='test@example.com', business_url='https://tuwebmx.com')
    return BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseño'], audience='PYMEs',
        tone='profesional', primary_colors=['#1A1A2E'],
    )


def test_content_calendar_creation(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    assert calendar.brand_dna == brand_dna
    assert calendar.id is not None


def test_content_post_creation(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    scheduled = timezone.now() + timedelta(days=1)
    post = ContentPost.objects.create(
        calendar=calendar,
        day_number=1,
        caption='Post de prueba para redes sociales.',
        image_url='https://storage.googleapis.com/agente-cosmic/img1.jpg',
        suggested_time='19:00',
        hashtags=['#diseñoweb', '#mexico'],
        scheduled_at=scheduled,
    )
    assert post.status == 'pending'
    assert post.day_number == 1
    assert post.sent_at is None


def test_calendar_has_7_posts(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    for i in range(1, 8):
        ContentPost.objects.create(
            calendar=calendar, day_number=i,
            caption=f'Post día {i}', image_url='https://example.com/img.jpg',
            suggested_time='19:00', hashtags=[],
            scheduled_at=timezone.now() + timedelta(days=i),
        )
    assert calendar.posts.count() == 7


def test_content_calendar_active_product_images_default(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    assert calendar.active_product_images == []


def test_weekly_feedback_defaults(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    feedback = WeeklyFeedback.objects.create(calendar=calendar, week_number=1)
    assert feedback.rating is None
    assert feedback.comment == ''
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_PENDING
    assert feedback.responded_at is None
    assert feedback.created_at is not None


def test_weekly_feedback_unique_per_calendar_and_week(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    WeeklyFeedback.objects.create(calendar=calendar, week_number=1)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WeeklyFeedback.objects.create(calendar=calendar, week_number=1)
```

- [ ] **Step 2: Corre los tests, verifica que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'WeeklyFeedback'` (o `AttributeError: 'ContentCalendar' object has no attribute 'active_product_images'`)

- [ ] **Step 3: Implementa el modelo**

El archivo actual `core/content_pipeline/models.py` es:

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    calendar = models.ForeignKey(ContentCalendar, on_delete=models.CASCADE, related_name='posts')
    day_number = models.IntegerField()
    caption = models.TextField()
    image_url = models.URLField(max_length=1000)
    suggested_time = models.TimeField()
    hashtags = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    scheduled_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)

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
    regen_count = models.PositiveIntegerField(default=0)
    edit_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'content_pipeline_post'
        ordering = ['day_number']

    def __str__(self):
        return f"Día {self.day_number} — {self.calendar.brand_dna.business_name}"
```

Reemplázalo por (agrega `active_product_images` a `ContentCalendar` y un nuevo modelo `WeeklyFeedback` al final del archivo):

```python
import uuid
from django.db import models
from core.brand_dna.models import BrandDNA


class ContentCalendar(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand_dna = models.OneToOneField(BrandDNA, on_delete=models.CASCADE, related_name='calendar')
    created_at = models.DateTimeField(auto_now_add=True)
    active_product_images = models.JSONField(default=list, blank=True)

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    calendar = models.ForeignKey(ContentCalendar, on_delete=models.CASCADE, related_name='posts')
    day_number = models.IntegerField()
    caption = models.TextField()
    image_url = models.URLField(max_length=1000)
    suggested_time = models.TimeField()
    hashtags = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    scheduled_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)

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
    regen_count = models.PositiveIntegerField(default=0)
    edit_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'content_pipeline_post'
        ordering = ['day_number']

    def __str__(self):
        return f"Día {self.day_number} — {self.calendar.brand_dna.business_name}"


class WeeklyFeedback(models.Model):
    CONTINUE_PENDING = 'pending'
    CONTINUE_YES = 'yes'
    CONTINUE_NO = 'no'
    CONTINUE_CHOICES = [
        (CONTINUE_PENDING, 'Pendiente'),
        (CONTINUE_YES, 'Sí'),
        (CONTINUE_NO, 'No'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    calendar = models.ForeignKey(ContentCalendar, on_delete=models.CASCADE, related_name='feedback_entries')
    week_number = models.IntegerField()
    rating = models.IntegerField(null=True, blank=True)
    comment = models.TextField(blank=True, default='')
    continue_decision = models.CharField(max_length=10, choices=CONTINUE_CHOICES, default=CONTINUE_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'content_pipeline_weekly_feedback'
        unique_together = ('calendar', 'week_number')
        ordering = ['week_number']

    def __str__(self):
        return f"Feedback semana {self.week_number} — {self.calendar.brand_dna.business_name}"
```

- [ ] **Step 4: Corre los tests, verifica que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_models.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Crea la migración**

Crea `core/content_pipeline/migrations/0004_weeklyfeedback_active_product_images.py`:

```python
import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content_pipeline', '0003_contentpost_counts'),
    ]

    operations = [
        migrations.AddField(
            model_name='contentcalendar',
            name='active_product_images',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name='WeeklyFeedback',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('week_number', models.IntegerField()),
                ('rating', models.IntegerField(blank=True, null=True)),
                ('comment', models.TextField(blank=True, default='')),
                ('continue_decision', models.CharField(choices=[('pending', 'Pendiente'), ('yes', 'Sí'), ('no', 'No')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('calendar', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feedback_entries', to='content_pipeline.contentcalendar')),
            ],
            options={
                'db_table': 'content_pipeline_weekly_feedback',
                'ordering': ['week_number'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='weeklyfeedback',
            unique_together={('calendar', 'week_number')},
        ),
    ]
```

- [ ] **Step 6: Verifica que la migración está completa**

Run: `docker compose exec backend python manage.py makemigrations content_pipeline --check --dry-run`
Expected: `No changes detected in app 'content_pipeline'`

- [ ] **Step 7: Commit**

```bash
git add core/content_pipeline/models.py core/content_pipeline/migrations/0004_weeklyfeedback_active_product_images.py core/content_pipeline/tests/test_models.py
GIT_EDITOR=true git commit -m "feat: agregar modelo WeeklyFeedback y campo active_product_images"
```

---

## Task 2: Setting `COSMIC_BASE_URL`

**Files:**
- Modify: `saas_chatbot/settings.py`
- Modify: `.env`
- Modify: `.env.example`

- [ ] **Step 1: Agrega el setting**

En `saas_chatbot/settings.py`, justo después de la línea 516 (`FRONTEND_URL = get_env('FRONTEND_URL', default='http://localhost:3000')`), agrega:

```python
# Base URL para Agente Cosmic — usada para construir links en emails (encuesta semanal)
COSMIC_BASE_URL = get_env('COSMIC_BASE_URL', default='https://cosmic.anuarbarrera.dev')
```

- [ ] **Step 2: Agrega la variable a `.env`**

En `.env` (raíz del proyecto, en este workspace), después de la línea `FRONTEND_URL=http://dialogix.anuarbarrera.dev`, agrega:

```
COSMIC_BASE_URL=https://cosmic.anuarbarrera.dev
```

Nota: `.env` está en `.gitignore` (no se commitea) — este paso solo asegura que el contenedor de este workspace cargue la variable para el Step 4.

- [ ] **Step 3: Documenta la variable en `.env.example`**

En `.env.example` (raíz del proyecto, archivo versionado), después de la línea `ALLOWED_HOSTS=localhost,127.0.0.1,backend,your-domain.com`, agrega:

```
# Base URL usada para construir links en emails (ej. encuesta semanal de feedback)
COSMIC_BASE_URL=https://cosmic.anuarbarrera.dev
```

- [ ] **Step 4: Verifica que el setting carga correctamente**

Run: `docker compose exec backend python manage.py shell -c "from django.conf import settings; print(settings.COSMIC_BASE_URL)"`
Expected: `https://cosmic.anuarbarrera.dev`

Nota: este setting no tiene comportamiento propio todavía — se usa por primera vez en la Tarea 6 (`email_sender.py`), donde sí se prueba vía `@override_settings(COSMIC_BASE_URL=...)`.

- [ ] **Step 5: Commit**

```bash
git add saas_chatbot/settings.py .env.example
GIT_EDITOR=true git commit -m "feat: agregar setting COSMIC_BASE_URL para links de email"
```

---

## Task 3: Generalizar `_load_product_images` / `_product_image_for_day` + `active_product_images` en `content_generation_task`

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Test: `core/content_pipeline/tests/test_tasks.py`

- [ ] **Step 1: Escribe los tests que fallan**

Edita `core/content_pipeline/tests/test_tasks.py`. Agrega estos 3 tests al final del archivo (después de `test_content_generation_marks_job_done`):

```python
def test_load_product_images_takes_paths_list(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    uploads_dir = tmp_path / 'uploads'
    uploads_dir.mkdir()
    (uploads_dir / 'product.webp').write_bytes(b'fake-image-bytes')

    from core.content_pipeline.tasks import _load_product_images
    result = _load_product_images(['uploads/product.webp'])
    assert result == [b'fake-image-bytes']


def test_product_image_for_day_maps_day_in_week():
    from core.content_pipeline.tasks import _product_image_for_day
    images = [b'img1', b'img2', b'img3']

    # Semana 1: day_in_week == day_number
    assert _product_image_for_day(1, images) == b'img1'
    assert _product_image_for_day(3, images) == b'img3'
    assert _product_image_for_day(4, images) is None

    # Semana 2, día 8 -> day_in_week 1 (mismo resultado que día 1 de semana 1)
    day_in_week = ((8 - 1) % 7) + 1
    assert day_in_week == 1
    assert _product_image_for_day(day_in_week, images) == _product_image_for_day(1, images)


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_content_generation_sets_active_product_images(job_with_dna):
    job_with_dna.product_image_paths = ['uploads/p1.jpg', 'uploads/p2.jpg']
    job_with_dna.save(update_fields=['product_image_paths'])

    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
    assert calendar.active_product_images == ['uploads/p1.jpg', 'uploads/p2.jpg']
```

- [ ] **Step 2: Corre los tests, verifica que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: FAIL —
- `test_load_product_images_takes_paths_list`: `AttributeError: 'list' object has no attribute 'product_image_paths'`
- `test_product_image_for_day_maps_day_in_week`: PASA por casualidad (la firma actual no cambia el comportamiento) — verifica que sigue pasando después del refactor.
- `test_content_generation_sets_active_product_images`: `AssertionError` — `calendar.active_product_images == []` (default) != `['uploads/p1.jpg', 'uploads/p2.jpg']`

- [ ] **Step 3: Implementa el refactor**

En `core/content_pipeline/tasks.py`, reemplaza `_load_product_images`:

```python
def _load_product_images(job) -> list[bytes]:
    """Carga hasta 7 imágenes de producto normalizadas a WebP."""
    paths = job.product_image_paths or []
    if not paths and job.product_image_path:
        paths = [job.product_image_path]
    result = []
    for path in paths[:7]:
        full = os.path.join(settings.MEDIA_ROOT, path)
        if os.path.exists(full):
            with open(full, 'rb') as f:
                result.append(normalize_image(f.read()))
    return result
```

por:

```python
def _load_product_images(paths: list[str]) -> list[bytes]:
    """Carga hasta 7 imágenes de producto normalizadas a WebP."""
    result = []
    for path in (paths or [])[:7]:
        full = os.path.join(settings.MEDIA_ROOT, path)
        if os.path.exists(full):
            with open(full, 'rb') as f:
                result.append(normalize_image(f.read()))
    return result
```

Renombra el primer parámetro de `_product_image_for_day` de `day_number` a `day_in_week` (el cuerpo no cambia):

```python
def _product_image_for_day(day_in_week: int, images: list[bytes]) -> bytes | None:
    """Asigna imagen de producto por día dentro de la semana (1-7).
    - Si hay imagen para ese día exacto: úsala.
    - Si solo hay 1 imagen: se repite el día 2 (máx 2 usos).
    - Después del día 3 sin imagen directa: sin producto.
    """
    n = len(images)
    if n == 0:
        return None
    if day_in_week <= n:
        return images[day_in_week - 1]
    if n == 1 and day_in_week == 2:
        return images[0]
    return None
```

En `content_generation_task`, cambia estas dos líneas:

```python
        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
```

por:

```python
        calendar = ContentCalendar.objects.create(
            brand_dna=brand_dna,
            active_product_images=job.product_image_paths[:7],
        )
```

y:

```python
        # Cargar imágenes de producto (hasta 7, una por día)
        product_images_bytes = _load_product_images(job)
```

por:

```python
        # Cargar imágenes de producto (hasta 7, una por día)
        product_images_bytes = _load_product_images(calendar.active_product_images)
```

El resto de `content_generation_task` no cambia: la llamada `_product_image_for_day(i, product_images_bytes)` sigue funcionando porque para `i` en 1-7, `day_in_week == i`.

- [ ] **Step 4: Corre los tests, verifica que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
GIT_EDITOR=true git commit -m "refactor: generalizar _load_product_images y _product_image_for_day para reutilizar en semanas futuras"
```

---

## Task 4: Crear `WeeklyFeedback` al completar cada semana en `send_daily_email_task`

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Test: `core/content_pipeline/tests/test_tasks.py`

- [ ] **Step 1: Escribe los tests que fallan**

Edita `core/content_pipeline/tests/test_tasks.py`. Primero, actualiza los imports del inicio del archivo:

```python
import pytest
from unittest.mock import patch
from django.test import override_settings
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost
```

por:

```python
import pytest
from unittest.mock import patch
from django.test import override_settings
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback
```

Luego agrega, al final del archivo, una fixture, un helper y 3 tests nuevos:

```python
@pytest.fixture
def calendar_with_dna():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    return ContentCalendar.objects.create(brand_dna=dna)


def _make_post(calendar, day_number, **kwargs):
    defaults = dict(
        caption=f'Post {day_number}',
        image_url='https://example.com/img.jpg',
        suggested_time='19:00',
        hashtags=[],
        scheduled_at=timezone.now() + timedelta(days=day_number),
    )
    defaults.update(kwargs)
    return ContentPost.objects.create(calendar=calendar, day_number=day_number, **defaults)


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_task_creates_weekly_feedback_on_day_7(calendar_with_dna):
    post = _make_post(calendar_with_dna, 7)
    with patch('core.content_pipeline.tasks.EmailSender'):
        from core.content_pipeline.tasks import send_daily_email_task
        send_daily_email_task(str(post.id))

    assert WeeklyFeedback.objects.filter(calendar=calendar_with_dna, week_number=1).exists()


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_task_no_feedback_on_other_days(calendar_with_dna):
    post = _make_post(calendar_with_dna, 5)
    with patch('core.content_pipeline.tasks.EmailSender'):
        from core.content_pipeline.tasks import send_daily_email_task
        send_daily_email_task(str(post.id))

    assert not WeeklyFeedback.objects.filter(calendar=calendar_with_dna).exists()


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_task_weekly_feedback_idempotent(calendar_with_dna):
    post = _make_post(calendar_with_dna, 14)
    with patch('core.content_pipeline.tasks.EmailSender'):
        from core.content_pipeline.tasks import send_daily_email_task
        send_daily_email_task(str(post.id))
        send_daily_email_task(str(post.id))

    assert WeeklyFeedback.objects.filter(calendar=calendar_with_dna, week_number=2).count() == 1
```

Nota: en los 3 tests, `post.image_url` ya está seteado (default `'https://example.com/img.jpg'` del helper `_make_post`), así que `send_daily_email_task` no entra al bloque de generación de imagen — no hace falta mockear `ImageGenerator`.

- [ ] **Step 2: Corre los tests, verifica que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v -k weekly_feedback`
Expected: FAIL — `ImportError: cannot import name 'WeeklyFeedback'` desde `core.content_pipeline.models` en el archivo de test (el modelo ya existe desde la Tarea 1, pero `tasks.py` aún no crea registros, así que `test_send_daily_email_task_creates_weekly_feedback_on_day_7` falla con `AssertionError: False is not true`)

- [ ] **Step 3: Implementa la creación de `WeeklyFeedback`**

En `core/content_pipeline/tasks.py`, cambia el import:

```python
from core.content_pipeline.models import ContentCalendar, ContentPost
```

por:

```python
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback
```

Reemplaza `send_daily_email_task` completo:

```python
def send_daily_email_task(post_id: str) -> None:
    post = ContentPost.objects.select_related('calendar__brand_dna__job').get(id=post_id)
    if post.calendar.brand_dna.job.deleted_at is not None:
        logger.info(f"Post {post_id} omitido — calendario eliminado por el usuario")
        return
    # Genera la imagen justo antes de enviar (solo si no fue generada antes)
    if not post.image_url:
        brand_dna = post.calendar.brand_dna
        job_id = str(brand_dna.job.id)
        try:
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            product_images = _load_product_images(brand_dna.job)
            product_image_bytes = _product_image_for_day(post.day_number, product_images)
            post.image_url = image_gen.generate(
                caption=post.caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day{post.day_number}",
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                product_image_bytes=product_image_bytes,
            )
            post.save(update_fields=['image_url'])
        except Exception as img_err:
            logger.warning(f"Imagen día {post.day_number} falló (no fatal): {img_err}")
    EmailSender().send_daily(post=post)
```

por:

```python
def send_daily_email_task(post_id: str) -> None:
    post = ContentPost.objects.select_related('calendar__brand_dna__job').get(id=post_id)
    if post.calendar.brand_dna.job.deleted_at is not None:
        logger.info(f"Post {post_id} omitido — calendario eliminado por el usuario")
        return
    # Genera la imagen justo antes de enviar (solo si no fue generada antes)
    if not post.image_url:
        brand_dna = post.calendar.brand_dna
        job_id = str(brand_dna.job.id)
        day_in_week = ((post.day_number - 1) % 7) + 1
        try:
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            product_images = _load_product_images(post.calendar.active_product_images)
            product_image_bytes = _product_image_for_day(day_in_week, product_images)
            post.image_url = image_gen.generate(
                caption=post.caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day{post.day_number}",
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                product_image_bytes=product_image_bytes,
            )
            post.save(update_fields=['image_url'])
        except Exception as img_err:
            logger.warning(f"Imagen día {post.day_number} falló (no fatal): {img_err}")
    EmailSender().send_daily(post=post)

    if post.day_number % 7 == 0:
        week_number = post.day_number // 7
        WeeklyFeedback.objects.get_or_create(calendar=post.calendar, week_number=week_number)
```

- [ ] **Step 4: Corre los tests, verifica que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
GIT_EDITOR=true git commit -m "feat: crear WeeklyFeedback al enviar el ultimo email de cada semana"
```

---

## Task 5: Fix en `schedule_daily_emails` — no reprogramar posts ya enviados

**Files:**
- Modify: `core/content_pipeline/scheduler.py`
- Test: `core/content_pipeline/tests/test_scheduler.py`

- [ ] **Step 1: Escribe el test que falla**

Agrega al final de `core/content_pipeline/tests/test_scheduler.py` (los imports ya incluyen `ContentPost`, `timezone`, `timedelta`, `patch`, `MagicMock` — no se necesitan cambios de imports):

```python
def test_schedule_daily_emails_does_not_reschedule_sent_posts(calendar_with_7_posts):
    # Marcar días 2-7 como ya enviados (semana 1 completada)
    for post in calendar_with_7_posts.posts.filter(day_number__gt=1):
        post.status = ContentPost.STATUS_SENT
        post.save(update_fields=['status'])

    # Agregar semana 2 (días 8-14), pendientes
    for i in range(8, 15):
        ContentPost.objects.create(
            calendar=calendar_with_7_posts, day_number=i, caption=f'Post {i}',
            image_url='', suggested_time='19:00', hashtags=[],
            scheduled_at=timezone.now() + timedelta(days=i),
        )

    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts)

    scheduled_days = []
    for call in mock_queue.enqueue_in.call_args_list:
        post_id = str(call[0][2])
        post = ContentPost.objects.get(id=post_id)
        scheduled_days.append(post.day_number)

    assert sorted(scheduled_days) == list(range(8, 15))
```

- [ ] **Step 2: Corre el test, verifica que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_scheduler.py -v -k does_not_reschedule`
Expected: FAIL — `AssertionError: [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14] != [8, 9, 10, 11, 12, 13, 14]` (los días 2-7, ya `sent`, se reprograman)

- [ ] **Step 3: Implementa el fix**

En `core/content_pipeline/scheduler.py`, cambia el import:

```python
from core.content_pipeline.models import ContentCalendar
```

por:

```python
from core.content_pipeline.models import ContentCalendar, ContentPost
```

Y cambia la línea del filtro:

```python
    posts = list(calendar.posts.filter(day_number__gt=1).order_by('day_number'))
```

por:

```python
    posts = list(calendar.posts.filter(
        day_number__gt=1, status=ContentPost.STATUS_PENDING
    ).order_by('day_number'))
```

- [ ] **Step 4: Corre los tests, verifica que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_scheduler.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/scheduler.py core/content_pipeline/tests/test_scheduler.py
GIT_EDITOR=true git commit -m "fix: schedule_daily_emails no reprograma posts ya enviados (regresion semana 2)"
```

---

## Task 6: CTA de fin de semana en el email diario

**Files:**
- Modify: `core/content_pipeline/email_sender.py`
- Modify: `core/content_pipeline/templates/content_pipeline/email_daily.html`
- Test: `core/content_pipeline/tests/test_email_sender.py`

- [ ] **Step 1: Escribe los tests que fallan**

Agrega al final de `core/content_pipeline/tests/test_email_sender.py` (los imports ya incluyen `override_settings` y `patch` — no se necesitan cambios de imports):

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_weekend_cta_on_day_7(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    post = posts[6]  # day_number == 7
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_daily(post=post)
    html = mock_send.call_args[1]['html_message']
    assert 'Dar feedback y ver mi próxima semana' in html
    assert 'https://cosmic.anuarbarrera.dev' in html


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_no_weekend_cta_on_other_days(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    post = posts[1]  # day_number == 2
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_daily(post=post)
    html = mock_send.call_args[1]['html_message']
    assert 'Dar feedback y ver mi próxima semana' not in html
```

- [ ] **Step 2: Corre los tests, verifica que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py -v -k weekend_cta`
Expected:
- `test_send_daily_email_weekend_cta_on_day_7`: FAIL — `AssertionError: 'Dar feedback y ver mi próxima semana' in html` (el bloque CTA no existe todavía)
- `test_send_daily_email_no_weekend_cta_on_other_days`: PASS trivialmente (el texto tampoco existe para el día 2) — se mantiene como red de seguridad para el siguiente paso

- [ ] **Step 3: Implementa el CTA**

En `core/content_pipeline/email_sender.py`, agrega el import al inicio del archivo:

```python
import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost
```

por:

```python
import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost
```

Reemplaza el método `send_daily`:

```python
    def send_daily(self, post: ContentPost) -> None:
        html = render_to_string('content_pipeline/email_daily.html', {'post': post})
        business_name = post.calendar.brand_dna.business_name
        email = post.calendar.brand_dna.job.email
        send_mail(
            f'Dia {post.day_number} de tu calendario — {business_name}',
            f'Tu contenido del dia {post.day_number} esta listo.',
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=html,
            fail_silently=False,
        )
        post.status = ContentPost.STATUS_SENT
        post.sent_at = timezone.now()
        post.save(update_fields=['status', 'sent_at'])
        logger.info(f"Email dia {post.day_number} enviado a {email}")
```

por:

```python
    def send_daily(self, post: ContentPost) -> None:
        calendar_review_url = settings.COSMIC_BASE_URL + reverse(
            'calendar_review', args=[post.calendar.brand_dna.job.id]
        )
        html = render_to_string('content_pipeline/email_daily.html', {
            'post': post,
            'calendar_review_url': calendar_review_url,
        })
        business_name = post.calendar.brand_dna.business_name
        email = post.calendar.brand_dna.job.email
        send_mail(
            f'Dia {post.day_number} de tu calendario — {business_name}',
            f'Tu contenido del dia {post.day_number} esta listo.',
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=html,
            fail_silently=False,
        )
        post.status = ContentPost.STATUS_SENT
        post.sent_at = timezone.now()
        post.save(update_fields=['status', 'sent_at'])
        logger.info(f"Email dia {post.day_number} enviado a {email}")
```

En `core/content_pipeline/templates/content_pipeline/email_daily.html`, el archivo actual es:

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Dia {{ post.day_number }} — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
  <h1 style="color: #1a1a2e;">Dia {{ post.day_number }} de tu calendario</h1>
  <p>Contenido listo para publicar hoy en <strong>{{ post.calendar.brand_dna.business_name }}</strong>.</p>

  <div style="background: #f9f9f9; padding: 20px; border-radius: 8px; margin: 20px 0;">
    <h2 style="margin-top: 0;">Tu post de hoy</h2>
    <p style="font-size: 16px;">{{ post.caption }}</p>
    {% if post.image_url %}
    <img src="{{ post.image_url }}" style="max-width: 100%; border-radius: 8px; margin: 12px 0;" alt="Imagen del post">
    {% endif %}
    <p><strong>Horario sugerido:</strong> {{ post.suggested_time|time:"H:i" }}</p>
    <p><strong>Hashtags:</strong> {{ post.hashtags|join:" " }}</p>
  </div>

  <hr>
  <p style="font-size: 12px; color: #999;">Agente Cosmic — Powered by Google Cloud</p>
</body>
</html>
```

Reemplázalo por (agrega el bloque condicional entre el `</div>` de "Tu post de hoy" y el `<hr>`):

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Dia {{ post.day_number }} — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
  <h1 style="color: #1a1a2e;">Dia {{ post.day_number }} de tu calendario</h1>
  <p>Contenido listo para publicar hoy en <strong>{{ post.calendar.brand_dna.business_name }}</strong>.</p>

  <div style="background: #f9f9f9; padding: 20px; border-radius: 8px; margin: 20px 0;">
    <h2 style="margin-top: 0;">Tu post de hoy</h2>
    <p style="font-size: 16px;">{{ post.caption }}</p>
    {% if post.image_url %}
    <img src="{{ post.image_url }}" style="max-width: 100%; border-radius: 8px; margin: 12px 0;" alt="Imagen del post">
    {% endif %}
    <p><strong>Horario sugerido:</strong> {{ post.suggested_time|time:"H:i" }}</p>
    <p><strong>Hashtags:</strong> {{ post.hashtags|join:" " }}</p>
  </div>

  {% if post.day_number|divisibleby:7 %}
  <div style="margin-top:24px;padding:16px;background:#1a1a2e;border-radius:8px;text-align:center;">
    <p style="color:#f0f0f0;margin:0 0 12px;">🎉 ¡Esta fue tu última pieza de esta semana!</p>
    <a href="{{ calendar_review_url }}" style="display:inline-block;padding:12px 24px;background:#e94560;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">
      Dar feedback y ver mi próxima semana →
    </a>
  </div>
  {% endif %}

  <hr>
  <p style="font-size: 12px; color: #999;">Agente Cosmic — Powered by Google Cloud</p>
</body>
</html>
```

- [ ] **Step 4: Corre los tests, verifica que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/email_sender.py core/content_pipeline/templates/content_pipeline/email_daily.html core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "feat: agregar CTA de fin de semana al email del dia 7/14/21..."
```

---

## Task 7: `generate_next_week` — genera y programa la siguiente semana

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Test: `core/content_pipeline/tests/test_tasks.py`

- [ ] **Step 1: Escribe el test que falla**

Agrega al final de `core/content_pipeline/tests/test_tasks.py`:

```python
def test_generate_next_week_creates_posts_for_week_2(job_with_dna):
    calendar = ContentCalendar.objects.create(brand_dna=job_with_dna.brand_dna, active_product_images=[])

    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.schedule_daily_emails') as mock_schedule:
        MockText.return_value.generate.return_value = _MOCK_POSTS

        from core.content_pipeline.tasks import generate_next_week
        generate_next_week(calendar, week_number=2)

    days = sorted(p.day_number for p in calendar.posts.all())
    assert days == list(range(8, 15))
    mock_schedule.assert_called_once_with(calendar)
```

- [ ] **Step 2: Corre el test, verifica que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v -k generate_next_week`
Expected: FAIL — `ImportError: cannot import name 'generate_next_week' from 'core.content_pipeline.tasks'`

- [ ] **Step 3: Implementa `generate_next_week`**

Agrega esta función al final de `core/content_pipeline/tasks.py` (reutiliza `TextGenerator`, `smart_schedule_dates`, `schedule_daily_emails`, ya importados):

```python
def generate_next_week(calendar: ContentCalendar, week_number: int) -> None:
    brand_dna = calendar.brand_dna
    text_gen = TextGenerator()
    posts_data = text_gen.generate(brand_dna)

    now = timezone.now()
    mexico_today = now.astimezone(MEXICO_TZ).date()
    scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

    base_day = (week_number - 1) * 7

    for i, post_data in enumerate(posts_data, start=1):
        hour, minute = map(int, post_data.get('suggested_time', '19:00').split(':'))
        ContentPost.objects.create(
            calendar=calendar,
            day_number=base_day + i,
            caption=post_data['caption'],
            image_url='',
            suggested_time=f"{hour:02d}:{minute:02d}",
            hashtags=post_data.get('hashtags', []),
            scheduled_at=scheduled_dates[i - 1],
        )

    schedule_daily_emails(calendar)
```

Nota: a diferencia de `content_generation_task`, aquí NO se genera ninguna imagen de forma eager — todas las imágenes de la nueva semana se generan perezosamente en `send_daily_email_task` (igual que los días 2-7 de la semana 1).

- [ ] **Step 4: Corre el test, verifica que pasa**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
GIT_EDITOR=true git commit -m "feat: agregar generate_next_week para continuar el calendario tras feedback positivo"
```

---

## Task 8: `calendar_review_view` expone `pending_feedback`/`product_pool` + banner en el template

**Files:**
- Modify: `core/brand_dna/views.py`
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html`
- Test: `core/brand_dna/tests/test_views.py`

- [ ] **Step 1: Escribe los tests que fallan**

Edita `core/brand_dna/tests/test_views.py`. Cambia los imports del inicio:

```python
import pytest
import json
from unittest.mock import patch
from django.test import Client
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db
```

por:

```python
import pytest
import json
from unittest.mock import patch
from django.test import Client
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback

pytestmark = pytest.mark.django_db
```

Agrega al final del archivo dos fixtures y 3 tests:

```python
@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(
        username='feedback@test.com', email='feedback@test.com', password='pass1234'
    )


@pytest.fixture
def job_with_calendar(user):
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com', user=user,
        status=AnalysisJob.STATUS_DONE, stage=AnalysisJob.STAGE_COMPLETE, progress=100,
    )
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    calendar = ContentCalendar.objects.create(brand_dna=dna)
    for i in range(1, 8):
        ContentPost.objects.create(
            calendar=calendar, day_number=i, caption=f'Post {i}',
            image_url='https://example.com/img.jpg', suggested_time='19:00',
            hashtags=[], scheduled_at=timezone.now() + timedelta(days=i),
        )
    WeeklyFeedback.objects.create(calendar=calendar, week_number=1)
    return job


def test_calendar_review_exposes_pending_feedback(client, user, job_with_calendar):
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert response.status_code == 200
    assert response.context['pending_feedback'] is not None
    assert response.context['pending_feedback'].week_number == 1


def test_calendar_review_no_pending_feedback_when_none_exists(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    calendar.feedback_entries.update(continue_decision=WeeklyFeedback.CONTINUE_NO)
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert response.context['pending_feedback'] is None


def test_calendar_review_shows_feedback_banner_when_pending(client, user, job_with_calendar):
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert b'feedback-banner' in response.content
```

- [ ] **Step 2: Corre los tests, verifica que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -v -k calendar_review_exposes`
Expected: FAIL — `KeyError: 'pending_feedback'` (la vista todavía no agrega esta clave al contexto). `test_calendar_review_shows_feedback_banner_when_pending` también falla: `assert b'feedback-banner' in response.content` es `False`.

- [ ] **Step 3: Implementa el contexto y el banner**

En `core/brand_dna/views.py`, reemplaza `calendar_review_view`:

```python
@login_required
def calendar_review_view(request, job_id):
    from core.brand_dna.rate_limits import get_user_plan
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    calendar = getattr(brand_dna, 'calendar', None) if brand_dna else None
    posts = list(calendar.posts.order_by('day_number')) if calendar else []
    plan = get_user_plan(request.user)
    total_regens = sum(p.regen_count for p in posts)
    total_edits = sum(p.edit_count for p in posts)
    return render(request, 'brand_dna/calendar_review.html', {
        'job': job,
        'brand_dna': brand_dna,
        'posts': posts,
        'max_regenerations': plan.max_post_regenerations,
        'max_edits': plan.max_post_edits,
        'total_regens': total_regens,
        'total_edits': total_edits,
    })
```

por:

```python
@login_required
def calendar_review_view(request, job_id):
    from core.brand_dna.rate_limits import get_user_plan
    from core.content_pipeline.models import WeeklyFeedback
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    calendar = getattr(brand_dna, 'calendar', None) if brand_dna else None
    posts = list(calendar.posts.order_by('day_number')) if calendar else []
    plan = get_user_plan(request.user)
    total_regens = sum(p.regen_count for p in posts)
    total_edits = sum(p.edit_count for p in posts)

    pending_feedback = None
    if calendar:
        pending_feedback = calendar.feedback_entries.filter(
            continue_decision=WeeklyFeedback.CONTINUE_PENDING
        ).order_by('-week_number').first()

    return render(request, 'brand_dna/calendar_review.html', {
        'job': job,
        'brand_dna': brand_dna,
        'posts': posts,
        'max_regenerations': plan.max_post_regenerations,
        'max_edits': plan.max_post_edits,
        'total_regens': total_regens,
        'total_edits': total_edits,
        'pending_feedback': pending_feedback,
        'product_pool': job.product_image_paths,
    })
```

En `core/brand_dna/templates/brand_dna/calendar_review.html`, busca este bloque (cierre de `#calendar-limits`, justo antes de `<div class="posts-grid">`):

```html
  <div id="calendar-limits" style="background:#1a1a2e;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:0.82rem;">
    <div style="color:#aaa;margin-bottom:8px;font-size:0.78rem;text-transform:uppercase;letter-spacing:0.05em;">Límites del calendario</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <span id="regens-counter" style="display:inline-flex;align-items:center;gap:6px;background:#12122a;border:1px solid #2a2a4a;border-radius:20px;padding:4px 12px;white-space:nowrap;">
        🔄 <span>Cambios</span> <strong id="regens-used" style="color:#f0f0f0;">{{ total_regens }}</strong><span style="color:#555;">/{{ max_regenerations }}</span>
      </span>
      <span id="edits-counter" style="display:inline-flex;align-items:center;gap:6px;background:#12122a;border:1px solid #2a2a4a;border-radius:20px;padding:4px 12px;white-space:nowrap;">
        ✏️ <span>Ediciones</span> <strong id="edits-used" style="color:#f0f0f0;">{{ total_edits }}</strong><span style="color:#555;">/{{ max_edits }}</span>
      </span>
    </div>
  </div>

  <div class="posts-grid">
```

Inserta el banner de feedback entre ambos `<div>` (después del cierre de `#calendar-limits`, antes de `.posts-grid`):

```html
  <div id="calendar-limits" style="background:#1a1a2e;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:0.82rem;">
    <div style="color:#aaa;margin-bottom:8px;font-size:0.78rem;text-transform:uppercase;letter-spacing:0.05em;">Límites del calendario</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <span id="regens-counter" style="display:inline-flex;align-items:center;gap:6px;background:#12122a;border:1px solid #2a2a4a;border-radius:20px;padding:4px 12px;white-space:nowrap;">
        🔄 <span>Cambios</span> <strong id="regens-used" style="color:#f0f0f0;">{{ total_regens }}</strong><span style="color:#555;">/{{ max_regenerations }}</span>
      </span>
      <span id="edits-counter" style="display:inline-flex;align-items:center;gap:6px;background:#12122a;border:1px solid #2a2a4a;border-radius:20px;padding:4px 12px;white-space:nowrap;">
        ✏️ <span>Ediciones</span> <strong id="edits-used" style="color:#f0f0f0;">{{ total_edits }}</strong><span style="color:#555;">/{{ max_edits }}</span>
      </span>
    </div>
  </div>

  {% if pending_feedback %}
  <div id="feedback-banner" style="background:#1a1a2e;border:1px solid #e94560;border-radius:12px;padding:20px;margin-bottom:24px;">
    <h2 style="font-size:1.1rem;margin-bottom:4px;">¡Tu semana {{ pending_feedback.week_number }} terminó! 🎉</h2>
    <p style="color:#aaa;font-size:0.85rem;margin-bottom:16px;">Cuéntanos cómo te fue y decide si quieres tu próxima semana de contenido.</p>

    <div style="margin-bottom:12px;">
      <div style="font-size:0.85rem;color:#ccc;margin-bottom:6px;">¿Cómo calificarías esta semana?</div>
      <div id="star-rating" style="display:flex;gap:8px;font-size:1.6rem;cursor:pointer;">
        <span class="star" data-value="1">☆</span>
        <span class="star" data-value="2">☆</span>
        <span class="star" data-value="3">☆</span>
        <span class="star" data-value="4">☆</span>
        <span class="star" data-value="5">☆</span>
      </div>
    </div>

    <div style="margin-bottom:16px;">
      <textarea id="feedback-comment" placeholder="Comentarios (opcional)" style="width:100%;padding:8px;border-radius:6px;border:1px solid #333;background:#0d0d1a;color:#f0f0f0;font-size:0.85rem;resize:vertical;min-height:60px;"></textarea>
    </div>

    <div style="display:flex;gap:12px;flex-wrap:wrap;">
      <button id="feedback-yes-btn" onclick="showImageChoice()" style="flex:1;min-width:200px;padding:12px;background:#e94560;color:#fff;border:none;border-radius:8px;font-weight:700;cursor:pointer;">Sí, quiero mi próxima semana</button>
      <button onclick="submitFeedback('no')" style="flex:1;min-width:150px;padding:12px;background:transparent;color:#aaa;border:1px solid #333;border-radius:8px;font-weight:600;cursor:pointer;">No, por ahora</button>
    </div>

    <div id="image-choice-section" style="display:none;margin-top:20px;padding-top:20px;border-top:1px solid #2a2a4a;">
      <div style="font-size:0.85rem;color:#ccc;margin-bottom:10px;">Imágenes de producto para tu próxima semana</div>

      <label style="display:flex;align-items:center;gap:8px;font-size:0.85rem;margin-bottom:8px;cursor:pointer;">
        <input type="radio" name="image_choice" value="reuse" checked onchange="toggleImageMode()"> Reutilizar mis imágenes
      </label>
      <label style="display:flex;align-items:center;gap:8px;font-size:0.85rem;margin-bottom:12px;cursor:pointer;">
        <input type="radio" name="image_choice" value="new" onchange="toggleImageMode()"> Subir nuevas imágenes
      </label>

      {% if product_pool|length > 7 %}
      <div id="image-gallery" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(70px,1fr));gap:8px;margin-bottom:12px;">
        {% for img in product_pool %}
        <label style="position:relative;cursor:pointer;">
          <input type="checkbox" class="gallery-checkbox" value="{{ img }}" style="position:absolute;top:4px;left:4px;z-index:1;">
          <img src="/media/{{ img }}" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px;border:2px solid transparent;" loading="lazy">
        </label>
        {% endfor %}
      </div>
      {% endif %}

      <div id="image-upload-section" style="display:none;">
        <input type="file" id="product-images-input" multiple accept="image/jpeg,image/png" style="font-size:0.82rem;color:#ccc;">
        <p style="font-size:0.75rem;color:#666;margin-top:6px;">Hasta 7 imágenes.</p>
      </div>

      <button onclick="submitFeedback('yes')" style="width:100%;margin-top:16px;padding:12px;background:#e94560;color:#fff;border:none;border-radius:8px;font-weight:700;cursor:pointer;">Generar mi semana →</button>
    </div>
  </div>
  {% endif %}

  <div class="posts-grid">
```

Nota: los botones llaman a `showImageChoice()`, `toggleImageMode()` y `submitFeedback(decision)` — estas funciones se implementan en la **Tarea 10**. Hasta entonces, los clics en el banner no hacen nada (no rompen la página: son `onclick` que apuntan a funciones aún no definidas, pero el HTML/CSS ya es válido y los tests de esta tarea no interactúan con JS).

- [ ] **Step 4: Corre los tests, verifica que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/templates/brand_dna/calendar_review.html core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat: mostrar banner de encuesta semanal en calendar_review cuando hay feedback pendiente"
```

---

## Task 9: Endpoint `calendar_feedback_api` + `_update_active_product_images`

**Files:**
- Modify: `core/brand_dna/views.py`
- Modify: `core/brand_dna/urls.py`
- Test: `core/brand_dna/tests/test_views.py`

- [ ] **Step 1: Escribe los tests que fallan**

Agrega `import os` al inicio de `core/brand_dna/tests/test_views.py` (si no está ya — el archivo no lo usa todavía):

```python
import pytest
import json
import os
from unittest.mock import patch
from django.test import Client
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback

pytestmark = pytest.mark.django_db
```

Agrega al final del archivo estos 5 tests:

```python
def test_calendar_feedback_api_no_decision_does_not_generate(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    with patch('core.content_pipeline.tasks.generate_next_week') as mock_gen:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '3',
            'comment': 'Estuvo bien',
            'continue_decision': 'no',
        })
    assert response.status_code == 200
    data = response.json()
    assert data['continue_decision'] == 'no'

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.rating == 3
    assert feedback.comment == 'Estuvo bien'
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_NO
    assert feedback.responded_at is not None
    mock_gen.assert_not_called()


def test_calendar_feedback_api_yes_triggers_generate_next_week(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    with patch('core.content_pipeline.tasks.generate_next_week') as mock_gen, \
         patch('core.brand_dna.views._update_active_product_images') as mock_update:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '5',
            'comment': '',
            'continue_decision': 'yes',
            'image_choice': 'reuse',
        })
    assert response.status_code == 200
    data = response.json()
    assert data['continue_decision'] == 'yes'

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_YES
    mock_update.assert_called_once()
    mock_gen.assert_called_once()


def test_calendar_feedback_api_requires_ownership(client, django_user_model, job_with_calendar):
    other_user = django_user_model.objects.create_user(
        username='other@test.com', email='other@test.com', password='pass1234'
    )
    client.force_login(other_user)
    response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
        'rating': '4',
        'continue_decision': 'no',
    })
    assert response.status_code == 404


def test_update_active_product_images_reuse_pool_le_7(job_with_calendar):
    from core.brand_dna.views import _update_active_product_images
    from django.test import RequestFactory

    job = job_with_calendar
    job.product_image_paths = ['uploads/p1.jpg', 'uploads/p2.jpg']
    job.save(update_fields=['product_image_paths'])
    calendar = job.brand_dna.calendar
    calendar.active_product_images = ['uploads/p1.jpg', 'uploads/p2.jpg']
    calendar.save(update_fields=['active_product_images'])

    request = RequestFactory().post('/', {'image_choice': 'reuse'})
    _update_active_product_images(calendar, job, request, next_week=2)

    calendar.refresh_from_db()
    assert calendar.active_product_images == ['uploads/p1.jpg', 'uploads/p2.jpg']


def test_update_active_product_images_reuse_pool_gt_7_with_selection(job_with_calendar):
    from core.brand_dna.views import _update_active_product_images
    from django.test import RequestFactory

    job = job_with_calendar
    pool = [f'uploads/p{i}.jpg' for i in range(1, 9)]  # 8 imágenes
    job.product_image_paths = pool
    job.save(update_fields=['product_image_paths'])
    calendar = job.brand_dna.calendar

    selected = pool[:5]
    request = RequestFactory().post('/', {
        'image_choice': 'reuse',
        'selected_images': selected,
    })
    _update_active_product_images(calendar, job, request, next_week=2)

    calendar.refresh_from_db()
    assert calendar.active_product_images == selected


def test_update_active_product_images_new_uploads(job_with_calendar, tmp_path, settings):
    from core.brand_dna.views import _update_active_product_images
    from django.test import RequestFactory
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.MEDIA_ROOT = str(tmp_path)
    job = job_with_calendar
    calendar = job.brand_dna.calendar

    image1 = SimpleUploadedFile('product1.jpg', b'fake-bytes-1', content_type='image/jpeg')
    image2 = SimpleUploadedFile('product2.png', b'fake-bytes-2', content_type='image/png')

    request = RequestFactory().post('/', {
        'image_choice': 'new',
        'product_images': [image1, image2],
    })
    _update_active_product_images(calendar, job, request, next_week=2)

    job.refresh_from_db()
    calendar.refresh_from_db()

    assert len(job.product_image_paths) == 2
    assert calendar.active_product_images == job.product_image_paths
    for path in calendar.active_product_images:
        full = os.path.join(settings.MEDIA_ROOT, path)
        assert os.path.exists(full)
```

- [ ] **Step 2: Corre los tests, verifica que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -v -k "feedback_api or active_product_images"`
Expected: FAIL —
- Los 3 tests de `calendar_feedback_api` fallan con `assert response.status_code == 200` (la URL no existe todavía, Django responde 404) — excepto `test_calendar_feedback_api_requires_ownership`, que espera 404 y "pasa" trivialmente por la razón equivocada (se corregirá al implementar, donde seguirá esperando 404 pero por ownership).
- Los 3 tests de `_update_active_product_images` fallan con `ImportError: cannot import name '_update_active_product_images' from 'core.brand_dna.views'`

- [ ] **Step 3: Implementa la vista, el helper y la ruta**

En `core/brand_dna/views.py`, agrega al final del archivo (después de `_regenerate_caption`):

```python
@login_required
@require_POST
def calendar_feedback_api(request, job_id):
    from django.utils import timezone
    from core.content_pipeline.models import WeeklyFeedback
    from core.content_pipeline.tasks import generate_next_week

    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    calendar = job.brand_dna.calendar
    feedback = get_object_or_404(
        WeeklyFeedback, calendar=calendar, continue_decision=WeeklyFeedback.CONTINUE_PENDING
    )

    feedback.rating = int(request.POST.get('rating'))
    feedback.comment = request.POST.get('comment', '')
    feedback.continue_decision = request.POST.get('continue_decision')
    feedback.responded_at = timezone.now()
    feedback.save(update_fields=['rating', 'comment', 'continue_decision', 'responded_at'])

    if feedback.continue_decision == WeeklyFeedback.CONTINUE_YES:
        next_week = feedback.week_number + 1
        _update_active_product_images(calendar, job, request, next_week)
        generate_next_week(calendar, next_week)

    return JsonResponse({'status': 'ok', 'continue_decision': feedback.continue_decision})


def _update_active_product_images(calendar, job, request, next_week):
    choice = request.POST.get('image_choice', 'reuse')
    if choice == 'new':
        files = request.FILES.getlist('product_images')[:7]
        new_paths = []
        for idx, f in enumerate(files):
            ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else 'jpg'
            path = f'uploads/product_{job.id}_w{next_week}_{idx}.{ext}'
            full = os.path.join(settings.MEDIA_ROOT, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'wb') as out:
                for chunk in f.chunks():
                    out.write(chunk)
            new_paths.append(path)
        if new_paths:
            job.product_image_paths = job.product_image_paths + new_paths
            job.save(update_fields=['product_image_paths'])
            calendar.active_product_images = new_paths
            calendar.save(update_fields=['active_product_images'])
    elif choice == 'reuse':
        pool = job.product_image_paths
        if len(pool) > 7:
            selected = request.POST.getlist('selected_images')[:7]
            valid = [p for p in selected if p in pool]
            if valid:
                calendar.active_product_images = valid
                calendar.save(update_fields=['active_product_images'])
```

En `core/brand_dna/urls.py`, agrega la ruta nueva después de `post_action_api`:

```python
urlpatterns = [
    path('', views.landing, name='landing'),
    path('favicon.svg', views.favicon, name='favicon'),
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
    path('calendar/<uuid:job_id>/', views.calendar_review_view, name='calendar_review'),
    path('api/post/<uuid:post_id>/action/', views.post_action_api, name='post_action_api'),
    path('api/calendar/<uuid:job_id>/delete/', views.delete_calendar_api, name='delete_calendar_api'),
    path('api/calendar/<uuid:job_id>/feedback/', views.calendar_feedback_api, name='calendar_feedback_api'),
]
```

- [ ] **Step 4: Corre los tests, verifica que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/urls.py core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat: agregar endpoint calendar_feedback_api y _update_active_product_images"
```

---

## Task 10: JS de la encuesta semanal en `calendar_review.html`

**Files:**
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html`

Esta tarea no agrega lógica de backend — solo implementa las funciones JS (`showImageChoice`, `toggleImageMode`, `submitFeedback`, rating de estrellas) que el banner de la Tarea 8 ya referencia vía `onclick`. No hay tests de Python; la verificación es manual en el navegador.

- [ ] **Step 1: Implementa las funciones JS del banner**

En `core/brand_dna/templates/brand_dna/calendar_review.html`, dentro del `<script>` final, el bloque actual termina así:

```html
  async function deleteCalendar(jobId) {
    if (!confirm('¿Eliminar este calendario? Se borrarán todos los posts. Esta acción no se puede deshacer.')) return;
    const res = await fetch(`/api/calendar/${jobId}/delete/`, {
      method: 'POST',
      headers: { 'X-CSRFToken': CSRF },
    });
    const data = await res.json();
    if (data.status === 'ok') {
      showToast('Calendario eliminado', '#e74c3c');
      setTimeout(() => window.location.href = '/dashboard/', 1500);
    } else {
      showToast(data.error || 'Error al eliminar', '#e74c3c');
    }
  }
</script>
```

Reemplázalo por (agrega las funciones de la encuesta semanal después de `deleteCalendar`, antes de `</script>`):

```html
  async function deleteCalendar(jobId) {
    if (!confirm('¿Eliminar este calendario? Se borrarán todos los posts. Esta acción no se puede deshacer.')) return;
    const res = await fetch(`/api/calendar/${jobId}/delete/`, {
      method: 'POST',
      headers: { 'X-CSRFToken': CSRF },
    });
    const data = await res.json();
    if (data.status === 'ok') {
      showToast('Calendario eliminado', '#e74c3c');
      setTimeout(() => window.location.href = '/dashboard/', 1500);
    } else {
      showToast(data.error || 'Error al eliminar', '#e74c3c');
    }
  }

  let selectedRating = 0;

  document.querySelectorAll('#star-rating .star').forEach(star => {
    star.addEventListener('click', () => {
      selectedRating = parseInt(star.dataset.value);
      document.querySelectorAll('#star-rating .star').forEach(s => {
        s.textContent = parseInt(s.dataset.value) <= selectedRating ? '★' : '☆';
      });
    });
  });

  function showImageChoice() {
    if (!selectedRating) { showToast('Selecciona una calificación primero', '#f0c040'); return; }
    document.getElementById('image-choice-section').style.display = 'block';
    document.getElementById('feedback-yes-btn').style.display = 'none';
  }

  function toggleImageMode() {
    const mode = document.querySelector('input[name="image_choice"]:checked').value;
    const gallery = document.getElementById('image-gallery');
    const upload = document.getElementById('image-upload-section');
    if (mode === 'new') {
      if (gallery) gallery.style.display = 'none';
      upload.style.display = 'block';
    } else {
      if (gallery) gallery.style.display = 'grid';
      upload.style.display = 'none';
    }
  }

  document.querySelectorAll('.gallery-checkbox').forEach(cb => {
    cb.addEventListener('change', () => {
      const checked = document.querySelectorAll('.gallery-checkbox:checked');
      if (checked.length > 7) {
        cb.checked = false;
        showToast('Máximo 7 imágenes', '#f0c040');
      }
    });
  });

  async function submitFeedback(decision) {
    if (!selectedRating) { showToast('Selecciona una calificación primero', '#f0c040'); return; }

    const formData = new FormData();
    formData.append('rating', selectedRating);
    formData.append('comment', document.getElementById('feedback-comment').value);
    formData.append('continue_decision', decision);

    if (decision === 'yes') {
      const mode = document.querySelector('input[name="image_choice"]:checked').value;
      formData.append('image_choice', mode);
      if (mode === 'reuse') {
        document.querySelectorAll('.gallery-checkbox:checked').forEach(cb => {
          formData.append('selected_images', cb.value);
        });
      } else if (mode === 'new') {
        const files = document.getElementById('product-images-input').files;
        for (let i = 0; i < Math.min(files.length, 7); i++) {
          formData.append('product_images', files[i]);
        }
      }
    }

    const res = await fetch('/api/calendar/{{ job.id }}/feedback/', {
      method: 'POST',
      headers: { 'X-CSRFToken': CSRF },
      body: formData,
    });
    const data = await res.json();
    if (data.status === 'ok') {
      if (data.continue_decision === 'yes') {
        showToast('¡Gracias! Tu próxima semana está en camino 🚀');
      } else {
        showToast('Gracias por tu feedback');
      }
      document.getElementById('feedback-banner').remove();
    } else {
      showToast('Error al enviar feedback', '#e74c3c', 5000);
    }
  }
</script>
```

Notas de implementación:
- `selectedRating` se valida en `showImageChoice()` y en `submitFeedback()` — ambos flujos ("Sí" y "No") requieren rating, según la Sección 4 del spec ("Rating 1-5 ... requerido").
- `document.querySelectorAll('#star-rating .star')` y `.gallery-checkbox` devuelven `NodeList` vacíos si `pending_feedback` es `None` (el banner no se renderiza) — `.forEach` sobre una lista vacía no genera errores, así que el script es seguro en páginas sin encuesta pendiente.
- `toggleImageMode()` usa `if (gallery)` porque `#image-gallery` solo existe en el DOM cuando `product_pool|length > 7` (ver Tarea 8).

- [ ] **Step 2: Verificación manual en el navegador**

Esta tarea no tiene tests de Python — verifica el golden path manualmente.

1. Crea datos de prueba con un `WeeklyFeedback` pendiente (semana 1, calendario completo de 7 posts):

```bash
docker compose exec backend python manage.py shell -c "
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback

User = get_user_model()
user, _ = User.objects.get_or_create(username='manualtest@test.com', defaults={'email': 'manualtest@test.com'})
user.set_password('pass1234')
user.save()

job = AnalysisJob.objects.create(email=user.email, business_url='https://tuwebmx.com', user=user, status=AnalysisJob.STATUS_DONE, stage=AnalysisJob.STAGE_COMPLETE, progress=100)
dna = BrandDNA.objects.create(job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com', description='Agencia digital', keywords=['diseno'], audience='PYMEs', tone='profesional', primary_colors=['#1a1a2e'])
calendar = ContentCalendar.objects.create(brand_dna=dna)
for i in range(1, 8):
    ContentPost.objects.create(calendar=calendar, day_number=i, caption=f'Post {i}', image_url='https://example.com/img.jpg', suggested_time='19:00', hashtags=[], scheduled_at=timezone.now() + timedelta(days=i))
WeeklyFeedback.objects.create(calendar=calendar, week_number=1)
print('job_id:', job.id)
"
```

Copia el `job_id` impreso.

2. Abre `http://localhost:3002/auth/login/` e inicia sesión con `manualtest@test.com` / `pass1234`.
3. Navega a `http://localhost:3002/calendar/<job_id>/`. Verifica que aparece el banner "¡Tu semana 1 terminó! 🎉".
4. Haz clic en la 4ª estrella — las primeras 4 deben mostrarse como `★` y la 5ª como `☆`.
5. Haz clic en "No, por ahora" — el banner debe desaparecer y debe mostrarse el toast "Gracias por tu feedback". Confirma en la base de datos:

```bash
docker compose exec backend python manage.py shell -c "
from core.content_pipeline.models import WeeklyFeedback
fb = WeeklyFeedback.objects.get(calendar__brand_dna__business_name='Tu Web MX', week_number=1)
print(fb.continue_decision, fb.rating, fb.responded_at)
"
```

Expected: `no 4 <datetime>`.

6. Para probar el flujo "Sí", crea un segundo `WeeklyFeedback` pendiente sobre el mismo calendario (semana distinta, para no chocar con `unique_together`):

```bash
docker compose exec backend python manage.py shell -c "
from core.content_pipeline.models import ContentCalendar, WeeklyFeedback
calendar = ContentCalendar.objects.get(brand_dna__business_name='Tu Web MX')
WeeklyFeedback.objects.create(calendar=calendar, week_number=99)
"
```

   Recarga `http://localhost:3002/calendar/<job_id>/`:
   - Selecciona una calificación, haz clic en "Sí, quiero mi próxima semana" → debe aparecer "Imágenes de producto para tu próxima semana" y ocultarse el botón "Sí".
   - Cambia el radio a "Subir nuevas imágenes" → se oculta la galería (si existía) y aparece el input de archivos; vuelve a "Reutilizar mis imágenes" → se revierte.
   - Haz clic en "Generar mi semana →" → debe mostrarse el toast "¡Gracias! Tu próxima semana está en camino 🚀" y el banner debe desaparecer (esto ejecuta `generate_next_week` de forma síncrona — llama a Gemini vía `TextGenerator`, puede tardar unos segundos).

7. Limpieza — borra los datos de prueba:

```bash
docker compose exec backend python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
User.objects.filter(username='manualtest@test.com').delete()
"
```

Expected: todos los pasos anteriores se comportan como se describe; sin errores en la consola del navegador (F12 → Console).

- [ ] **Step 3: Commit**

```bash
git add core/brand_dna/templates/brand_dna/calendar_review.html
GIT_EDITOR=true git commit -m "feat: agregar JS de encuesta semanal (rating, eleccion de imagenes, submit)"
```

---
