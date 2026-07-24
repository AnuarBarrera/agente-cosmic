# Suscripciones con Stripe — Design

## Objetivo

Cobrar por el uso continuado de Agente Cosmic después de una semana gratis. Hoy el
mecanismo de "generar la siguiente semana" ya existe y funciona (ver Contexto), pero
no tiene ningún gate de pago: cualquiera puede seguir diciendo "sí, continúa" para
siempre sin pagar. Este diseño conecta ese mecanismo existente con Stripe, sin
reconstruirlo.

## Contexto (código ya existente, no se toca su forma)

- `content_generation_task` (`core/content_pipeline/tasks.py:44-119`, modo
  `AnalysisJob.MODE_FULL`) crea el primer `ContentCalendar` justo después del
  análisis de marca — este es el "día 0" del trial. `generate_sample_task` (modo
  muestra, prospección) también crea un `ContentCalendar` pero **no** debe arrancar
  ningún trial.
- Cada día múltiplo de 7 (`post.day_number % 7 == 0`), `send_daily_email_task`
  (`core/content_pipeline/tasks.py:239-241`) crea un `WeeklyFeedback` pendiente
  (`get_or_create`, modelo en `core/content_pipeline/models.py:84-108`).
- `calendar_review.html` muestra un banner cuando hay `pending_feedback` (contexto
  armado en `core/brand_dna/views.py:250-287`), pidiendo rating + decisión
  "¿continuar?" (sí/no).
- `calendar_feedback_api` (`core/brand_dna/views.py:519-558`) recibe esa decisión.
  Si `continue_decision == 'yes'`, encola `generate_next_week` — **hoy sin ningún
  gate de pago**.
- `generate_next_week` (`core/content_pipeline/tasks.py:244-307`) genera la semana
  siguiente reusando el pipeline completo, y ya manda el correo "tu nueva semana ya
  está lista" (`EmailSender.send_week_ready`, línea 300) — este correo **no es parte
  de este trabajo**, ya existe y sigue funcionando igual.
- `Subscription` (`core/tenant_management/models.py:67-81`) tiene `tenant`, `plan`,
  `start_date`, `end_date`, `status` (default `'active'`) — sin `trial_ends_at`.
  Se crea hoy en `provision_tenant` (`core/brand_dna/auth_views.py:24-34`), en el
  registro, con `status='active'` — antes de que exista ningún calendario. Este
  diseño no cambia ese momento del registro.
- La app `core.tenant_management.interfaces` (DRF: `SubscriptionViewSet`, etc.) está
  confirmada en memoria (`project_deferred_security_decisions.md`) como **no
  enrutada** en `saas_chatbot/urls.py`. El código real y enrutado vive en
  `core.brand_dna` (`views.py`, `auth_views.py`, `urls.py`). El nuevo endpoint de
  webhook va ahí, no en `tenant_management/interfaces`.
- Ya existe una cuenta de Stripe y un Payment Link estático creado a mano (fuera de
  código). Hoy `core/content_pipeline/tasks.py` no importa `stripe` en ningún lado —
  cero integración de Stripe en código (confirmado por grep en sesiones previas).

## Fuera de alcance (v1)

- Facturación recurrente en sí (Stripe la maneja solo; nosotros solo escuchamos el
  webhook de pago inicial).
- Cancelación de suscripción desde la app, cambios de plan (upgrade/downgrade).
- Checkout Session dinámica vía API — se usa el Payment Link estático ya creado.
- Tabla de deduplicación de eventos de webhook (ver Riesgos aceptados).
- Los otros 2 disparadores de reactivación de `ultimosCambios.md` punto 6 (calendario
  sin descargar, registro sin análisis) — no dependen de pago, quedan para otro
  brainstorm independiente.

## Decisiones de producto (confirmadas con Anuar, 2026-07-24)

1. **Ancla del trial:** el día 0 es cuando se genera el primer calendario completo
   (`content_generation_task`, modo `MODE_FULL`), no el registro ni la verificación
   de email.
2. **Acceso durante el trial:** completo — mismo `Plan` que ya se asigna hoy, sin
   plan reducido.
3. **Payment Link:** el estático ya creado, con `?client_reference_id=<tenant_id>`
   agregado en la URL para amarrar el pago al tenant correcto. Sin Checkout Session
   dinámica.
4. **El gate de pago vive en `calendar_feedback_api`**, no en el job diario. El job
   diario solo notifica y degrada el estado; quien decide si `generate_next_week` se
   ejecuta es el propio endpoint de feedback, en el momento en que el usuario pide
   continuar.
5. **Regla de acceso:** `trial_expired` bloquea únicamente la generación de la
   siguiente semana. El contenido ya generado sigue accesible/descargable sin
   restricción — no se construye un sistema de control de acceso más amplio.

## Diseño

### 1. `Subscription.trial_ends_at` + nuevo valor de `status`

- Campo nuevo: `trial_ends_at = models.DateTimeField(null=True, blank=True)`.
- Nuevo valor válido de `status` (sigue siendo `CharField` libre, no choices
  formales — mismo patrón que hoy): `'trialing'` y `'trial_expired'`, sumados a los
  ya usados `'active'`/`'canceled'`/`'past_due'`.
- `content_generation_task`, justo después de crear el `ContentCalendar` (línea 56),
  agrega:
  ```python
  from core.tenant_management.models import Subscription
  Subscription.objects.filter(tenant=job.user.tenant).update(
      status='trialing',
      trial_ends_at=timezone.now() + timedelta(days=7),
  )
  ```
  Solo en `content_generation_task` (modo completo). `generate_sample_task` no se
  toca.
- Migración nueva en `core/tenant_management/migrations/`.

### 2. Endpoint de webhook de Stripe

- Nuevo archivo `core/brand_dna/stripe_views.py` (separado de `views.py`/
  `auth_views.py` por responsabilidad — es integración externa, no vistas de
  usuario) con una vista `stripe_webhook_view`, `@csrf_exempt`, solo POST.
- Verifica la firma con `stripe.Webhook.construct_event(payload, sig_header,
  settings.STRIPE_WEBHOOK_SECRET)`. Firma inválida → 400, log de advertencia, sin
  cambios de estado.
- Escucha `checkout.session.completed`. Lee `client_reference_id` del evento →
  `tenant_id`. Si no hay tenant con ese id: log de error con el id del evento,
  responde 200 igual (no queremos que Stripe reintente algo que nunca vamos a poder
  resolver).
- Si el tenant existe: `Subscription.objects.filter(tenant_id=tenant_id).update(
  status='active', trial_ends_at=None)`.
- Registrado en `core/brand_dna/urls.py`: `path('stripe/webhook/',
  stripe_views.stripe_webhook_view, name='stripe_webhook')`.
- Nuevas variables de entorno (mismo patrón `get_env` de `saas_chatbot/settings.py`):
  `STRIPE_WEBHOOK_SECRET`, `STRIPE_PAYMENT_LINK_URL`.
- Nueva dependencia: `stripe` en `requirements.txt`.

### 3. Gate de pago en `calendar_feedback_api`

- En `core/brand_dna/views.py:552` (donde hoy dice `if feedback.continue_decision ==
  WeeklyFeedback.CONTINUE_YES:`), antes de encolar `generate_next_week`, se agrega:
  ```python
  subscription = calendar.brand_dna.job.user.tenant.subscription
  if subscription.status == 'trial_expired':
      return JsonResponse({
          'status': 'payment_required',
          'payment_url': f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={calendar.brand_dna.job.user.tenant_id}",
      })
  # status in ('active', 'trialing') → comportamiento actual sin cambios
  next_week = feedback.week_number + 1
  ...
  ```
- El rating/comentario del `WeeklyFeedback` se guarda siempre, incluso si el gate
  bloquea la generación — no se pierde el feedback del usuario.
- `calendar_review.html`: el JS que maneja la respuesta de `/api/calendar/.../
  feedback/` (línea ~441) gana un caso nuevo: si `status === 'payment_required'`,
  muestra el link de pago (`payment_url`) en vez del toast de éxito actual.

### 4. Job diario de RQ: aviso + degradación de trial

- Nueva función `expire_stale_trials_task()` en `core/content_pipeline/tasks.py`
  (mismo archivo que ya tiene `schedule_daily_emails`/`send_daily_email_task`,
  mismo patrón de scheduling con `django_rq`).
- Lógica:
  ```python
  expired = Subscription.objects.filter(status='trialing', trial_ends_at__lte=timezone.now())
  for sub in expired:
      EmailSender().send_trial_expired(tenant=sub.tenant)
      sub.status = 'trial_expired'
      sub.save(update_fields=['status'])
  ```
- Idempotente por construcción: en cuanto una `Subscription` pasa a
  `trial_expired`, deja de aparecer en el filtro `status='trialing'` al día
  siguiente, así que el correo se manda una sola vez.
- Se programa diario (mismo mecanismo que el job de correos diarios ya
  existente) — **cuidado con el incidente ya documentado de ráfaga de scheduler
  tras un apagón** (`project_rq_scheduler_burst.md`): no reintroducir ese patrón sin
  guard de staleness al conectar este job al scheduler.
- Nuevo método `EmailSender.send_trial_expired(tenant)` — mismo patrón que
  `send_initial`/`send_week_ready`/`send_daily` (`core/content_pipeline/
  email_sender.py`), nuevo template `content_pipeline/email_trial_expired.html`,
  con copy "paga para continuar" y el Payment Link con `client_reference_id`.

## Riesgos aceptados (v1, documentados a propósito)

- **Sin deduplicación de eventos de webhook.** Si Stripe reenvía
  `checkout.session.completed` (retry normal de su lado), volver a marcar
  `status='active'` es inofensivo — no hay side effect distinto de "ya estaba
  activo". Si en el futuro se agregan efectos no idempotentes (ej. incrementar un
  contador de pagos), esto habrá que revisarlo.
- **Sin manejo de `checkout.session.expired`, `invoice.payment_failed`, ni eventos de
  cancelación/downgrade.** Fuera de alcance v1, ver "Fuera de alcance".
- **Ningún guard de staleness en el job diario nuevo** más allá de la idempotencia
  natural del cambio de estado — mismo patrón de riesgo aceptado que
  `schedule_daily_emails` ya tiene documentado.

## Testing

- `content_generation_task`: modo completo deja `Subscription.status='trialing'` +
  `trial_ends_at` ≈ ahora+7 días; `generate_sample_task` no toca el estado del
  trial.
- `stripe_webhook_view`: firma válida + `client_reference_id` válido → `active`,
  `trial_ends_at=None`; firma inválida → 400 sin cambios; tenant inexistente → 200 +
  log, sin excepción; evento repetido → sin efectos secundarios nuevos.
- `calendar_feedback_api`: `continue_decision='yes'` con `status` `'active'` o
  `'trialing'` → encola `generate_next_week` (sin regresión del comportamiento
  actual); con `status='trial_expired'` → no encola, responde
  `status: 'payment_required'` con `payment_url`; el `WeeklyFeedback` se guarda en
  ambos casos.
- `expire_stale_trials_task`: trial vencido sin pago → correo enviado una vez +
  `status` pasa a `trial_expired`; trial vencido pero ya `active` (pagó antes de que
  corriera el job) → no aparece en el filtro, no hace nada.
