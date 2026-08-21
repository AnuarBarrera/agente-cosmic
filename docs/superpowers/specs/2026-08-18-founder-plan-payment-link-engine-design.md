# Motor de Payment Link por plan + migración de testers al plan Fundador

## Contexto y motivación

El 2026-08-18 (martes previo), Anuar mostró Cosmic a un tester real,
le ofreció el "plan Fundador" y aceptó. Ahora quiere migrar a **todos**
los usuarios con perfil Tester (en este sistema, "Tester" es
simplemente `Subscription.plan.name == 'Tester'`, no hay un campo de
rol separado) al plan Fundador.

Hoy existe un único Payment Link de Stripe, fijo en
`settings.STRIPE_PAYMENT_LINK_URL`, usado en 4 lugares del código
(`core/brand_dna/views.py:calendar_review_view`,
`core/brand_dna/auth_views.py:dashboard_view`,
`core/content_pipeline/email_sender.py:send_trial_expired` y
`:send_month_expired`). No existe ningún mecanismo para que planes
distintos usen links de Stripe distintos — necesario porque el plan
Fundador debe cobrar por un Payment Link propio, y a futuro Anuar
planea más variantes de plan (ej. "1, 3 o 5 reels").

Este documento cubre dos piezas, en un solo hilo de trabajo:

- **Parte A — motor de selección de Payment Link por plan**: pieza de
  infraestructura reusable, sin la cual la Parte B no puede
  funcionar.
- **Parte B — migración de testers al plan Fundador**: el primer caso
  de uso real que consume la Parte A.

Se prueba primero en este entorno de desarrollo (con los testers que
existen aquí) y solo después se corre el mismo comando en producción
vía la sesión `CosmicProd`.

## Decisiones ya confirmadas con Anuar (no reabrir)

- El "botón de regenerar contenido" al que se refería Anuar es el
  botón de pago existente en `calendar_review.html` ("Genera tu
  próximo mes →" / "Genera tu mes completo →"), **no** el botón de
  "Regenerar mis posts con estos cambios" de la edición de ADN de
  marca (que no tiene relación con Stripe).
- El plan Fundador copia todos los límites del plan "User" (pagado
  real) — `max_calendars_per_week`, `max_post_regenerations`,
  `max_post_edits`, `max_photo_prechecks_per_day`,
  `max_product_reference_photos`, `allows_sample_generation`. La
  única diferencia real es el precio/Payment Link.
- El link de pago se guarda en un campo nuevo del modelo `Plan`
  (`stripe_payment_link_url`), editable desde Django Admin — no un
  diccionario en `settings.py`. Vacío = usa el link global de
  `settings.STRIPE_PAYMENT_LINK_URL` (retrocompatible).
- Para los testers con más de un calendario (`AnalysisJob`), se
  conserva el más reciente (`created_at` más nuevo) y se eliminan los
  demás. Regla automática, no revisión manual caso por caso.
- Tras migrar, la `Subscription` del tester debe quedar con
  `status='trial_expired'` — el mismo estado que ya dispara
  `payment_needed=True` en `calendar_review_view` y en el dashboard —
  para que el botón de pago hacia el link de Fundador aparezca de
  inmediato.

## Parte A — Motor de Payment Link por plan

### A.1 — Modelo

`core/tenant_management/models.py`, clase `Plan`: nuevo campo

```python
stripe_payment_link_url = models.CharField(max_length=255, blank=True, default='')
```

Migración de Django estándar (`makemigrations tenant_management`).
Sin backfill necesario — el default `''` ya representa "usa el link
global", el mismo comportamiento de hoy para todos los planes
existentes.

### A.2 — Helper centralizado

`core/brand_dna/rate_limits.py`, junto a la función `get_user_plan`
que ya vive ahí:

```python
def get_payment_url(user) -> str:
    """Payment Link de Stripe para el plan actual del usuario, con
    client_reference_id ya adjunto. plan.stripe_payment_link_url vacio
    (default) cae al link global settings.STRIPE_PAYMENT_LINK_URL --
    retrocompatible, ningun plan existente necesita configurarse."""
    plan = get_user_plan(user)
    base_url = plan.stripe_payment_link_url or settings.STRIPE_PAYMENT_LINK_URL
    return f"{base_url}?client_reference_id={user.tenant_id}"
```

### A.3 — Call sites reemplazados

Los 4 lugares que hoy construyen `payment_url` manualmente con
`f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={...}"`
pasan a llamar `get_payment_url(user)` (o `job.user` según el
contexto disponible en cada uno):

1. `core/brand_dna/views.py:calendar_review_view` (línea ~378).
2. `core/brand_dna/auth_views.py:dashboard_view` (línea ~511).
3. `core/content_pipeline/email_sender.py:send_trial_expired` (línea ~122).
4. `core/content_pipeline/email_sender.py:send_month_expired` (línea ~167).

Ningún otro cambio de comportamiento — para el plan `User` (que no
tendrá `stripe_payment_link_url` configurado), el resultado es
idéntico al de hoy.

## Parte B — Migración de testers al plan Fundador

### B.1 — Management command

`core/tenant_management/management/commands/migrate_testers_to_founder.py`
(nuevo). Reproducible en dev y producción con el mismo código.

Argumentos:
- `--payment-link-url` (requerido): el Payment Link de Stripe del
  plan Fundador.
- `--apply` (flag, default ausente = dry-run): sin este flag, el
  comando solo **imprime** qué haría (qué tenants, qué calendarios se
  borrarían, con sus fechas) sin tocar la base de datos. Con
  `--apply`, ejecuta de verdad. Dado que borra calendarios, el
  dry-run es el comportamiento por defecto a propósito.

### B.2 — Lógica

1. **Plan Fundador**: `Plan.objects.get_or_create(name='Fundador', ...)`
   — si no existe, se crea copiando los límites del plan `User`
   (`Plan.objects.get(name='User')`) y fijando
   `stripe_payment_link_url` al valor de `--payment-link-url`. Si ya
   existe (ej. segunda corrida), se actualiza solo
   `stripe_payment_link_url` con el valor pasado — nunca se
   duplica.
2. Para cada `Subscription` con `plan__name='Tester'`:
   a. Localiza todos los `AnalysisJob` de `subscription.tenant`
      (vía `user.tenant`) ordenados por `created_at` descendente.
   b. Si hay más de uno, conserva el primero (más reciente) y elimina
      los demás con `.delete()` (cascada ya cubre
      `BrandDNA→ContentCalendar→ContentPost`).
   c. `subscription.plan = plan_fundador`.
   d. `subscription.status = 'trial_expired'`.
   e. `subscription.save(update_fields=['plan', 'status'])`.
3. En modo dry-run, los pasos b-e se calculan y se imprimen (tenant,
   cuántos calendarios se borrarían y sus fechas, plan/status
   anterior→nuevo) pero no se ejecuta ningún `.save()`/`.delete()`.

### B.3 — Fuera de alcance de este comando

- No envía ningún email ni notificación al tester sobre el cambio de
  plan — decisión implícita de Anuar (no lo mencionó); si hace falta
  un aviso, es un paso manual o un follow-up separado.
- No toca `AnalysisJob`/`ContentCalendar` de usuarios que no sean
  Tester.

## Testing

- `get_payment_url`: devuelve el link del plan cuando está seteado;
  cae al link global de `settings` cuando el plan lo tiene vacío.
- Los 4 call sites: test de que el HTML/email generado usa el link
  correcto según el plan del usuario (reemplazando/extendiendo los
  tests existentes de `payment_url` en `test_views.py` y los que
  existan para `dashboard_view`/`email_sender`).
- Management command: crea el plan Fundador si no existe (copiando
  límites de User); no lo duplica en una segunda corrida; poda
  correctamente dejando el `AnalysisJob` más reciente; cambia
  plan+status solo en modo `--apply`; en dry-run no modifica nada
  (verificar con `refresh_from_db`); ignora tenants sin plan Tester.

## Preguntas explícitamente sin resolver (fuera de este spec)

- El Payment Link real de Stripe del plan Fundador (Anuar lo
  proporciona al correr el comando, vía `--payment-link-url` — no se
  hardcodea en ningún lado).
- Si se necesita un aviso/email a los testers migrados — no
  mencionado por Anuar, no se construye en este spec.
