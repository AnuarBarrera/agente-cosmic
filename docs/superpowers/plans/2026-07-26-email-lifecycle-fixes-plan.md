# Correcciones al ciclo de vida de correos — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corregir HALLAZGO 81 (correos diarios duplicados), fijar el recordatorio diario a
las 7am hora de Ciudad de México, corregir 2 copys de correo confundibles con el trial
gratuito, y agregar correos de reactivación por inactividad (punto 6 de `ultimosCambios.md`).

**Architecture:** 6 tareas secuenciales sobre el subsistema de correos de
`core/content_pipeline/` (Django + RQ). Las tareas 1-3 son fixes independientes sobre código
ya existente (`scheduler.py`, `email_sender.py`, templates). Las tareas 4-6 construyen la
feature nueva de reactivación (modelo → EmailSender/templates → tarea+comando), cada una
dependiendo de la anterior.

**Tech Stack:** Django, django-rq (RQ Scheduler ya activo vía `--with-scheduler`), pytest +
pytest-django, PostgreSQL (soporta `select_for_update()` real, a diferencia de SQLite).

## Global Constraints

- Repo: `/home/anuarbarrera/agente-cosmic/`, checkout normal de `main`, sin rama de feature.
- Todos los comandos de Django/pytest se ejecutan dentro del contenedor:
  `docker compose exec backend <comando>`.
- `MEXICO_TZ = UTC-6` fijo, sin DST (desde 2023) — mismo offset ya usado en
  `core/content_pipeline/tasks.py` y `core/content_pipeline/smart_scheduler.py`. No usar
  `zoneinfo`/`pytz` — replicar el patrón existente (`timezone(timedelta(hours=-6))`).
- El recordatorio diario siempre apunta a las **7:00 AM hora de Ciudad de México** del día de
  `post.scheduled_at`; si ese horario ya pasó al momento de encolar, cae 2 horas después de
  "ahora" (nunca de inmediato).
- Los correos de reactivación se repiten cada **15 días** hasta que el usuario actúe (no son
  de una sola vez).
- No modificar `email_initial.html` (correo del trial gratuito) — su copy actual ("Tus 7
  días...") es correcto para el trial y debe permanecer intacto.
- Cada commit usa `GIT_EDITOR=true git commit -m "mensaje"` (nunca heredoc — se cuelga en
  este entorno).
- Spec completa con todo el código de referencia:
  `docs/superpowers/specs/2026-07-26-email-lifecycle-fixes-design.md`.

---

### Task 1: Fix HALLAZGO 81 (rango de días) + hora fija 7am CDMX

**Files:**
- Modify: `core/content_pipeline/scheduler.py` (reescritura completa, 28 líneas hoy)
- Modify: `core/content_pipeline/tasks.py:285` (llamada dentro de `_trial_closing_task`)
- Modify: `core/content_pipeline/tasks.py:355` (llamada dentro de `generate_next_month`)
- Test: `core/content_pipeline/tests/test_scheduler.py` (reescritura completa, 97 líneas hoy)
- Test: `core/content_pipeline/tests/test_tasks.py:783` (assertion rota por el cambio de firma)

**Interfaces:**
- Produce: `schedule_daily_emails(calendar: ContentCalendar, day_start: int, day_end: int) -> None`
  (firma nueva — antes solo recibía `calendar`). Todas las tareas futuras que la llamen deben
  pasar los 3 argumentos.

- [ ] **Step 1: Escribir los tests que fallan — reemplazar `core/content_pipeline/tests/test_scheduler.py` completo**

```python
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, time as dt_time
from django.utils import timezone
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db


@pytest.fixture
def calendar_with_7_posts():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Test', keywords=[], audience='Test', tone='profesional', primary_colors=[],
    )
    cal = ContentCalendar.objects.create(brand_dna=dna)
    for i in range(1, 8):
        ContentPost.objects.create(
            calendar=cal, day_number=i, caption=f'Post {i}',
            image_url='https://example.com/img.jpg', suggested_time='19:00',
            hashtags=[], scheduled_at=timezone.now() + timedelta(days=i),
        )
    return cal


def test_schedule_daily_emails_enqueues_jobs_within_range(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=7)

    assert mock_queue.enqueue_in.call_count == 7


def test_schedule_daily_emails_includes_day_1(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=7)

    calls = mock_queue.enqueue_in.call_args_list
    scheduled_days = [ContentPost.objects.get(id=str(call[0][2])).day_number for call in calls]
    assert 1 in scheduled_days


def test_schedule_daily_emails_excludes_days_outside_range(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=3)

    calls = mock_queue.enqueue_in.call_args_list
    scheduled_days = [ContentPost.objects.get(id=str(call[0][2])).day_number for call in calls]
    assert sorted(scheduled_days) == [1, 2, 3]


def test_schedule_daily_emails_two_calls_with_different_ranges_do_not_overlap(calendar_with_7_posts):
    # Reproduce HALLAZGO 81: _trial_closing_task programa dias 1-7, luego
    # generate_next_month programa dias 8-14 sobre el MISMO calendario — dias 1-7
    # deben seguir sin re-encolarse aunque sigan PENDING.
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
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=7)
        schedule_daily_emails(calendar_with_7_posts, day_start=8, day_end=14)

    scheduled_post_ids = [str(call[0][2]) for call in mock_queue.enqueue_in.call_args_list]
    assert len(scheduled_post_ids) == 14
    assert len(set(scheduled_post_ids)) == 14


def test_schedule_daily_emails_targets_7am_mexico_time_for_future_post(calendar_with_7_posts):
    from core.content_pipeline.scheduler import MEXICO_TZ, _REMINDER_HOUR_MEXICO
    post1 = calendar_with_7_posts.posts.get(day_number=1)

    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=7)

    calls = mock_queue.enqueue_in.call_args_list
    day1_call = next(c for c in calls if str(c[0][2]) == str(post1.id))
    delta = day1_call[0][0]

    expected_target = datetime.combine(
        post1.scheduled_at.astimezone(MEXICO_TZ).date(),
        dt_time(_REMINDER_HOUR_MEXICO, 0),
        tzinfo=MEXICO_TZ,
    )
    actual_target = timezone.now() + delta
    # Tolerancia de 5s por el tiempo real transcurrido entre el now() interno de
    # schedule_daily_emails y el now() de esta aserción.
    assert abs((actual_target - expected_target).total_seconds()) < 5


def test_schedule_daily_emails_falls_back_to_2_hours_when_7am_already_passed(calendar_with_7_posts):
    post1 = calendar_with_7_posts.posts.get(day_number=1)
    post1.scheduled_at = timezone.now() - timedelta(hours=10)
    post1.save(update_fields=['scheduled_at'])

    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=7)

    calls = mock_queue.enqueue_in.call_args_list
    day1_call = next(c for c in calls if str(c[0][2]) == str(post1.id))
    assert day1_call[0][0] == timedelta(hours=2)


def test_schedule_daily_emails_does_not_reschedule_sent_posts(calendar_with_7_posts):
    for post in calendar_with_7_posts.posts.all():
        post.status = ContentPost.STATUS_SENT
        post.save(update_fields=['status'])

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
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=14)

    scheduled_days = []
    for call in mock_queue.enqueue_in.call_args_list:
        post_id = str(call[0][2])
        post = ContentPost.objects.get(id=post_id)
        scheduled_days.append(post.day_number)

    assert sorted(scheduled_days) == list(range(8, 15))
```

- [ ] **Step 2: Corregir la aserción rota en `core/content_pipeline/tests/test_tasks.py:783`**

En `test_trial_closing_task_sends_initial_email_and_marks_job_done` (línea 774-788), la
línea 783 hoy dice `mock_schedule.assert_called_once_with(calendar)`. El `calendar` de ese
test se crea vacío (`ContentCalendar.objects.create(brand_dna=job_with_dna.brand_dna)`, sin
posts) — con la firma nueva, `_trial_closing_task` llamará
`schedule_daily_emails(calendar, day_start=1, day_end=calendar.posts.count())`, y
`calendar.posts.count()` es `0` en ese test. Reemplazar la línea 783 por:

```python
    mock_schedule.assert_called_once_with(calendar, day_start=1, day_end=0)
```

- [ ] **Step 3: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_scheduler.py core/content_pipeline/tests/test_tasks.py::test_trial_closing_task_sends_initial_email_and_marks_job_done -v`
Expected: FAIL — `TypeError: schedule_daily_emails() missing 2 required positional arguments: 'day_start' and 'day_end'` (o el mock assertion fallando con los argumentos viejos).

- [ ] **Step 4: Reescribir `core/content_pipeline/scheduler.py` completo**

```python
import logging
from datetime import datetime as dt_datetime, timedelta, timezone as dt_timezone, time as dt_time
import django_rq
from django.utils import timezone
from core.content_pipeline.models import ContentCalendar, ContentPost

logger = logging.getLogger(__name__)

MEXICO_TZ = dt_timezone(timedelta(hours=-6))  # UTC-6 sin DST (desde 2023) — mismo offset que tasks.py/smart_scheduler.py
_REMINDER_HOUR_MEXICO = 7
_REMINDER_FALLBACK_DELAY = timedelta(hours=2)


def schedule_daily_emails(calendar: ContentCalendar, day_start: int, day_end: int) -> None:
    from core.content_pipeline.tasks import send_daily_email_task
    queue = django_rq.get_queue('default')
    now = timezone.now()
    posts = list(calendar.posts.filter(
        status=ContentPost.STATUS_PENDING,
        day_number__gte=day_start,
        day_number__lte=day_end,
    ).order_by('day_number'))
    for post in posts:
        post_date_mx = post.scheduled_at.astimezone(MEXICO_TZ).date()
        target = dt_datetime.combine(post_date_mx, dt_time(_REMINDER_HOUR_MEXICO, 0), tzinfo=MEXICO_TZ)
        delta = target - now
        if delta < _REMINDER_FALLBACK_DELAY:
            delta = _REMINDER_FALLBACK_DELAY
        queue.enqueue_in(delta, send_daily_email_task, str(post.id))
        logger.info(f"Dia {post.day_number} programado en {delta} (objetivo 7am CDMX {post_date_mx})")
```

- [ ] **Step 5: Actualizar los 2 llamadores en `core/content_pipeline/tasks.py`**

En `_trial_closing_task` (línea 285), cambiar:
```python
            EmailSender().send_initial(job=job, brand_dna=brand_dna)
            schedule_daily_emails(calendar)
```
por:
```python
            EmailSender().send_initial(job=job, brand_dna=brand_dna)
            schedule_daily_emails(calendar, day_start=1, day_end=calendar.posts.count())
```

En `generate_next_month` (línea 355), cambiar:
```python
        schedule_daily_emails(calendar)
        _enqueue_week_images(calendar_id, week_index=0)
```
por:
```python
        schedule_daily_emails(calendar, day_start=base_day + 1, day_end=base_day + 28)
        _enqueue_week_images(calendar_id, week_index=0)
```

- [ ] **Step 6: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_scheduler.py core/content_pipeline/tests/test_tasks.py -v`
Expected: PASS — todos los tests de `test_scheduler.py` (7) y todos los de `test_tasks.py`
(sin regresiones en los que mockean `schedule_daily_emails` sin asserts de argumentos).

- [ ] **Step 7: Commit**

```bash
git add core/content_pipeline/scheduler.py core/content_pipeline/tasks.py core/content_pipeline/tests/test_scheduler.py core/content_pipeline/tests/test_tasks.py
GIT_EDITOR=true git commit -m "fix(hallazgo-81): schedule_daily_emails por rango de dias + recordatorio fijo a las 7am CDMX"
```

---

### Task 2: Guard de idempotencia en `EmailSender.send_daily()`

**Files:**
- Modify: `core/content_pipeline/email_sender.py:86-111` (método `send_daily`, agregar import `transaction`)
- Test: `core/content_pipeline/tests/test_email_sender.py`

**Interfaces:**
- Consumes: nada de la Task 1.
- Produce: `EmailSender.send_daily(post: ContentPost) -> None` (misma firma pública, comportamiento interno cambia — ahora es un no-op silencioso si `post.status != STATUS_PENDING` al momento de ejecutarse bajo lock).

- [ ] **Step 1: Escribir el test que falla — agregar a `core/content_pipeline/tests/test_email_sender.py`**

Agregar después de `test_send_daily_email_uses_real_date_not_day_number` (línea 82):

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_daily_is_idempotent_for_already_sent_post(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    post = posts[0]
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_daily(post=post)
        sender.send_daily(post=post)
    assert mock_send.call_count == 1
    post.refresh_from_db()
    assert post.status == ContentPost.STATUS_SENT
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py::test_send_daily_is_idempotent_for_already_sent_post -v`
Expected: FAIL — `mock_send.call_count` es `2`, no `1` (la implementación actual no verifica el status antes de reenviar).

- [ ] **Step 3: Reemplazar `send_daily` en `core/content_pipeline/email_sender.py`**

Agregar al bloque de imports existente (línea 1-9, junto a los demás imports de Django):
```python
from django.db import transaction
```

Reemplazar el método completo (líneas 86-111):
```python
    def send_daily(self, post: ContentPost) -> None:
        with transaction.atomic():
            locked_post = ContentPost.objects.select_for_update().filter(
                id=post.id, status=ContentPost.STATUS_PENDING
            ).first()
            if locked_post is None:
                logger.info(f"Post {post.id} ya no está pending — se omite envío duplicado")
                return
            calendar_review_url = settings.COSMIC_BASE_URL + reverse(
                'calendar_review', args=[locked_post.calendar.brand_dna.job.id]
            )
            fecha = _fecha_es(locked_post.scheduled_at)
            html = render_to_string('content_pipeline/email_daily.html', {
                'post': locked_post,
                'calendar_review_url': calendar_review_url,
                'fecha': fecha,
            })
            business_name = (locked_post.calendar.brand_dna.business_name or '').strip()
            email = locked_post.calendar.brand_dna.job.email
            subject = f'🔔 No se te olvide publicar hoy ({fecha}) — {business_name}' if business_name else f'🔔 No se te olvide publicar hoy ({fecha}) — Agente Cosmic'
            send_mail(
                subject,
                f'No se te olvide publicar el día de hoy ({fecha}).',
                settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                html_message=html,
                fail_silently=False,
            )
            locked_post.status = ContentPost.STATUS_SENT
            locked_post.sent_at = timezone.now()
            locked_post.save(update_fields=['status', 'sent_at'])
        EMAILS_SENT.labels(type='daily_post').inc()
        logger.info(f"Email dia {locked_post.day_number} enviado a {email}")
```

- [ ] **Step 4: Correr todos los tests de `send_daily` para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py core/content_pipeline/tests/test_tasks.py::test_send_daily_email_task_skips_when_already_downloaded core/content_pipeline/tests/test_tasks.py::test_send_daily_email_task_sends_when_not_downloaded -v`
Expected: PASS — los 3 tests de `send_daily` en `test_email_sender.py`
(`test_send_daily_email_marks_post_sent`, `test_send_daily_email_uses_real_date_not_day_number`,
`test_send_daily_is_idempotent_for_already_sent_post`) y los 2 de `test_tasks.py`.

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/email_sender.py core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "fix(hallazgo-81): guard de idempotencia con select_for_update en EmailSender.send_daily"
```

---

### Task 3: Templates de correo — mes completo separado del trial + copy de "primera semana del mes"

**Files:**
- Create: `core/content_pipeline/templates/content_pipeline/email_month_ready.html`
- Modify: `core/content_pipeline/templates/content_pipeline/email_week_ready.html:9`
- Modify: `core/content_pipeline/email_sender.py:47` (método `send_month_ready`, nombre del template)
- Test: `core/content_pipeline/tests/test_email_sender.py`

**Interfaces:**
- Consumes: nada de las tareas 1-2.
- Produce: nada consumido por tareas futuras — cambio de copy autocontenido.

- [ ] **Step 1: Escribir los tests que fallan — agregar a `core/content_pipeline/tests/test_email_sender.py`**

Agregar después de `test_send_month_ready_email_calls_django_send` (línea 157):

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_month_ready_email_uses_month_specific_copy(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_month_ready(job=job, brand_dna=dna)
    html = mock_send.call_args[1]['html_message']
    assert '7 días' not in html
    assert '4 semanas' in html
    assert 'La generación del mes de contenido que adquiriste' in html
```

Agregar después de `test_send_week_ready_email_calls_django_send` (línea 172):

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_week_ready_email_clarifies_it_is_part_of_paid_month(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_week_ready(job=job, brand_dna=dna)
    html = mock_send.call_args[1]['html_message']
    assert 'del mes que adquiriste' in html
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py::test_send_month_ready_email_uses_month_specific_copy core/content_pipeline/tests/test_email_sender.py::test_send_week_ready_email_clarifies_it_is_part_of_paid_month -v`
Expected: FAIL — `send_month_ready` sigue renderizando `email_initial.html` (contiene "Tus 7
días", no "4 semanas" ni la frase nueva); `email_week_ready.html` no contiene "del mes que
adquiriste".

- [ ] **Step 3: Crear `core/content_pipeline/templates/content_pipeline/email_month_ready.html`**

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Tu mes de contenido está listo — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 580px; margin: 0 auto; padding: 24px 20px; color: #333;">

  <p style="margin: 0 0 16px; font-size: 0.72rem; color: #999; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">📬 Notificación automática</p>

  <p style="font-size: 1.1rem; margin-bottom: 8px;">Hola,</p>
  <p>La generación del mes de contenido que adquiriste para <strong>{{ brand_dna.business_name }}</strong> ya está completa.</p>

  <p>Puedes revisarlo y aprobarlo desde la plataforma:</p>

  <div style="text-align: center; margin: 28px 0;">
    <a href="{{ calendar_url }}"
       style="display: inline-block; padding: 14px 32px; background: #e94560; color: #fff;
              border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 1rem;">
      Ver mi calendario →
    </a>
  </div>

  <p style="color: #555;">Tus 4 semanas de contenido ya están listas — no tienes que esperar. Cada día te mandaremos un recordatorio para que no se te pase publicar.</p>

  <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">
  <p style="font-size: 11px; color: #bbb; margin: 0;">Agente Cosmic — Powered by Google Cloud</p>

</body>
</html>
```

- [ ] **Step 4: Cambiar el template usado por `send_month_ready` en `core/content_pipeline/email_sender.py`**

En el método `send_month_ready` (línea 45-63), cambiar:
```python
        html = render_to_string('content_pipeline/email_initial.html', {
            'brand_dna': brand_dna,
            'calendar_url': calendar_url,
        })
```
por:
```python
        html = render_to_string('content_pipeline/email_month_ready.html', {
            'brand_dna': brand_dna,
            'calendar_url': calendar_url,
        })
```
(nada más de esa función cambia — `email_initial.html`, usado por `send_initial`, no se toca).

- [ ] **Step 5: Actualizar la línea 9 de `core/content_pipeline/templates/content_pipeline/email_week_ready.html`**

Cambiar:
```html
  <p>Tu primera semana de contenido para <strong>{{ brand_dna.business_name }}</strong> ya está lista.</p>
```
por:
```html
  <p>Tu primera semana de contenido del mes que adquiriste para <strong>{{ brand_dna.business_name }}</strong> ya está lista.</p>
```

- [ ] **Step 6: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py -v`
Expected: PASS — todos, incluyendo los 2 nuevos y los ya existentes de `send_month_ready`/`send_week_ready`.

- [ ] **Step 7: Commit**

```bash
git add core/content_pipeline/templates/content_pipeline/email_month_ready.html core/content_pipeline/templates/content_pipeline/email_week_ready.html core/content_pipeline/email_sender.py core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "fix(email): separar copy de mes completo del trial y aclarar primera semana del mes pagado"
```

---

### Task 4: Modelo + migraciones para correos de reactivación

**Files:**
- Modify: `core/content_pipeline/models.py:6-16` (`ContentCalendar`, agregar campo)
- Modify: `core/tenant_management/models.py:142-153` (`User`, agregar campo)
- Create: migración de `content_pipeline` (generada por `makemigrations`, no escribir a mano)
- Create: migración de `tenant_management` (generada por `makemigrations`, no escribir a mano)
- Test: `core/content_pipeline/tests/test_models.py`
- Test: `core/tenant_management/tests/test_models.py`

**Interfaces:**
- Produce: `ContentCalendar.last_reactivation_email_at` (`DateTimeField(null=True, blank=True)`)
  y `User.last_reactivation_email_at` (`DateTimeField(null=True, blank=True)`) — usados por la
  Task 6.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `core/content_pipeline/tests/test_models.py` (reusa la fixture `brand_dna` ya
existente en ese archivo):

```python
def test_content_calendar_last_reactivation_email_at_defaults_to_none(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    assert calendar.last_reactivation_email_at is None
```

Agregar a `core/tenant_management/tests/test_models.py`:

```python
def test_user_last_reactivation_email_at_defaults_to_none():
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username='reactivacion@test.com', email='reactivacion@test.com', password='pass1234')
    assert user.last_reactivation_email_at is None
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_models.py::test_content_calendar_last_reactivation_email_at_defaults_to_none core/tenant_management/tests/test_models.py::test_user_last_reactivation_email_at_defaults_to_none -v`
Expected: FAIL — `AttributeError: 'ContentCalendar' object has no attribute 'last_reactivation_email_at'` (y lo mismo para `User`).

- [ ] **Step 3: Agregar el campo a `ContentCalendar` en `core/content_pipeline/models.py`**

Cambiar:
```python
class ContentCalendar(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand_dna = models.OneToOneField(BrandDNA, on_delete=models.CASCADE, related_name='calendar')
    created_at = models.DateTimeField(auto_now_add=True)
    next_week_generating = models.BooleanField(default=False)

    class Meta:
        db_table = 'content_pipeline_calendar'
```
por:
```python
class ContentCalendar(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand_dna = models.OneToOneField(BrandDNA, on_delete=models.CASCADE, related_name='calendar')
    created_at = models.DateTimeField(auto_now_add=True)
    next_week_generating = models.BooleanField(default=False)
    last_reactivation_email_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'content_pipeline_calendar'
```

- [ ] **Step 4: Agregar el campo a `User` en `core/tenant_management/models.py`**

Cambiar:
```python
class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TenantModel, on_delete=models.CASCADE, null=True, blank=True)
    email = models.EmailField(_('email address'), unique=True)
    display_name = models.CharField(max_length=255, blank=True, null=True) # New field
    email_verified = models.BooleanField(default=False)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = 'email'
```
por:
```python
class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TenantModel, on_delete=models.CASCADE, null=True, blank=True)
    email = models.EmailField(_('email address'), unique=True)
    display_name = models.CharField(max_length=255, blank=True, null=True) # New field
    email_verified = models.BooleanField(default=False)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    last_reactivation_email_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = 'email'
```

- [ ] **Step 5: Generar las migraciones reales (no escribirlas a mano)**

Run: `docker compose exec backend python manage.py makemigrations content_pipeline tenant_management`
Expected: Django crea 2 archivos nuevos — uno en `core/content_pipeline/migrations/`
(siguiente número después de `0012_delete_weeklyfeedback.py`, probablemente
`0013_contentcalendar_last_reactivation_email_at.py`) y uno en
`core/tenant_management/migrations/` (siguiente número después de
`0023_subscription_paid_until.py`, probablemente `0024_user_last_reactivation_email_at.py`).
Usar los nombres que Django genere — no forzar un nombre distinto.

- [ ] **Step 6: Aplicar las migraciones**

Run: `docker compose exec backend python manage.py migrate`
Expected: `Applying content_pipeline.00XX_... OK` y `Applying tenant_management.00XX_... OK`.

- [ ] **Step 7: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_models.py core/tenant_management/tests/test_models.py -v`
Expected: PASS — todos, sin regresiones en los tests de modelo existentes.

- [ ] **Step 8: Commit**

```bash
git add core/content_pipeline/models.py core/tenant_management/models.py core/content_pipeline/migrations/ core/tenant_management/migrations/ core/content_pipeline/tests/test_models.py core/tenant_management/tests/test_models.py
GIT_EDITOR=true git commit -m "feat(reactivacion): agregar last_reactivation_email_at a ContentCalendar y User"
```

---

### Task 5: `EmailSender` + templates para correos de reactivación

**Files:**
- Modify: `core/content_pipeline/email_sender.py` (2 métodos nuevos)
- Create: `core/content_pipeline/templates/content_pipeline/email_reactivation_calendar.html`
- Create: `core/content_pipeline/templates/content_pipeline/email_reactivation_analysis.html`
- Test: `core/content_pipeline/tests/test_email_sender.py`

**Interfaces:**
- Consumes: nada de la Task 4 directamente (no toca el campo `last_reactivation_email_at`,
  solo envía el correo — la Task 6 es quien lee/escribe ese campo).
- Produce: `EmailSender.send_reactivation_calendar(calendar: ContentCalendar) -> None` y
  `EmailSender.send_reactivation_analysis(user) -> None` — usados por la Task 6.

- [ ] **Step 1: Escribir los tests que fallan — agregar a `core/content_pipeline/tests/test_email_sender.py`** (al final del archivo, antes de la última línea en blanco)

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_reactivation_calendar_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_reactivation_calendar(calendar=calendar)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    assert 'Tu Web MX' in call_kwargs[0][0]


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_reactivation_analysis_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    from django.contrib.auth import get_user_model
    job, dna, calendar, posts = full_setup
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username=job.email, email=job.email, password='pass1234')
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_reactivation_analysis(user=user)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert user.email in call_kwargs[1]['recipient_list']
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py::test_send_reactivation_calendar_calls_django_send core/content_pipeline/tests/test_email_sender.py::test_send_reactivation_analysis_calls_django_send -v`
Expected: FAIL — `AttributeError: 'EmailSender' object has no attribute 'send_reactivation_calendar'` (y lo mismo para `send_reactivation_analysis`).

- [ ] **Step 3: Crear `core/content_pipeline/templates/content_pipeline/email_reactivation_calendar.html`**

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Tu contenido sigue esperando — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 580px; margin: 0 auto; padding: 24px 20px; color: #333;">

  <p style="margin: 0 0 16px; font-size: 0.72rem; color: #999; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">📬 Notificación automática</p>

  <p style="font-size: 1.1rem; margin-bottom: 8px;">Hola,</p>
  <p>Tu contenido para <strong>{{ brand_dna.business_name }}</strong> sigue listo para descargar y publicar — no lo dejes esperando.</p>

  <div style="text-align: center; margin: 28px 0;">
    <a href="{{ calendar_review_url }}"
       style="display: inline-block; padding: 14px 32px; background: #e94560; color: #fff;
              border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 1rem;">
      Ver mi calendario →
    </a>
  </div>

  <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">
  <p style="font-size: 11px; color: #bbb; margin: 0;">Agente Cosmic — Powered by Google Cloud</p>

</body>
</html>
```

- [ ] **Step 4: Crear `core/content_pipeline/templates/content_pipeline/email_reactivation_analysis.html`**

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Termina el análisis de tu marca — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 580px; margin: 0 auto; padding: 24px 20px; color: #333;">

  <p style="margin: 0 0 16px; font-size: 0.72rem; color: #999; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">📬 Notificación automática</p>

  <p style="font-size: 1.1rem; margin-bottom: 8px;">Hola,</p>
  <p>Te registraste en Agente Cosmic pero aún no analizamos tu marca — te toma 2 minutos y desbloquea tu primera semana de contenido gratis.</p>

  <div style="text-align: center; margin: 28px 0;">
    <a href="{{ analysis_url }}"
       style="display: inline-block; padding: 14px 32px; background: #e94560; color: #fff;
              border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 1rem;">
      Analizar mi marca →
    </a>
  </div>

  <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">
  <p style="font-size: 11px; color: #bbb; margin: 0;">Agente Cosmic — Powered by Google Cloud</p>

</body>
</html>
```

- [ ] **Step 5: Agregar los 2 métodos nuevos a `core/content_pipeline/email_sender.py`** (al final de la clase `EmailSender`, después de `send_month_expired`)

```python
    def send_reactivation_calendar(self, calendar: ContentCalendar) -> None:
        brand_dna = calendar.brand_dna
        job = brand_dna.job
        calendar_review_url = settings.COSMIC_BASE_URL + reverse('calendar_review', args=[job.id])
        html = render_to_string('content_pipeline/email_reactivation_calendar.html', {
            'brand_dna': brand_dna,
            'calendar_review_url': calendar_review_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'👀 Tu contenido de {name} sigue esperando' if name else '👀 Tu contenido sigue esperando'
        plain = (
            f'Tu contenido de {name} sigue listo para descargar y publicar.'
            if name else 'Tu contenido sigue listo para descargar y publicar.'
        )
        send_mail(
            subject, plain, settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email], html_message=html, fail_silently=False,
        )
        EMAILS_SENT.labels(type='reactivation_calendar').inc()
        logger.info(f"Email de reactivacion (calendario) enviado a {job.email} para calendar {calendar.id}")

    def send_reactivation_analysis(self, user) -> None:
        analysis_url = settings.COSMIC_BASE_URL + reverse('new_analysis')
        html = render_to_string('content_pipeline/email_reactivation_analysis.html', {
            'analysis_url': analysis_url,
        })
        subject = '🚀 Aún no analizamos tu marca — te tomará 2 minutos'
        plain = f'Aún no completas el análisis de tu marca. Hazlo aquí: {analysis_url}'
        send_mail(
            subject, plain, settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email], html_message=html, fail_silently=False,
        )
        EMAILS_SENT.labels(type='reactivation_analysis').inc()
        logger.info(f"Email de reactivacion (analisis) enviado a {user.email}")
```

Necesita `ContentCalendar` importado en `core/content_pipeline/email_sender.py` — ya está
importado (línea 8: `from core.content_pipeline.models import ContentPost`; agregar
`ContentCalendar` a esa misma línea: `from core.content_pipeline.models import ContentCalendar, ContentPost`).

- [ ] **Step 6: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py -v`
Expected: PASS — todos, incluyendo los 2 nuevos.

- [ ] **Step 7: Commit**

```bash
git add core/content_pipeline/email_sender.py core/content_pipeline/templates/content_pipeline/email_reactivation_calendar.html core/content_pipeline/templates/content_pipeline/email_reactivation_analysis.html core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "feat(reactivacion): metodos y templates de EmailSender para correos de reactivacion"
```

---

### Task 6: Tarea `send_reactivation_emails_task` + management command

**Files:**
- Modify: `core/content_pipeline/tasks.py` (imports + tarea nueva al final del archivo)
- Create: `core/tenant_management/management/commands/send_reactivation_emails.py`
- Test: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: `ContentCalendar.last_reactivation_email_at` y `User.last_reactivation_email_at`
  (Task 4), `EmailSender.send_reactivation_calendar`/`send_reactivation_analysis` (Task 5).
- Produce: `send_reactivation_emails_task() -> None`, invocada por el management command
  `send_reactivation_emails` (mismo patrón que `expire_stale_trials`).

- [ ] **Step 1: Escribir los tests que fallan — agregar a `core/content_pipeline/tests/test_tasks.py`** (al final del archivo)

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_reactivation_emails_task_sends_for_stale_calendar_without_downloads(calendar_with_dna):
    for i in range(1, 4):
        _make_post(calendar_with_dna, i)
    ContentCalendar.objects.filter(id=calendar_with_dna.id).update(
        created_at=timezone.now() - timedelta(days=4)
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_calendar.assert_called_once()
    calendar_with_dna.refresh_from_db()
    assert calendar_with_dna.last_reactivation_email_at is not None


def test_send_reactivation_emails_task_skips_recent_calendar(calendar_with_dna):
    for i in range(1, 4):
        _make_post(calendar_with_dna, i)
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_calendar.assert_not_called()


def test_send_reactivation_emails_task_skips_calendar_with_a_download(calendar_with_dna):
    _make_post(calendar_with_dna, 1, downloaded_at=timezone.now())
    ContentCalendar.objects.filter(id=calendar_with_dna.id).update(
        created_at=timezone.now() - timedelta(days=4)
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_calendar.assert_not_called()


def test_send_reactivation_emails_task_does_not_repeat_before_15_days(calendar_with_dna):
    _make_post(calendar_with_dna, 1)
    ContentCalendar.objects.filter(id=calendar_with_dna.id).update(
        created_at=timezone.now() - timedelta(days=20),
        last_reactivation_email_at=timezone.now() - timedelta(days=5),
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_calendar.assert_not_called()


def test_send_reactivation_emails_task_repeats_after_15_days(calendar_with_dna):
    _make_post(calendar_with_dna, 1)
    ContentCalendar.objects.filter(id=calendar_with_dna.id).update(
        created_at=timezone.now() - timedelta(days=30),
        last_reactivation_email_at=timezone.now() - timedelta(days=16),
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_calendar.assert_called_once()


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_reactivation_emails_task_sends_for_user_without_analysis():
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username='sinanalisis@test.com', email='sinanalisis@test.com', password='pass1234')
    UserModel.objects.filter(id=user.id).update(date_joined=timezone.now() - timedelta(days=3))
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_analysis.assert_called_once()
    user.refresh_from_db()
    assert user.last_reactivation_email_at is not None


def test_send_reactivation_emails_task_skips_recent_user():
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    UserModel.objects.create_user(username='reciente@test.com', email='reciente@test.com', password='pass1234')
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_analysis.assert_not_called()


def test_send_reactivation_emails_task_skips_user_with_analysis(job_with_dna_and_tenant):
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    UserModel.objects.filter(id=job_with_dna_and_tenant.user.id).update(
        date_joined=timezone.now() - timedelta(days=3)
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_analysis.assert_not_called()
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -k reactivation -v`
Expected: FAIL — `ImportError: cannot import name 'send_reactivation_emails_task'`.

- [ ] **Step 3: Agregar los imports nuevos a `core/content_pipeline/tasks.py`**

Cambiar la línea 5 (`from django.utils import timezone`) agregando debajo:
```python
from django.db.models import Count
```

Cambiar la línea 13 (`from core.tenant_management.models import Subscription`) por:
```python
from core.tenant_management.models import Subscription, User
```

- [ ] **Step 4: Agregar la tarea nueva al final de `core/content_pipeline/tasks.py`**

```python
_REACTIVATION_FIRST_DAYS_CALENDAR = 3
_REACTIVATION_FIRST_DAYS_ANALYSIS = 2
_REACTIVATION_REPEAT_DAYS = 15


def send_reactivation_emails_task() -> None:
    now = timezone.now()

    stale_calendars = ContentCalendar.objects.filter(
        created_at__lte=now - timedelta(days=_REACTIVATION_FIRST_DAYS_CALENDAR),
    ).exclude(
        posts__downloaded_at__isnull=False
    )
    for calendar in stale_calendars:
        due = (
            calendar.last_reactivation_email_at is None
            or calendar.last_reactivation_email_at <= now - timedelta(days=_REACTIVATION_REPEAT_DAYS)
        )
        if not due:
            continue
        try:
            EmailSender().send_reactivation_calendar(calendar)
            calendar.last_reactivation_email_at = now
            calendar.save(update_fields=['last_reactivation_email_at'])
        except Exception as email_err:
            logger.error(f"Email de reactivacion (calendario) fallo para {calendar.id} (no fatal): {email_err}")

    stale_users = User.objects.filter(
        date_joined__lte=now - timedelta(days=_REACTIVATION_FIRST_DAYS_ANALYSIS),
    ).annotate(
        jobs_count=Count('analysis_jobs')
    ).filter(jobs_count=0)
    for user in stale_users:
        due = (
            user.last_reactivation_email_at is None
            or user.last_reactivation_email_at <= now - timedelta(days=_REACTIVATION_REPEAT_DAYS)
        )
        if not due:
            continue
        try:
            EmailSender().send_reactivation_analysis(user)
            user.last_reactivation_email_at = now
            user.save(update_fields=['last_reactivation_email_at'])
        except Exception as email_err:
            logger.error(f"Email de reactivacion (analisis) fallo para {user.id} (no fatal): {email_err}")
```

- [ ] **Step 5: Crear `core/tenant_management/management/commands/send_reactivation_emails.py`**

```python
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Envia correos de reactivacion a calendarios sin descargar y usuarios sin analizar su marca'

    def handle(self, *args, **options):
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
        self.stdout.write(self.style.SUCCESS('Proceso de correos de reactivacion finalizado.'))
```

- [ ] **Step 6: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -k reactivation -v`
Expected: PASS — los 8 tests nuevos.

- [ ] **Step 7: Correr la suite completa de `content_pipeline` y `tenant_management` para confirmar que no hay regresiones**

Run: `docker compose exec backend pytest core/content_pipeline/ core/tenant_management/ -v`
Expected: PASS — todos (más los 3 fallos ya conocidos y documentados de HALLAZGO 80 en
`test_auth_security.py`, que son preexistentes y no relacionados a este plan).

- [ ] **Step 8: Commit**

```bash
git add core/content_pipeline/tasks.py core/tenant_management/management/commands/send_reactivation_emails.py core/content_pipeline/tests/test_tasks.py
GIT_EDITOR=true git commit -m "feat(reactivacion): tarea send_reactivation_emails_task + management command"
```

**Nota para el cierre del plan:** avisar a Anuar que falta una acción suya en el servidor
(no es código de este plan, mismo patrón que quedó pendiente para `expire_stale_trials`):
agregar al cron externo al repo una línea que llame
`python manage.py send_reactivation_emails` una vez al día.

---

## Verificación final

Después de completar las 6 tareas, correr la suite completa del proyecto una vez más:

Run: `docker compose exec backend pytest core/ -v`
Expected: solo los 3 fallos preexistentes de HALLAZGO 80 (`test_auth_security.py`, no
relacionados a este plan); todo lo demás en verde.
