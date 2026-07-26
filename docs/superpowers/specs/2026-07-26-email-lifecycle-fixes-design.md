# Correcciones al ciclo de vida de correos — Diseño

**Fecha:** 2026-07-26
**Origen:** HALLAZGO 81 (`hallazgos.txt`, correos diarios duplicados, Severidad/Prioridad
Alta, confirmado en producción con un cliente real — calendario de `dialogix`) + puntos 3
(ya resuelto en sesión anterior) y 6 (`ultimosCambios.md`, correos de reactivación,
propuesto por Anuar 2026-07-23) + feedback directo de Anuar sobre copys de correo con
capturas reales de Gmail (2026-07-26).

## Contexto

`schedule_daily_emails()` (`core/content_pipeline/scheduler.py`) se invoca en 2 puntos de
`core/content_pipeline/tasks.py`: `_trial_closing_task` (cierre del trial gratuito de 7
días) y `generate_next_month` (generación del mes pagado, disparada por el webhook de
Stripe). Ambas llamadas filtran `calendar.posts.filter(status=PENDING)` sin distinguir si
un post ya tiene un recordatorio programado de una llamada anterior — cuando el mes se paga
mientras aún quedan días del trial sin publicar, la segunda llamada vuelve a programar esos
mismos días, duplicando el envío. Confirmado en logs reales: 2 jobs de RQ para el mismo post
(día 2 y día 3 del calendario de dialogix), un correo real duplicado confirmado.

Adicionalmente, el mismo mecanismo dispara el recordatorio del día 1 casi de inmediato
(clamp de 5 minutos cuando la hora "sugerida" del post ya pasó, típico el mismo día en que
se generó el contenido) — se siente a spam llegando pegado al correo de "tu contenido ya
está listo". Y 2 correos (el de "mes completo" y el de "primera semana del mes") tienen copy
que puede confundirse con el trial gratuito, según feedback directo de Anuar con capturas
reales.

Por último, el punto 6 de `ultimosCambios.md` (correos de reactivación por inactividad)
sigue sin diseño — se resuelve aquí como parte del mismo subsistema de correos.

## Decisiones de Anuar

- **Fix de HALLAZGO 81**: por rango de `day_number` (no por campo booleano nuevo) — cada
  llamada a `schedule_daily_emails` cubre solo el rango de días que le corresponde a esa
  etapa.
- **Hora del recordatorio diario**: siempre **7:00 AM hora de Ciudad de México**
  (`MEXICO_TZ = UTC-6`, mismo offset fijo sin DST ya usado en `smart_scheduler.py` y
  `tasks.py`), sin importar la hora "sugerida" de publicación del post — le da margen al
  usuario para verlo durante el día. Si ese 7am ya pasó al momento de encolar (caso del día
  1, cuando la generación termina después de las 7am del mismo día), el envío cae 2 horas
  después de generado en vez de ser instantáneo.
- **Templates de correo**: 3 cambios de copy puntuales (ver Diseño técnico C), confirmados
  con capturas reales de Gmail.
- **Correos de reactivación (punto 6)**: 2 disparadores (calendario sin descargar, usuario
  sin analizar marca), repetición **cada 15 días** hasta que el usuario actúe — no es un
  envío único.
- **Fuera de alcance**: el 3er disparador original del punto 6 ("día 7 alcanzado") ya está
  resuelto por el pivote a pago único mensual (ver `ultimosCambios.md` punto 5/6.3) — no se
  toca aquí.

## Diseño técnico

### A. Fix de HALLAZGO 81 — `schedule_daily_emails` por rango de días

`core/content_pipeline/scheduler.py`, firma nueva:

```python
def schedule_daily_emails(calendar: ContentCalendar, day_start: int, day_end: int) -> None:
    ...
    posts = list(calendar.posts.filter(
        status=ContentPost.STATUS_PENDING,
        day_number__gte=day_start,
        day_number__lte=day_end,
    ).order_by('day_number'))
```

Llamadores actualizados en `core/content_pipeline/tasks.py`:

- `_trial_closing_task` (línea ~285): `schedule_daily_emails(calendar, day_start=1, day_end=calendar.posts.count())`
  — en este punto del trial, `calendar.posts.count()` es exactamente los días recién
  creados (7), sin magic number.
- `generate_next_month` (línea ~355): `schedule_daily_emails(calendar, day_start=base_day + 1, day_end=base_day + 28)`
  — `base_day` ya existe en el scope de la función (línea 327), es el último `day_number`
  antes de agregar los 28 nuevos del mes.

### B. Hora fija de recordatorio (7am CDMX) + fallback del día 1

Mismo archivo, `schedule_daily_emails`, reemplaza el cálculo de `delta`:

```python
from datetime import datetime as dt_datetime, timedelta, timezone as dt_timezone, time as dt_time
import django_rq
from django.utils import timezone
from core.content_pipeline.models import ContentCalendar, ContentPost

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

Reemplaza por completo el clamp anterior (`if delta.total_seconds() < 300: delta =
timedelta(minutes=5)`) — un solo mecanismo cubre "siempre a las 7am" y "el día 1 no debe
sentirse instantáneo".

### C. Guard de idempotencia en `EmailSender.send_daily()`

`core/content_pipeline/email_sender.py`, defensa adicional contra el `SerializationFailure`
concurrente documentado en HALLAZGO 81 (2 workers procesando el mismo post casi
simultáneamente):

```python
# email_sender.py — agregar al bloque de imports existente (top del archivo):
from django.db import transaction

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

`select_for_update()` (sin `skip_locked`) bloquea al segundo worker hasta que el primero
libere el lock (commit); al reintentar la lectura ve `status != PENDING` y sale sin enviar.
El bloque completo (lectura + `send_mail` + escritura) queda dentro de la misma
transacción — necesario para que el lock cubra todo el envío, no solo la lectura.

### D. Templates de correo

**D1. `email_month_ready.html` (nuevo)** — hoy `send_month_ready` reutiliza
`email_initial.html` (el del trial), lo cual produce el texto incorrecto "Tus 7 días" para
un mes de 28. Se crea un template dedicado, mismo layout que `email_initial.html`:

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

`EmailSender.send_month_ready` cambia `render_to_string('content_pipeline/email_initial.html', ...)`
por `render_to_string('content_pipeline/email_month_ready.html', ...)`. `email_initial.html`
(usado por `send_initial`, el correo del trial) **no se toca**.

**D2. `email_week_ready.html` — copy actualizado**, línea 9:

```html
<p>Tu primera semana de contenido del mes que adquiriste para <strong>{{ brand_dna.business_name }}</strong> ya está lista.</p>
```
(reemplaza `Tu primera semana de contenido para <strong>...</strong> ya está lista.`)

### E. Correos de reactivación (punto 6)

**E1. Campos nuevos (migración):**

- `ContentCalendar.last_reactivation_email_at` — `DateTimeField(null=True, blank=True)`
- `core.tenant_management.models.User.last_reactivation_email_at` — `DateTimeField(null=True, blank=True)`

**E2. Templates nuevos:**

- `email_reactivation_calendar.html` — tono igual al resto de correos automáticos
  (`📬 Notificación automática`), recuerda que el calendario está listo y trae link a
  `calendar_review_url`.
- `email_reactivation_analysis.html` — invita a completar el análisis de marca pendiente,
  link al flujo de análisis (`COSMIC_BASE_URL` + la vista donde se inicia el análisis).

**E3. `EmailSender`, 2 métodos nuevos:**

```python
def send_reactivation_calendar(self, calendar: ContentCalendar) -> None: ...
def send_reactivation_analysis(self, user) -> None: ...
```

**E4. Nueva tarea `send_reactivation_emails_task()`** en `core/content_pipeline/tasks.py`.
Imports nuevos a agregar al archivo (no existen todavía): `from django.db.models import
Count` y `User` agregado al import ya existente de `core.tenant_management.models`
(`from core.tenant_management.models import Subscription` → `from
core.tenant_management.models import Subscription, User`).

```python
_REACTIVATION_FIRST_DAYS_CALENDAR = 3
_REACTIVATION_FIRST_DAYS_ANALYSIS = 2
_REACTIVATION_REPEAT_DAYS = 15


def send_reactivation_emails_task() -> None:
    now = timezone.now()

    # Trigger 1: calendario sin ningún post descargado
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

    # Trigger 2: registrado sin analizar marca
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

**E5. Management command** `core/tenant_management/management/commands/send_reactivation_emails.py`
— mismo patrón exacto que `expire_stale_trials.py` (comando delgado que solo llama a la
tarea). **Acción pendiente de Anuar en el servidor** (no es código de este plan, igual que
quedó pendiente para `expire_stale_trials`): agregar la línea de cron externa al repo que
llame `python manage.py send_reactivation_emails` una vez al día.

## Testing

- `core/content_pipeline/tests/test_scheduler.py` **ya existe con 4 tests que llaman
  `schedule_daily_emails(calendar)` con 1 solo argumento** — los 4 se rompen con el cambio
  de firma y deben actualizarse (no solo agregar tests nuevos):
  `test_schedule_daily_emails_enqueues_7_jobs`, `test_schedule_daily_emails_includes_day_1`,
  `test_schedule_daily_emails_does_not_reschedule_sent_posts` (agregar `day_start=1,
  day_end=7` o el rango que corresponda al escenario de cada test) y
  `test_schedule_daily_emails_clamps_past_due_to_5_minutes` (reescribir por completo — el
  clamp de 5 minutos ya no existe, reemplazar por un caso que verifique el fallback de 2h
  cuando el 7am objetivo ya pasó). Casos nuevos a agregar: rango de días excluye posts fuera
  del rango aunque sigan `PENDING` (reproduce HALLAZGO 81), y cálculo de 7am CDMX para un
  post cuyo `scheduled_at` es futuro (se programa exactamente a las 7am de ese día en
  `MEXICO_TZ`).
- `test_email_sender.py`: `send_daily` no debe enviar 2 veces si se invoca 2 veces
  concurrentemente sobre el mismo post ya `SENT` (simulable llamando 2 veces seguidas en el
  test, la segunda debe ser no-op); `send_month_ready` debe renderizar
  `email_month_ready.html` (no `email_initial.html`); ambos métodos nuevos de reactivación
  deben enviar con los templates correctos.
- `test_tasks.py`: `send_reactivation_emails_task` — no dispara para calendarios/usuarios
  recientes; dispara para los que exceden el umbral inicial; no vuelve a disparar antes de
  15 días; vuelve a disparar después de 15 días; no dispara para calendarios con al menos 1
  post descargado ni para usuarios con al menos 1 `AnalysisJob`.

## Fuera de alcance

- El 3er disparador original del punto 6 ("día 7 alcanzado") — ya resuelto por el pivote a
  pago único mensual, ver `ultimosCambios.md` punto 5.
- Cambiar el mecanismo de scheduling de RQ Scheduler a otra cosa — se mantiene
  `queue.enqueue_in`, solo cambia cómo se calcula el `delta`.
- Cualquier cambio a `email_initial.html` (correo del trial) — confirmado que su copy actual
  ("Tus 7 días...") es correcto para el trial y no se toca.
- El punto 7 (auditor de consistencia de marca en `image_generator.py`) y el resto de los
  hallazgos de Gemini sobre imagen/reel — se abordan en un spec separado (Grupo B, este
  mismo día).
