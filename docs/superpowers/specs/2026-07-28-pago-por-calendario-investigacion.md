# Pago por calendario (primera gratis, $199 c/u despues) — Investigacion

## Objetivo

Anuar pregunto si, en el modo "User" o asignando un nuevo perfil, se puede lograr
que un usuario genere multiples calendarios pero que solo la primera vez sea gratis,
y que despues de esa prueba gratis se cobre $199 por cada calendario/mes generado.
Este documento es el resultado de investigar el codigo actual (`core/tenant_management/`,
`core/brand_dna/`, `core/content_pipeline/`) para confirmar que tan lejos/cerca esta
la arquitectura de hoy de soportar ese modelo. **No es un diseño confirmado ni hay
decisiones de producto tomadas — es la base para decidir si se construye y como.**

## Conclusion corta

Hoy **no se puede** con lo que existe. El sistema de billing esta construido
integramente para **suscripcion recurrente de precio fijo** (una `Subscription`
Stripe por tenant, `paid_until` que se extiende 28 dias por pago). El modelo que
pide Anuar es **pago por evento/uso** (cada calendario es una unidad cobrable
independiente) — un paradigma distinto que no tiene soporte hoy. Si se implementa,
es un cambio de arquitectura de billing, no un flag nuevo en `Plan`.

## Estado actual (confirmado leyendo el codigo)

### 1. Modelo de planes y suscripcion

`core/tenant_management/models.py`:

- `Plan` (lineas 30-52) es un plan de **capacidades/limites de uso**, no de
  "features" tipo SaaS clasico:
  - `max_daily_interactions`, `max_monthly_interactions` (int)
  - `max_calendars_per_week` (int, default 2) — el nombre dice "por semana" pero
    la logica que lo usa **no aplica ninguna ventana temporal** (ver punto 2)
  - `max_post_regenerations`, `max_post_edits` (int)
  - `allows_sample_generation` (bool) — feature flag para el modo "muestra"
    (`sample_product_image`/`sample_product_reel`/`sample_reel`)
  - `price` (Decimal) — **existe en el modelo pero no se usa en ningun flujo de
    cobro real**; los planes reales seedeados (migraciones 0006, 0012, 0014) son
    "Free"/"User", "Tester", "Admin", todos con `price=0.00`
- `Subscription` (lineas 67-86), one-to-one con `TenantModel`:
  - `plan` (FK a `Plan`, `PROTECT`)
  - `status` (`CharField` libre, sin choices formales): `active`, `trialing`,
    `trial_expired`, `canceled`, `past_due`
  - `trial_ends_at` (DateTime, nullable)
  - `stripe_customer_id`, `stripe_subscription_id` (strings)
  - `cancel_at_period_end` (bool)
  - `paid_until` (DateTime, nullable)
  - `start_date`, `end_date`

Es **una** Subscription por tenant. No hay relacion N:1 entre "unidades pagadas"
y tenant, ni concepto de "saldo de calendarios comprados".

### 2. Como se decide hoy si un usuario puede generar un calendario nuevo

`core/brand_dna/rate_limits.py`:

- `get_user_plan(user)` (lineas 6-23): intenta `user.tenant.subscription.plan`;
  si falla, cae a mapeo por grupo de Django (`admin`→Admin, `tester`→Tester,
  `user`→User) o plan `User` por defecto.
- `can_create_calendar(user)` (lineas 26-30):
  ```python
  used = AnalysisJob.objects.filter(user=user).count()
  remaining = max(0, plan.max_calendars_per_week - used)
  ```
  **Hallazgo clave:** pese al nombre `max_calendars_per_week`, el conteo es
  **acumulado de por vida**, sin filtro de fecha ni de `status` ni de
  `generation_mode` — cuenta TODOS los `AnalysisJob` del usuario, incluyendo
  muestras de prospeccion y jobs fallidos. Es decir, el limite hoy **ya funciona
  de facto como un tope duro de N calendarios totales**, mas parecido
  conceptualmente a "pago por uso" de lo que el nombre del campo sugiere — pero
  no esta conectado a ningun cobro incremental, solo bloquea con un mensaje de
  "contacta soporte" (`core/brand_dna/views.py`, vista `analyze_submit`,
  ~linea 108-115).
- No hay middleware/decorator generico de limites — el chequeo es inline en esa
  vista.

Nota aparte (sin confirmar si esta conectada al flujo real): existe una capa DDD
paralela en `core/tenant_management/domain/aggregates.py` y
`application/services.py` con `enforce_usage_limits`, evento `UsageLimitExceeded`
(`core/shared/events.py:72`) y un modelo `usage_limits` como dict generico. El
flujo real de `brand_dna` usa `rate_limits.py` directamente, no esta capa — hay
que confirmar si esa capa DDD esta enrutada/usada en algo antes de construir
sobre ella.

### 3. Integracion con Stripe

`core/brand_dna/stripe_views.py` (ver tambien
`docs/superpowers/specs/2026-07-24-stripe-subscriptions-design.md`, que documenta
el diseño original de esto):

- **No hay creacion dinamica de Stripe Checkout Session ni Payment Intent en el
  backend.** El cobro usa un **Payment Link estatico** configurado fuera del
  codigo (`settings.STRIPE_PAYMENT_LINK_URL`), con `?client_reference_id={tenant_id}`
  agregado en la URL (usado en `views.py:275`, `auth_views.py:511`,
  `email_sender.py:122,167`).
- El webhook `stripe_webhook_view` escucha:
  - `checkout.session.completed` → `status='active'`, `trial_ends_at=None`,
    **`paid_until = now + 28 dias`** (fijo), guarda
    `stripe_customer_id`/`stripe_subscription_id`, dispara `generate_next_month`.
  - `customer.subscription.updated` → sincroniza `cancel_at_period_end`.
  - `customer.subscription.deleted` → `status='canceled'`.
  - `invoice.payment_failed` → `status='past_due'` + email.
  - `invoice.payment_succeeded` → `status='active'`.
- Todo esto asume que el Payment Link esta configurado en Stripe como **producto
  recurrente (Subscription)** — de ahi que lleguen eventos `customer.subscription.*`
  e `invoice.*`. **No hay manejo de `payment_intent.succeeded` ni de cargos
  unicos (one-time charge).**
- `manage_subscription_view` abre el Billing Portal de Stripe — tambien tipico
  de suscripciones recurrentes, no de pago por uso.

**Este es el punto mas importante para la decision arquitectonica**: "$199 por
cada calendario adicional" es conceptualmente un cargo por evento (metered
billing o pago unico), pero el codigo actual solo sabe manejar **una**
suscripcion Stripe recurrente por tenant. No hay: idempotencia por "unidad
comprada", no hay relacion entre un pago y "cuantos calendarios desbloquea", y
`checkout.session.completed` simplemente extiende `paid_until` 28 dias — no
incrementa ningun contador de "calendarios pagados".

### 4. Trial actual — es de tiempo, no de "1 uso"

- Se activa 7 dias de acceso *ilimitado* (dentro del tope del plan) recien
  cuando termina de generarse el PRIMER calendario completo
  (`content_generation_task`, `core/content_pipeline/tasks.py`, solo si
  `plan__name='User'`) — no al momento del signup.
- En el signup (`provision_tenant`, `auth_views.py`) se crea la `Subscription`
  con `plan=Free` y `status` default `'active'` — sin `trial_ends_at` todavia.
- No existe ningun campo booleano tipo `has_used_trial`. Que "ya uso su trial"
  se infiere indirectamente por el `status` y por el conteo de `AnalysisJob`.
- `expire_stale_trials_task` (invocada por el management command
  `expire_stale_trials.py`) recorre `Subscription` con `status='trialing' AND
  trial_ends_at<=now` (trial de 7 dias vencido) y tambien `status='active' AND
  paid_until<=now` (mes pagado vencido), manda email y pone
  `status='trial_expired'` en ambos casos.

Osea: el trial de hoy es "7 dias de acceso, empezando cuando termina el primer
calendario" — un concepto distinto a "1 generacion gratis y ya".

### 5. Contador de calendarios generados

- `AnalysisJob` (`core/brand_dna/models.py`) tiene `generation_mode` con choices
  `MODE_FULL`, `MODE_SAMPLE_IMAGE`, `MODE_SAMPLE_REEL`,
  `MODE_SAMPLE_PRODUCT_IMAGE`, `MODE_SAMPLE_PRODUCT_REEL` — pero **no tiene
  ningun campo contador propio** (ni `is_paid`, ni `charged`, ni
  `billing_period`).
- El conteo se calcula al vuelo con `.count()` en dos lugares
  (`rate_limits.py:28` para el limite del plan, y el dashboard de
  `auth_views.py` para mostrarlo en UI) — **ninguno filtra por
  `generation_mode=MODE_FULL`** (cuentan tambien las muestras gratis de
  prospeccion) ni por si el job fallo.
- No existe relacion "1 AnalysisJob = 1 cobro". No hay tabla de facturacion por
  unidad, ni `PaymentRecord`, ni `Purchase`, ni similar ligado a un
  `AnalysisJob` especifico.

## Brecha respecto al modelo que pide Anuar

Para "primera gratis + $199 por cada generacion adicional" faltaria:

1. **Checkout Session/Payment Intent dinamico por cobro** (no el Payment Link
   estatico generico) con metadata que identifique que unidad especifica se
   esta pagando — hoy Stripe solo sabe cobrar "la suscripcion del tenant",
   no "un calendario puntual".
2. **Contador explicito de calendarios completos generados**, filtrado por
   `generation_mode=MODE_FULL` (el actual mezcla muestras + calendarios reales
   + jobs fallidos).
3. **Flujo que, al llegar al limite gratis, mande a pagar $199 puntual** en vez
   de bloquear con el mensaje generico actual de "contacta soporte"
   (`analyze_submit`).
4. **Decision de producto**: ¿este modelo reemplaza la `Subscription`/
   `paid_until` actual para el plan "User", o coexisten? ¿Que pasa con el
   trial de 7 dias de acceso ilimitado que ya existe — desaparece, o la
   "primera generacion gratis" ES ese trial reencuadrado?

## Lo que SI es reutilizable

- El conteo de `AnalysisJob.objects.filter(user=user).count()` en
  `can_create_calendar()` ya funciona, por accidente de implementacion, como
  tope acumulado — es la pieza base de "cuantas veces ya genero", solo falta
  filtrar por `MODE_FULL` y conectarlo a un cobro en vez de a un bloqueo mudo.
- El patron de Payment Link + `client_reference_id` + webhook ya esta probado
  en produccion (aunque para suscripcion, no pago unico) — si se decide usar
  Stripe Checkout Session dinamica en vez de Payment Link estatico, es un
  cambio de flujo pero reusa la infraestructura de webhook/firma ya existente.

## Archivos clave para cualquier implementacion futura

`core/tenant_management/models.py` (Plan, Subscription) ·
`core/brand_dna/rate_limits.py` (can_create_calendar) ·
`core/brand_dna/views.py` / `auth_views.py` (analyze_submit, dashboard,
calendar_review_view) · `core/brand_dna/stripe_views.py` (webhook) ·
`core/brand_dna/models.py` (AnalysisJob) ·
`core/content_pipeline/tasks.py` (content_generation_task,
expire_stale_trials_task, generate_next_month) ·
`docs/superpowers/specs/2026-07-24-stripe-subscriptions-design.md` (diseño del
sistema de billing actual, para no contradecirlo sin querer)

## Siguiente paso

Sin decisiones tomadas todavia. Antes de diseñar la implementacion, definir con
Anuar: (a) si esto reemplaza o coexiste con la suscripcion mensual actual,
(b) que pasa si el usuario cancela/no paga a medio periodo del calendario que
ya empezo a generarse, (c) si $199 es por calendario completo (7 posts) o por
mes de contenido, y (d) si Payment Link estatico sigue siendo suficiente o hace
falta Checkout Session dinamica para amarrar cada pago a una unidad especifica.
