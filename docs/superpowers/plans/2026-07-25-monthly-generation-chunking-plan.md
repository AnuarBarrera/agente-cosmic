# Generación mensual encadenada por semanas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el job monolítico `generate_next_month` (que falló por timeout en su primera prueba real) por una cadena de jobs de RQ encadenados por semana, cada uno con su propio timeout chico, para que ningún fallo se lleve el mes completo y el usuario reciba contenido progresivamente.

**Architecture:** `generate_next_month` pasa a hacer solo la Fase 1 (texto: crea los 28 `ContentPost` sin imagen). Al terminar, encola los 7 `backfill_image_task` de la semana 1 (ya existe, ya idempotente, reusado tal cual) más un job de cierre que depende de esos 7 vía `Dependency` nativo de RQ. El cierre manda el correo correspondiente y encola la siguiente semana, hasta cerrar el mes.

**Tech Stack:** Django, RQ 2.4.0 (`django_rq`, `rq.Retry`, `rq.job.Dependency`), Redis.

## Global Constraints

- Granularidad de trabajo: 1 job de RQ por post (`backfill_image_task`), nunca un loop de 7 dentro de un mismo job.
- Encadenado semana a semana vía `Dependency(jobs=[...7...], allow_failure=True)` — la semana avanza aunque algún post individual haya fallado de forma permanente.
- Reintentos automáticos por job de imagen/reel: `Retry(max=3, interval=[10, 20, 40])`.
- Timeout por job: **300s** para posts `single`/`carousel`, **600s** para el post `reel` de la semana.
- Job de cierre de semana: `job_timeout=120`, `Retry(max=2, interval=[10, 30])`.
- Cadencia de correos: exactamente 2 por mes — semana 1 lista (`send_week_ready`, nuevo) y mes completo (`send_month_ready`, ya existe, sin tocar su copy). Semanas 2 y 3 avanzan en silencio, sin correo.
- El banner del dashboard/calendar_review NO cambia — sigue usando `next_week_generating` como único booleano, sin rastrear progreso por semana.
- La portada del reel sigue siendo un frame real del video (no se genera aparte) — decisión explícita, ver spec sección "Decisiones confirmadas" #8.
- `next_week_generating` se resetea a `False` únicamente: (a) si la Fase 1 (texto) falla, o (b) al cerrar la semana 4 (mes completo), o (c) si el propio job de cierre de semana falla de forma inesperada. Nunca en el camino exitoso de las semanas 1-3.
- El webhook de Stripe (`core/brand_dna/stripe_views.py`) sigue llamando `generate_next_month(calendar_id)` sin cambios de lógica — solo cambia el `job_timeout` del enqueue, de `2400` a `900`.

---

### Task 1: `_enqueue_week_images` + `_week_closing_task` — el motor de encadenado

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Test: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: `backfill_image_task(post_id: str) -> None` (ya existe, sin cambios, línea
  ~222 de `tasks.py`); `ContentPost.FORMAT_REEL` (constante de
  `core/content_pipeline/models.py`); `EmailSender().send_week_ready(job, brand_dna)`
  y `EmailSender().send_month_ready(job, brand_dna)` (el segundo ya existe; el primero
  se implementa en el Task 2 de este plan — aquí solo se llama, mockeado en los tests
  de este task).
- Produces: `_enqueue_week_images(calendar_id: str, week_index: int) -> None` y
  `_week_closing_task(calendar_id: str, week_index: int) -> None` — el Task 3 (rewrite
  de `generate_next_month`) llama a `_enqueue_week_images(calendar_id, week_index=0)`.

- [ ] **Step 1: Agregar imports nuevos al inicio de `tasks.py`**

En `core/content_pipeline/tasks.py`, después de la línea 5 (`from django.utils import
timezone`), agregar:

```python
import django_rq
from rq import Retry
from rq.job import Dependency
```

- [ ] **Step 2: Escribir los tests que fallan**

Agregar al final de `core/content_pipeline/tests/test_tasks.py` (usa la fixture
`job_with_dna` ya existente en el archivo):

```python
def _make_calendar_with_month(job_with_dna, reel_day_number=None):
    """Crea un calendar con 7 posts de trial + 28 del mes, todos sin imagen (image_url='')."""
    from core.content_pipeline.models import ContentCalendar, ContentPost
    calendar = ContentCalendar.objects.create(brand_dna=job_with_dna.brand_dna)
    for day in range(1, 36):
        fmt = ContentPost.FORMAT_REEL if day == reel_day_number else ContentPost.FORMAT_SINGLE
        ContentPost.objects.create(
            calendar=calendar, day_number=day, caption=f'Post {day}',
            image_url='', image_urls=[], video_url='', format=fmt,
            suggested_time='19:00', hashtags=[],
            scheduled_at=timezone.now() + timedelta(days=day),
        )
    return calendar


def test_enqueue_week_images_enqueues_7_jobs_plus_closing(job_with_dna):
    from core.content_pipeline.tasks import _enqueue_week_images
    calendar = _make_calendar_with_month(job_with_dna)
    with patch('core.content_pipeline.tasks.django_rq') as mock_rq:
        mock_rq.enqueue.side_effect = lambda *a, **kw: MagicMock()
        _enqueue_week_images(str(calendar.id), week_index=0)
    assert mock_rq.enqueue.call_count == 8  # 7 backfill_image_task + 1 _week_closing_task
    closing_call = mock_rq.enqueue.call_args_list[-1]
    assert closing_call.kwargs['job_timeout'] == 120
    dependency = closing_call.kwargs['depends_on']
    assert isinstance(dependency, Dependency)
    assert len(dependency.dependencies) == 7
    assert dependency.allow_failure is True


def test_enqueue_week_images_uses_longer_timeout_for_reel(job_with_dna):
    from core.content_pipeline.tasks import _enqueue_week_images
    calendar = _make_calendar_with_month(job_with_dna, reel_day_number=1)
    with patch('core.content_pipeline.tasks.django_rq') as mock_rq:
        mock_rq.enqueue.side_effect = lambda *a, **kw: MagicMock()
        _enqueue_week_images(str(calendar.id), week_index=0)
    backfill_calls = mock_rq.enqueue.call_args_list[:7]
    timeouts_by_post_id = {call.args[1]: call.kwargs['job_timeout'] for call in backfill_calls}
    reel_post = calendar.posts.get(day_number=1)
    single_post = calendar.posts.get(day_number=2)
    assert timeouts_by_post_id[str(reel_post.id)] == 600
    assert timeouts_by_post_id[str(single_post.id)] == 300


def test_enqueue_week_images_selects_correct_day_range_for_week_index(job_with_dna):
    from core.content_pipeline.tasks import _enqueue_week_images
    calendar = _make_calendar_with_month(job_with_dna)
    with patch('core.content_pipeline.tasks.django_rq') as mock_rq:
        mock_rq.enqueue.side_effect = lambda *a, **kw: MagicMock()
        _enqueue_week_images(str(calendar.id), week_index=2)  # dias 22-28
    backfill_post_ids = {call.args[1] for call in mock_rq.enqueue.call_args_list[:7]}
    expected_ids = {str(p.id) for p in calendar.posts.filter(day_number__gte=22, day_number__lte=28)}
    assert backfill_post_ids == expected_ids


def test_week_closing_task_week_0_sends_week_ready_and_advances(job_with_dna):
    from core.content_pipeline.tasks import _week_closing_task
    calendar = _make_calendar_with_month(job_with_dna)
    calendar.next_week_generating = True
    calendar.save(update_fields=['next_week_generating'])
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue:
        _week_closing_task(str(calendar.id), week_index=0)
    MockEmail.return_value.send_week_ready.assert_called_once()
    MockEmail.return_value.send_month_ready.assert_not_called()
    mock_enqueue.assert_called_once_with(str(calendar.id), 1)
    calendar.refresh_from_db()
    assert calendar.next_week_generating is True


def test_week_closing_task_middle_weeks_silent(job_with_dna):
    from core.content_pipeline.tasks import _week_closing_task
    calendar = _make_calendar_with_month(job_with_dna)
    calendar.next_week_generating = True
    calendar.save(update_fields=['next_week_generating'])
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue:
        _week_closing_task(str(calendar.id), week_index=1)
    MockEmail.return_value.send_week_ready.assert_not_called()
    MockEmail.return_value.send_month_ready.assert_not_called()
    mock_enqueue.assert_called_once_with(str(calendar.id), 2)
    calendar.refresh_from_db()
    assert calendar.next_week_generating is True


def test_week_closing_task_week_3_sends_month_ready_and_resets_flag(job_with_dna):
    from core.content_pipeline.tasks import _week_closing_task
    calendar = _make_calendar_with_month(job_with_dna)
    calendar.next_week_generating = True
    calendar.save(update_fields=['next_week_generating'])
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue:
        _week_closing_task(str(calendar.id), week_index=3)
    MockEmail.return_value.send_month_ready.assert_called_once()
    MockEmail.return_value.send_week_ready.assert_not_called()
    mock_enqueue.assert_not_called()
    calendar.refresh_from_db()
    assert calendar.next_week_generating is False


def test_week_closing_task_advances_despite_partial_failure_is_implicit_in_dependency(job_with_dna):
    """No hay logica propia de _week_closing_task para fallos parciales — RQ ya
    dispara el job aunque algun dependiente haya fallado (allow_failure=True, probado
    en test_enqueue_week_images_enqueues_7_jobs_plus_closing). Este test solo confirma
    que _week_closing_task no revisa el estado de los 7 posts antes de avanzar."""
    from core.content_pipeline.tasks import _week_closing_task
    calendar = _make_calendar_with_month(job_with_dna)
    calendar.next_week_generating = True
    calendar.save(update_fields=['next_week_generating'])
    # Ningun post de esta semana tiene imagen (todos image_url='') — si _week_closing_task
    # revisara el estado, se bloquearia. Debe avanzar de todos modos.
    with patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue:
        _week_closing_task(str(calendar.id), week_index=0)
    mock_enqueue.assert_called_once_with(str(calendar.id), 1)


def test_week_closing_task_resets_flag_on_internal_error(job_with_dna):
    from core.content_pipeline.tasks import _week_closing_task
    calendar = _make_calendar_with_month(job_with_dna)
    calendar.next_week_generating = True
    calendar.save(update_fields=['next_week_generating'])
    with patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks._enqueue_week_images', side_effect=Exception('redis down')):
        _week_closing_task(str(calendar.id), week_index=0)
    calendar.refresh_from_db()
    assert calendar.next_week_generating is False
```

Agregar `from unittest.mock import MagicMock` al bloque de imports de
`test_tasks.py` si no está ya (revisar el archivo — probablemente ya importa
`patch` de `unittest.mock`, solo falta `MagicMock`).

- [ ] **Step 3: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py::test_enqueue_week_images_enqueues_7_jobs_plus_closing -v`
Expected: FAIL — `_enqueue_week_images` no existe todavía.

- [ ] **Step 4: Implementar `_enqueue_week_images` y `_week_closing_task`**

En `core/content_pipeline/tasks.py`, agregar estas dos funciones **justo antes** de
`def generate_next_month(calendar_id: str) -> None:` (que se reescribe en el Task 3 de
este plan — por ahora la función vieja se queda intacta, estas funciones nuevas van
antes de ella):

```python
def _enqueue_week_images(calendar_id: str, week_index: int) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    base_day = calendar.posts.count() - 28
    week_start = base_day + (week_index * 7) + 1
    week_end = week_start + 6
    week_posts = list(
        calendar.posts.filter(day_number__gte=week_start, day_number__lte=week_end).order_by('day_number')
    )
    jobs = []
    for post in week_posts:
        timeout = 600 if post.format == ContentPost.FORMAT_REEL else 300
        job = django_rq.enqueue(
            backfill_image_task, str(post.id),
            job_timeout=timeout,
            retry=Retry(max=3, interval=[10, 20, 40]),
        )
        jobs.append(job)
    django_rq.enqueue(
        _week_closing_task, calendar_id, week_index,
        job_timeout=120,
        retry=Retry(max=2, interval=[10, 30]),
        depends_on=Dependency(jobs=jobs, allow_failure=True),
    )


def _week_closing_task(calendar_id: str, week_index: int) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    brand_dna = calendar.brand_dna
    try:
        if week_index == 0:
            try:
                EmailSender().send_week_ready(job=brand_dna.job, brand_dna=brand_dna)
            except Exception as email_err:
                logger.error(f"Email de semana 1 lista falló para calendar {calendar_id} (no fatal): {email_err}")
        if week_index < 3:
            _enqueue_week_images(calendar_id, week_index + 1)
        else:
            try:
                EmailSender().send_month_ready(job=brand_dna.job, brand_dna=brand_dna)
            except Exception as email_err:
                logger.error(f"Email de mes listo falló para calendar {calendar_id} (no fatal): {email_err}")
            calendar.next_week_generating = False
            calendar.save(update_fields=['next_week_generating'])
    except Exception as e:
        logger.error(f"_week_closing_task error para calendar {calendar_id}, semana {week_index}: {e}")
        calendar.next_week_generating = False
        calendar.save(update_fields=['next_week_generating'])
```

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v -k "enqueue_week_images or week_closing_task"`
Expected: PASS — los 7 tests nuevos de este task.

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
GIT_EDITOR=true git commit -m "feat(monthly-chunking): motor de encadenado por semana (_enqueue_week_images + _week_closing_task)"
```

---

### Task 2: `send_week_ready` — correo nuevo de "semana 1 lista"

**Files:**
- Modify: `core/content_pipeline/email_sender.py`
- Create: `core/content_pipeline/templates/content_pipeline/email_week_ready.html`
- Test: `core/content_pipeline/tests/test_email_sender.py`

**Interfaces:**
- Consumes: nada nuevo — mismo patrón que `send_month_ready` (`render_to_string`,
  `send_mail`, `EMAILS_SENT`, `reverse('calendar_review', ...)`, ya importados en
  `email_sender.py`).
- Produces: `EmailSender.send_week_ready(self, job: AnalysisJob, brand_dna: BrandDNA) -> None`
  — consumido por `_week_closing_task` (Task 1 de este plan, ya escrito y llamándolo).

- [ ] **Step 1: Escribir el test que falla**

Agregar en `core/content_pipeline/tests/test_email_sender.py`, cerca de
`test_send_month_ready_email_calls_django_send` (línea ~146, mismo patrón exacto):

```python
def test_send_week_ready_email_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_week_ready(job=job, brand_dna=dna)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    subject = mock_send.call_args[0][0]
    assert 'Tu Web MX' in subject
    assert 'semana' in subject.lower()
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py::test_send_week_ready_email_calls_django_send -v`
Expected: FAIL — `AttributeError: 'EmailSender' object has no attribute 'send_week_ready'`.

- [ ] **Step 3: Implementar el método**

En `core/content_pipeline/email_sender.py`, agregar este método **justo después** de
`send_month_ready` (después de la línea `logger.info(f"Email de mes listo enviado a
{job.email} para job {job.id}")`, antes de `def send_daily(self, post: ContentPost)`):

```python
    def send_week_ready(self, job: AnalysisJob, brand_dna: BrandDNA) -> None:
        calendar_url = settings.COSMIC_BASE_URL + reverse('calendar_review', args=[job.id])
        html = render_to_string('content_pipeline/email_week_ready.html', {
            'brand_dna': brand_dna,
            'calendar_url': calendar_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'🎉 Tu primera semana de contenido ya está lista — {name}' if name else '🎉 Tu primera semana de contenido ya está lista — Agente Cosmic'
        plain = (
            f'Tu primera semana de contenido de {name} ya está lista. Seguimos generando el resto del mes en segundo plano.'
            if name else
            'Tu primera semana de contenido ya está lista. Seguimos generando el resto del mes en segundo plano.'
        )
        send_mail(
            subject, plain, settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email], html_message=html, fail_silently=False,
        )
        EMAILS_SENT.labels(type='week_ready').inc()
        logger.info(f"Email de semana 1 lista enviado a {job.email} para job {job.id}")
```

- [ ] **Step 4: Crear el template**

Crear `core/content_pipeline/templates/content_pipeline/email_week_ready.html`:

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Tu primera semana está lista — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 580px; margin: 0 auto; padding: 24px 20px; color: #333;">

  <p style="margin: 0 0 16px; font-size: 0.72rem; color: #999; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">📬 Notificación automática</p>

  <p style="font-size: 1.1rem; margin-bottom: 8px;">Hola,</p>
  <p>Tu primera semana de contenido para <strong>{{ brand_dna.business_name }}</strong> ya está lista.</p>

  <p>Puedes revisarla y empezar a descargar desde la plataforma:</p>

  <div style="text-align: center; margin: 28px 0;">
    <a href="{{ calendar_url }}"
       style="display: inline-block; padding: 14px 32px; background: #e94560; color: #fff;
              border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 1rem;">
      Ver mi calendario →
    </a>
  </div>

  <p style="color: #555;">Seguimos generando el resto de tu mes en segundo plano — te avisaremos por correo apenas esté completo.</p>

  <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">
  <p style="font-size: 11px; color: #bbb; margin: 0;">Agente Cosmic — Powered by Google Cloud</p>

</body>
</html>
```

- [ ] **Step 5: Correr el test para confirmar que pasa**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py -v`
Expected: PASS — todos, incluyendo el nuevo.

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/email_sender.py core/content_pipeline/templates/content_pipeline/email_week_ready.html core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "feat(monthly-chunking): correo send_week_ready para el cierre de la semana 1"
```

---

### Task 3: Reescribir `generate_next_month` — Fase 1 (solo texto)

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Test: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: `_enqueue_week_images(calendar_id: str, week_index: int) -> None` (Task 1
  de este plan, ya implementado).
- Produces: `generate_next_month(calendar_id: str) -> None` con comportamiento nuevo —
  consumido por el webhook de Stripe (Task 4 de este plan, sin cambios de firma).

- [ ] **Step 1: Escribir los tests que fallan**

En `core/content_pipeline/tests/test_tasks.py`, **reemplazar completamente** las 3
funciones existentes `test_generate_next_month_creates_28_posts` (línea 518),
`test_generate_next_month_sends_month_ready_email` (línea 547, con su decorador
`@override_settings` de arriba) y `test_generate_next_month_resets_flag_even_on_failure`
(línea 573, con su decorador) por estas 3:

```python
def test_generate_next_month_creates_28_posts_without_images(job_with_dna):
    from core.content_pipeline.tasks import content_generation_task, generate_next_month
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue_week:
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        content_generation_task(str(job_with_dna.id))

        calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
        generate_next_month(str(calendar.id))

    posts = ContentPost.objects.filter(calendar=calendar).order_by('day_number')
    assert posts.count() == 35  # 7 del trial + 28 del mes
    day_numbers = list(posts.values_list('day_number', flat=True))
    assert day_numbers == list(range(1, 36))
    assert MockText.return_value.generate.call_count == 5  # 1 del trial + 4 del mes
    new_posts = posts.filter(day_number__gte=8)
    assert all(p.image_url == '' for p in new_posts)
    assert all(p.image_urls == [] for p in new_posts)
    assert all(p.video_url == '' for p in new_posts)
    mock_enqueue_week.assert_called_once_with(str(calendar.id), week_index=0)


def test_generate_next_month_resets_flag_on_text_failure(job_with_dna):
    from core.content_pipeline.tasks import content_generation_task, generate_next_month
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        content_generation_task(str(job_with_dna.id))
        calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
        calendar.next_week_generating = True
        calendar.save(update_fields=['next_week_generating'])

        MockText.return_value.generate.side_effect = Exception('Gemini error')
        generate_next_month(str(calendar.id))

    calendar.refresh_from_db()
    assert calendar.next_week_generating is False


def test_generate_next_month_keeps_flag_true_on_success(job_with_dna):
    from core.content_pipeline.tasks import content_generation_task, generate_next_month
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks._enqueue_week_images'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        content_generation_task(str(job_with_dna.id))
        calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
        calendar.next_week_generating = True
        calendar.save(update_fields=['next_week_generating'])

        generate_next_month(str(calendar.id))

    calendar.refresh_from_db()
    assert calendar.next_week_generating is True
```

(`test_content_generation_passes_business_url_to_image_gen`, que viene justo después en
el archivo, no se toca — sigue probando `content_generation_task`, no
`generate_next_month`.)

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py::test_generate_next_month_creates_28_posts_without_images -v`
Expected: FAIL — hoy `generate_next_month` sigue generando imagen inline.

- [ ] **Step 3: Reescribir `generate_next_month`**

En `core/content_pipeline/tasks.py`, reemplazar la función completa `generate_next_month`
(la que empieza en la línea ~250, desde `def generate_next_month(calendar_id: str) ->
None:` hasta el `calendar.save(update_fields=['next_week_generating'])` final de su
bloque `finally`) por:

```python
def generate_next_month(calendar_id: str) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    brand_dna = calendar.brand_dna
    try:
        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()
        last_post = calendar.posts.order_by('-day_number').first()
        base_day = last_post.day_number if last_post else 0
        if last_post:
            day_after_last = last_post.scheduled_at.astimezone(MEXICO_TZ).date() + timedelta(days=1)
            base_date = max(mexico_today, day_after_last)
        else:
            base_date = mexico_today

        scheduled_dates = smart_schedule_dates(brand_dna, base_date=base_date, count=28)
        text_gen = TextGenerator()

        for batch in range(4):
            posts_data = text_gen.generate(brand_dna)
            for i, post_data in enumerate(posts_data, start=1):
                day_number = base_day + (batch * 7) + i
                scheduled = scheduled_dates[batch * 7 + i - 1]
                ContentPost.objects.create(
                    calendar=calendar,
                    day_number=day_number,
                    caption=post_data['caption'],
                    image_url='',
                    image_urls=[],
                    video_url='',
                    format=post_data.get('format', ContentPost.FORMAT_SINGLE),
                    suggested_time=scheduled.astimezone(MEXICO_TZ).time(),
                    hashtags=post_data.get('hashtags', []),
                    scheduled_at=scheduled,
                )

        schedule_daily_emails(calendar)
        _enqueue_week_images(calendar_id, week_index=0)
    except Exception as e:
        logger.error(f"generate_next_month error para calendar {calendar_id}: {e}")
        calendar.next_week_generating = False
        calendar.save(update_fields=['next_week_generating'])
```

Nota: ya no hay `finally` — el reset de la bandera solo pasa dentro del `except`. En el
camino exitoso, la bandera se queda en `True` hasta que `_week_closing_task` la resetee
al cerrar la semana 4 (Task 1 de este plan).

- [ ] **Step 4: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: PASS — toda la suite del archivo, incluyendo los tests del Task 1.

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
GIT_EDITOR=true git commit -m "feat(monthly-chunking): generate_next_month solo genera texto, encadena la semana 1"
```

---

### Task 4: Webhook de Stripe — ajustar `job_timeout`

**Files:**
- Modify: `core/brand_dna/stripe_views.py`
- Test: `core/brand_dna/tests/test_stripe_views.py`

**Interfaces:**
- Consumes: `generate_next_month(calendar_id)` (Task 3 de este plan, firma sin cambios).
- Produces: nada nuevo para otras tareas.

- [ ] **Step 1: Escribir el test que falla**

En `core/brand_dna/tests/test_stripe_views.py`, dentro de
`test_webhook_payment_enqueues_generate_next_month` (línea ~305), **reemplazar** las
últimas 4 líneas del test:

```python
    assert response.status_code == 200
    mock_rq.enqueue.assert_called_once()
    enqueue_args = mock_rq.enqueue.call_args[0]
    assert enqueue_args[1] == str(calendar.id)
    calendar.refresh_from_db()
    assert calendar.next_week_generating is True
```

por:

```python
    assert response.status_code == 200
    mock_rq.enqueue.assert_called_once()
    enqueue_args = mock_rq.enqueue.call_args[0]
    assert enqueue_args[1] == str(calendar.id)
    enqueue_kwargs = mock_rq.enqueue.call_args[1]
    assert enqueue_kwargs['job_timeout'] == 900
    calendar.refresh_from_db()
    assert calendar.next_week_generating is True
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py::test_webhook_payment_enqueues_generate_next_month -v`
Expected: FAIL — `KeyError: 'job_timeout'` no coincide (hoy vale 2400, no 900) — en
realidad el test fallará en el `assert enqueue_kwargs['job_timeout'] == 900` con
`AssertionError` (2400 != 900), ya que el kwarg sí existe hoy, solo con otro valor.

- [ ] **Step 3: Cambiar el valor**

En `core/brand_dna/stripe_views.py`, línea 67, cambiar:

```python
                django_rq.enqueue(generate_next_month, str(calendar.id), job_timeout=2400)
```

por:

```python
                django_rq.enqueue(generate_next_month, str(calendar.id), job_timeout=900)
```

- [ ] **Step 4: Correr el test para confirmar que pasa**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py -v`
Expected: PASS — todos.

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/stripe_views.py core/brand_dna/tests/test_stripe_views.py
GIT_EDITOR=true git commit -m "fix(monthly-chunking): job_timeout del webhook baja de 2400s a 900s (Fase 1 ya no genera imagenes)"
```

---

### Verificación final

- [ ] Correr la suite completa:

Run: `docker compose exec backend pytest core/ -v`
Expected: 0 failures (aparte de los tests de rate-limit-flake ya documentados como
no relacionados — `test_results_requires_login`, `test_privacy_policy_accessible_without_login`,
`test_terms_of_service_accessible_without_login`, `test_ga4_tag_renders_when_measurement_id_configured`,
`test_umami_tag_always_renders` — si fallan por 429 al correr la suite completa
consecutiva, confirmar que pasan en aislamiento antes de darlos por buenos, no son
parte de este plan).

- [ ] Confirmar que no quedó ninguna llamada directa a `_generate_post_media` desde
`generate_next_month` (solo debe seguir usándose desde `content_generation_task` y
`_generate_missing_image`):

Run: `grep -n "_generate_post_media" core/content_pipeline/tasks.py`
Expected: 3 apariciones — la definición de la función, su uso en
`content_generation_task`, y su uso en `_generate_missing_image`. Cero apariciones
dentro de `generate_next_month`.

- [ ] Nota operativa para Anuar, fuera del alcance de este plan: el tenant de prueba
roto por la corrida fallida de hoy (`ventas@anuarbarrera.dev`, tenant
`54d0c749-fb2a-46b5-a289-5d1a970cbe50`: 24/35 posts, `next_week_generating=True`
atorado, job `f6606e34-9db5-4e7a-9395-3529646ac79a` en el `FailedJobRegistry` de RQ) se
limpia como paso operativo aparte, después de que este plan esté implementado y
probado — no antes.
