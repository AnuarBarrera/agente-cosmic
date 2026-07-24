# Pago puntual mensual + CTA temprano — Design

## Objetivo

Reemplazar el modelo de suscripción recurrente de Stripe (construido e implementado el
mismo día, ver `docs/superpowers/specs/2026-07-24-stripe-subscription-lifecycle-design.md`)
por un modelo de **pago puntual mensual**: cada pago genera un mes completo de
contenido de inmediato, sin cobro automático posterior. El usuario vuelve a pagar
cuando quiere más contenido — nosotros se lo recordamos, no lo cobramos solos. Se
agrega también un CTA temprano (día 1, justo después del primer análisis) para
capturar pagos antes de que termine el trial gratis, y 2 mejoras de UX del flujo diario
(descarga cancela el correo del día, quitar el zoom de imagen para forzar el flujo de
descarga).

## Contexto (lo que existe hoy, construido en sesiones anteriores del mismo día)

- **Trial de 7 días** (sin cambios en este diseño): `content_generation_task`
  (`core/content_pipeline/tasks.py:45`) crea el primer `ContentCalendar` y pone
  `Subscription.status='trialing'`, `trial_ends_at=now+7d`.
- **Payment Link de Stripe**: hoy en modo `subscription` (cobra mes a mes solo). Este
  diseño requiere que Anuar lo reconfigure en el Dashboard de Stripe a modo **pago
  puntual** (`Payment` en vez de `Subscription`) — paso manual, no de código. Sin ese
  cambio, `session.subscription` seguiría trayendo un id de suscripción real y Stripe
  seguiría cobrando solo cada mes, contradiciendo este diseño.
- **Webhook** (`core/brand_dna/stripe_views.py`): ya maneja `checkout.session.completed`,
  `customer.subscription.updated/deleted`, `invoice.payment_failed/succeeded`, todos
  verificados en vivo el mismo día contra Stripe real en modo `subscription`. Con pago
  puntual, Stripe **nunca** vuelve a mandar los últimos 4 tipos de evento (no hay
  objeto `Subscription` de Stripe ni facturas recurrentes que generarlos). Por decisión
  explícita de Anuar, ese código se queda tal cual, sin usarse — no se borra, por si en
  el futuro se vuelve a un modelo de suscripciones.
- **`Subscription`** (`core/tenant_management/models.py:67-86`) ya tiene
  `stripe_customer_id`, `stripe_subscription_id`, `cancel_at_period_end` — los últimos
  2 quedan sin uso activo bajo este diseño (mismo motivo de arriba).
- **`WeeklyFeedback`** (`core/content_pipeline/models.py:84-109`): hoy es el disparador
  de `generate_next_week` — el usuario dice "sí, continúa" en un banner y eso encola la
  generación. Se crea en `send_daily_email_task`
  (`core/content_pipeline/tasks.py`, cuando `post.day_number % 7 == 0`) y se consume en
  `calendar_feedback_api` (`core/brand_dna/views.py:521-559`, ya incluye el gate de
  pago agregado en la sesión de suscripciones). **Este diseño retira por completo este
  mecanismo** — ver sección 6.
- **`manage_subscription_view`** (Customer Portal, botón "Administrar mi suscripción"
  en `dashboard.html`) se queda — Anuar lo quiere conservar para que el cliente pueda
  actualizar su tarjeta, y por si algún día se reintroducen suscripciones. Cambia solo
  el texto del botón (ver sección 9).
- **`ContentPost.downloaded_at`** (`core/content_pipeline/models.py:57`) ya existe y ya
  se llena vía `mark_downloaded` — se reutiliza en la sección 10, no se crea nada nuevo.

## Decisiones de producto confirmadas (2026-07-24)

1. Stripe pasa de suscripción recurrente a **pago puntual** — Anuar reconfigura el
   Payment Link en su Dashboard.
2. El trial de 7 días no cambia.
3. **Cualquier pago confirmado** (temprano día 1, al vencer el trial, o cuando ya había
   pagado y vuelve a pagar tras vencer el mes) dispara la generación del mes **de
   inmediato**, vía webhook — nunca requiere que el usuario haga clic en "generar"
   aparte de pagar.
4. El estado de "se venció el mes pagado sin volver a pagar" usa el **mismo** mecanismo
   interno que "se venció el trial sin pagar" (`status='trial_expired'`, mismo gate) —
   solo cambia el copy del correo/banner según el caso.
5. El CTA temprano vive en **ambos**: `dashboard.html` y `calendar_review.html`.
6. El banner de pago **no tiene botón de rechazar** — no pagar ya es la forma de decir
   que no. `WeeklyFeedback` (rating + `continue_decision`) se **retira por completo**.
7. Los handlers de webhook de suscripción recurrente (`customer.subscription.*`,
   `invoice.*`) se quedan en el código sin usarse, no se borran.
8. En ningún copy visible al usuario se menciona la palabra "suscripción" — el mensaje
   es "generas tu mes al momento, ahorras tiempo", nunca "pago mensual automático".
9. Descargar una imagen/zip/video cancela el correo diario programado de ese post — no
   afecta los correos de día 7/vencimiento de mes (esos dependen del trigger de
   generación, no de la descarga).
10. El correo diario cambia de "Hoy es tu Día {número}" a un mensaje con fecha real,
    menos confuso.
11. Se quita el zoom/link de ampliar imagen (clic para abrir en pestaña nueva) — solo
    para imágenes. El video se queda exactamente igual.

## Diseño

### 1. Campo nuevo: `Subscription.paid_until`

```python
paid_until = models.DateTimeField(null=True, blank=True)
```

Se pone en `now + 28 días` cada vez que se confirma un pago (ver sección 3). Elegimos
28 días (4 semanas exactas) en vez de "un mes calendario" para mantener la misma
cadencia de días de la semana que ya usa `smart_schedule_dates` — evita que el mes 2
empiece un día de la semana distinto al mes 1.

Migración nueva en `core/tenant_management/migrations/`.

### 2. Webhook — `checkout.session.completed` genera el mes de inmediato

En `core/brand_dna/stripe_views.py`, el bloque que maneja este evento pasa de:

```python
updated = Subscription.objects.filter(tenant_id=tenant_id).update(
    status='active', trial_ends_at=None,
    stripe_customer_id=..., stripe_subscription_id=...,
)
```

a:

```python
updated = Subscription.objects.filter(tenant_id=tenant_id).update(
    status='active', trial_ends_at=None,
    paid_until=timezone.now() + timedelta(days=28),
    stripe_customer_id=getattr(session, 'customer', '') or '',
)
```

(`stripe_subscription_id` deja de capturarse activamente — en modo pago puntual
`session.subscription` no existe; si el campo se queda vacío no rompe nada, ningún
código lo lee ya de forma crítica bajo este diseño).

Justo después, si `updated`, se resuelve el `calendar_id` del tenant (reusando
`_job_for_tenant`, ya existe en el mismo archivo) y se encola `generate_next_month`
— sin importar si es el primer pago o el número 5, el tratamiento es idéntico, no hay
distinción de "primera vez".

### 3. `generate_next_month` — mismo patrón que `generate_next_week`, sin contador de mes

```python
def generate_next_month(calendar_id: str) -> None:
```

Sin parámetro de número de mes — a diferencia de `generate_next_week(calendar_id,
week_number)`, este calcula `base_day` directo del último post existente
(`calendar.posts.order_by('-day_number').first()`), igual que ya hace
`generate_next_week` internamente para las fechas. Genera 28 posts (reusa
`TextGenerator`/`ImageGenerator`/`ReelScriptGenerator`/`ReelGenerator` exactamente
igual). Al terminar, manda el correo de "tu mes está listo" (reusa
`EmailSender.send_week_ready`, cambiando el copy — ver sección 9).

### 4. Gate — se calcula al vuelo, sin tabla intermedia

`calendar_review_view` (`core/brand_dna/views.py:239`) reemplaza el cálculo de
`pending_feedback` por:

```python
payment_needed = subscription and (
    subscription.status == 'trial_expired'
    or (subscription.paid_until and subscription.paid_until <= timezone.now())
)
```

pasado al template como `payment_needed` (booleano). El banner en
`calendar_review.html` (hoy `{% if pending_feedback %}`) pasa a `{% if payment_needed
%}`, sin botón de rechazar, con el `payment_url` ya calculado igual que hoy
(`STRIPE_PAYMENT_LINK_URL?client_reference_id=<tenant_id>`).

**Se elimina por completo:** `calendar_feedback_api` (vista + URL
`api/calendar/<uuid:job_id>/feedback/` + su JS `submitFeedback` en
`calendar_review.html`), el modelo `WeeklyFeedback` y su creación en
`send_daily_email_task` (`if post.day_number % 7 == 0: WeeklyFeedback.objects...`), y
el bloque `{% if post.day_number|divisibleby:7 %}` de `email_daily.html`. Se elimina
también su tabla de la base de datos (migración de `RemoveField`/`DeleteModel`, o dejar
la tabla huérfana — decidir en el plan de implementación, no aquí).

### 5. Job de vencimiento — un chequeo más, mismo mecanismo

`expire_stale_trials_task` (`core/content_pipeline/tasks.py`) gana un segundo filtro
junto al que ya existe:

```python
Subscription.objects.filter(status='active', paid_until__lte=timezone.now())
```

Mismo tratamiento que un trial vencido (`status='trial_expired'` + correo), pero con
`EmailSender.send_month_expired` (método nuevo, mismo molde que `send_trial_expired`)
en vez de `send_trial_expired` — copy distinto (sección 9).

### 6. CTA temprano

Banner nuevo, visible en `dashboard.html` y `calendar_review.html` cuando
`subscription.status == 'trialing'` (nunca pagó, trial activo). Mismo `payment_url`
que el banner bloqueado. Copy en sección 9.

### 7. Copy — sin la palabra "suscripción" en ningún lado visible

- **CTA temprano:** *"¿Quieres el contenido de un mes completo? Paga $199 y genera tu
  mes desde hoy — ahorra horas de trabajo cada semana."*
- **Banner bloqueado (nunca pagó / mes vencido):** *"Genera tu próximo mes →"*
- **Correo de mes vencido** (`send_month_expired`, nuevo): *"Ya pasó un mes desde tu
  última generación de contenido. Genera un mes nuevo ahora y vuelve a sentir la
  experiencia de ganar tiempo."* con botón "Generar mi próximo mes →".
- **Botón del Customer Portal:** cambia de "Administrar mi suscripción" a
  **"Administrar mi método de pago"** (`dashboard.html`).
- El correo de trial vencido (`send_trial_expired`, ya existente) mantiene su copy
  actual — ya no menciona "suscripción" (dice "activa tu suscripción" hoy, se ajusta
  también a "Genera tu próximo mes" por consistencia con el resto).

### 8. Correo diario — fecha real en vez de número de día

`email_daily.html` y `EmailSender.send_daily` cambian:
- Asunto: de `🔔 Hoy es tu Día {{ post.day_number }}` a algo con la fecha real
  (`post.scheduled_at`), ej. `🔔 No se te olvide publicar hoy`.
- Cuerpo: de "Hoy es tu Día {{ post.day_number }}" a *"No se te olvide publicar el día
  de hoy ({{ post.scheduled_at|date:"d \d\e F" }})."*

### 9. Descarga cancela el correo diario

En `send_daily_email_task` (`core/content_pipeline/tasks.py`), justo después del
chequeo existente de `deleted_at`:

```python
if post.downloaded_at is not None:
    logger.info(f"Post {post_id} ya descargado — se omite el correo diario")
    return
```

No afecta ningún otro correo (el de mes listo, el de mes vencido, el de trial vencido
— ninguno depende de `downloaded_at`).

### 10. Quitar el zoom de imagen — solo imágenes, no video

En `calendar_review.html`, los 3 lugares donde una `<img>` está envuelta en `<a
href="..." target="_blank">` (imagen individual línea ~196, cada slide de carrusel
línea ~190, y la versión regenerada por JS línea ~387) pasan a ser `<img>` sueltas, sin
el `<a>` que envuelve. El `<video controls>` (línea ~184) no se toca — sigue igual.

## Fuera de alcance (v1 de este sub-proyecto)

- Reconfigurar el Payment Link en Stripe a modo pago puntual — paso manual de Anuar en
  el Dashboard, no hay código para esto.
- Borrar los handlers de webhook de suscripción recurrente — se quedan sin usar por
  decisión explícita (ver decisión #7).
- Cualquier lógica de prorrateo si alguien paga antes de que se le venza el mes actual
  — pagar siempre genera un mes nuevo desde hoy (`paid_until = now + 28d`), sin
  importar cuánto le quedaba del mes anterior.
- Cambiar el modelo `Plan`/precios — el precio ($199 en el copy) es solo texto de
  ejemplo, confirmar el monto real con Anuar antes de publicar el copy.

## Testing

- `checkout.session.completed`: confirma que `paid_until` queda en `now+28d` y que se
  encola `generate_next_month` (mock del enqueue), tanto para un tenant que nunca pagó
  como para uno que ya había pagado antes (mismo tratamiento en ambos casos).
- `generate_next_month`: genera 28 posts nuevos, con `day_number` continuando desde el
  último post existente — igual patrón de test que ya existe para
  `generate_next_week`.
- `calendar_review_view`: `payment_needed` es `True` cuando `status='trial_expired'` o
  cuando `paid_until` ya pasó; `False` en trial activo, `active` con `paid_until`
  futuro, o `past_due` (ese caso no debería bloquear — confirmar que `past_due` sigue
  sin bloquear bajo este diseño, mismo criterio que el plan anterior).
- `expire_stale_trials_task`: el nuevo filtro de `paid_until` vencido dispara
  `send_month_expired` (no `send_trial_expired`) y pasa a `trial_expired`.
- `send_daily_email_task`: con `downloaded_at` seteado, no se llama a `send_mail`; sin
  él, se comporta igual que hoy.
- Template: `calendar_review.html` con `payment_needed=True` muestra el banner sin
  botón de rechazar; con `False` no lo muestra. El CTA temprano aparece solo con
  `status='trialing'`.
- Confirmar que `calendar_feedback_api` y su URL ya no existen (404 si algo todavía le
  pega) y que ningún test viejo de `WeeklyFeedback`/`calendar_feedback_api` queda
  huérfano sin actualizar o eliminar.
