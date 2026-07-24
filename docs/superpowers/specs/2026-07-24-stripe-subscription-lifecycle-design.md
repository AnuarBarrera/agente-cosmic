# Ciclo de vida de suscripciones Stripe (cancelación, sincronización, visibilidad) — Design

## Objetivo

El plan anterior (`docs/superpowers/specs/2026-07-24-stripe-subscriptions-design.md`) conectó
Stripe con el trial de 7 días, pero dejó explícitamente fuera de alcance "cancelación de
suscripción desde la app". Al probar con un pago real en modo test se confirmaron 4 gaps
reales:

1. No existe ningún mecanismo de cancelación self-service — solo manual en el Dashboard de
   Stripe.
2. `Subscription` no está registrado en el Django Admin — cero visibilidad desde la app.
3. **Bug confirmado**: `deactivate_account_view` (`core/brand_dna/auth_views.py:550-581`)
   marca `Subscription.status='canceled'` solo en nuestra base de datos — nunca cancela la
   suscripción real en Stripe. Un usuario que elimina su cuenta sigue siendo cobrado
   indefinidamente, sin sesión para pararlo.
4. El webhook actual (`core/brand_dna/stripe_views.py`, plan anterior) solo escucha
   `checkout.session.completed`. Si una suscripción se cancela o falla el cobro por
   cualquier otra vía, nuestro `status` nunca se entera — se queda `'active'` para
   siempre, y como el gate de `calendar_feedback_api` solo bloquea en `'trial_expired'`,
   un usuario sin suscripción real seguiría generando contenido gratis indefinidamente.

Este diseño resuelve los 4 juntos porque comparten la misma causa raíz: mantener
`Subscription.status` sincronizado con la realidad de Stripe, y darle al usuario/admin
control real sobre eso.

## Contexto (código ya existente del plan anterior, no se reconstruye)

- `Subscription` (`core/tenant_management/models.py:67-83`) ya tiene `trial_ends_at`
  (agregado en el plan anterior). Hoy **no** guarda ningún identificador de Stripe — el
  webhook actual solo usa `client_reference_id` (viene del Payment Link) para encontrar
  al tenant, y ese campo **no existe** en los eventos de ciclo de vida de suscripción
  (`customer.subscription.*`, `invoice.*`) — esos traen `customer` (el id de cliente de
  Stripe), no `client_reference_id`. Sin persistir el `customer_id` desde el primer
  webhook, es estructuralmente imposible matchear los eventos futuros a un tenant.
- `core/brand_dna/stripe_views.py` (plan anterior): `stripe_webhook_view` verifica firma y
  solo maneja `checkout.session.completed`. Se extiende, no se reescribe.
- `core/brand_dna/views.py` `calendar_feedback_api` (plan anterior): el gate hoy es
  ```python
  if subscription and subscription.status == 'trial_expired':
  ```
  Se amplía a incluir `'canceled'`.
- `core/brand_dna/auth_views.py:550-581` `deactivate_account_view`: hoy marca
  `sub.status = 'canceled'` sin tocar Stripe. Se le agrega la llamada real a la API.
- `core/content_pipeline/email_sender.py`: patrón ya establecido
  (`render_to_string` + `send_mail` + `EMAILS_SENT.labels(...).inc()`), se agrega un
  método nuevo siguiendo el mismo molde.
- `core/tenant_management/admin.py:155-162`: registra `User`, `InvitationCode`, `Plan`,
  `AnalysisJob`, `WeeklyFeedback`, `SecurityEvent`, `TOTPDevice`, `Group` — **no**
  `Subscription` ni `TenantModel`.
- Resolución de "tenant → AnalysisJob representativo para mandar correo" ya existe en
  `expire_stale_trials_task` (`core/content_pipeline/tasks.py`, plan anterior):
  `AnalysisJob.objects.filter(user__tenant=sub.tenant, generation_mode=AnalysisJob.MODE_FULL).order_by('-created_at').first()`
  — se reutiliza el mismo patrón para el correo de cobro fallido.

## Decisiones de producto (confirmadas con Anuar, 2026-07-24)

1. **Mecanismo de cancelación:** Customer Portal de Stripe (hosteado por Stripe), no un
   botón/página propia. Nuestro código solo genera un link de sesión temporal por
   petición (`stripe.billing_portal.Session.create`) y redirige — nunca un link fijo
   compartido.
2. **Corte de acceso:** al cancelar, el servicio sigue hasta el final del periodo ya
   pagado (`cancel_at_period_end=True`), no corte inmediato.
3. **Eliminar cuenta:** SIEMPRE cancela la suscripción real en Stripe si existe una
   activa (respetando el mismo corte a fin de periodo), no solo el registro interno.
4. **Cobro fallido:** solo se avisa por correo, sin bloquear acceso — se deja que Stripe
   agote sus reintentos automáticos (Smart Retries) antes de que el acceso se vea
   afectado (lo cual ocurre solo cuando Stripe finalmente cancela y dispara
   `customer.subscription.deleted`).
5. **Admin:** solo lectura. Para cancelar o modificar se sigue usando el Dashboard de
   Stripe o el Customer Portal — el admin es solo para consulta rápida sin salir de la
   app.

## Diseño

### 1. Campos nuevos en `Subscription`

```python
stripe_customer_id = models.CharField(max_length=255, blank=True, default='')
stripe_subscription_id = models.CharField(max_length=255, blank=True, default='')
cancel_at_period_end = models.BooleanField(default=False)
```

`status` gana el valor `'past_due'` como valor realmente usado (ya estaba mencionado en
el comentario del campo desde el plan anterior, pero ningún código lo escribía).

### 2. Webhook — captura de identificadores en `checkout.session.completed`

Se agrega, dentro del bloque existente que ya maneja este evento:

```python
Subscription.objects.filter(tenant_id=tenant_id).update(
    status='active',
    trial_ends_at=None,
    stripe_customer_id=session.customer,
    stripe_subscription_id=session.subscription,
)
```
(`session.customer`/`session.subscription` via `getattr` con default `''` — mismo
patrón defensivo ya corregido en el plan anterior tras el bug real encontrado con
`session.get(...)`, que confirmó que los objetos de Stripe solo soportan acceso por
atributo, no `.get()` de dict.)

### 3. Webhook — 4 eventos nuevos

Todos resuelven el tenant vía `stripe_customer_id` (no `client_reference_id`, que no
existe en estos eventos):

```python
def _subscription_for_customer(customer_id):
    if not customer_id:
        return None
    return Subscription.objects.filter(stripe_customer_id=customer_id).first()
```

- **`customer.subscription.updated`**: sincroniza `cancel_at_period_end` desde
  `event['data']['object'].cancel_at_period_end`. No toca `status` — el acceso sigue
  mientras el periodo pagado no haya terminado.
- **`customer.subscription.deleted`**: `status='canceled'`, `cancel_at_period_end=False`.
  Este es el evento que de verdad corta el acceso (vía el gate actualizado, ver
  sección 4) — dispara tanto en cancelación con periodo agotado como en cancelación
  inmediata.
- **`invoice.payment_failed`**: `status='past_due'` + envía
  `EmailSender.send_payment_failed(job, brand_dna)` (ver sección 6). No bloquea nada.
- **`invoice.payment_succeeded`**: `status='active'`. Recupera de `past_due` si el
  reintento de Stripe funciona tras actualizar tarjeta; en el pago inicial es
  inofensivo (ya estaba `active` desde `checkout.session.completed`).

Si `_subscription_for_customer` no encuentra nada (customer_id desconocido): mismo
patrón que hoy — log de error, responder 200 igual, sin crashear.

### 4. Gate de `calendar_feedback_api` — actualización de un valor

```python
if subscription and subscription.status in ('trial_expired', 'canceled'):
```
(antes: solo `== 'trial_expired'`). `'past_due'` no aparece en esta condición — no
bloquea, por diseño.

### 5. Endpoint de Customer Portal

Nueva vista `manage_subscription_view` (mismo archivo `core/brand_dna/stripe_views.py`),
`@login_required`, `POST` únicamente:

```python
subscription = getattr(getattr(request.user, 'tenant', None), 'subscription', None)
if not subscription or not subscription.stripe_customer_id:
    return redirect('dashboard')
portal_session = stripe.billing_portal.Session.create(
    customer=subscription.stripe_customer_id,
    return_url=settings.COSMIC_BASE_URL + reverse('dashboard'),
)
return redirect(portal_session.url)
```
Registrada en `core/brand_dna/urls.py` como `path('dashboard/suscripcion/',
stripe_views.manage_subscription_view, name='manage_subscription')`.

`dashboard.html` gana un botón "Administrar mi suscripción" (form POST a esa ruta),
visible solo cuando `subscription.stripe_customer_id` no está vacío — un usuario que
sigue en trial y nunca pagó no tiene nada que administrar en Stripe todavía.

**Prerrequisito manual (no es código):** activar el Customer Portal en el Dashboard de
Stripe (Settings → Billing → Customer portal) antes de que este botón funcione en
producción — mismo tipo de paso manual que ya existe para el Payment Link y el webhook
endpoint.

### 6. Eliminar cuenta cancela de verdad

En `deactivate_account_view`, antes de la sección que ya marca `sub.status = 'canceled'`:

```python
if sub.stripe_subscription_id and sub.status not in ('canceled', 'trial_expired'):
    try:
        stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=True)
    except Exception:
        pass
```
(mismo patrón defensivo `try/except: pass` que ya usa esa vista para el logout de JWT —
no bloquear la eliminación de cuenta si Stripe falla). El resto de la vista sigue igual
(`sub.status = 'canceled'` local); el webhook `customer.subscription.deleted` reconfirma
esto más adelante cuando el periodo real termine.

### 7. Correo de cobro fallido

`EmailSender.send_payment_failed(self, job: AnalysisJob, brand_dna: BrandDNA) -> None`
— mismo molde que `send_trial_expired` (Plan anterior), mismo patrón de resolución
tenant→job que `expire_stale_trials_task`. El link del correo va a
`settings.COSMIC_BASE_URL + reverse('dashboard')`, **no** directo a una sesión de
Customer Portal — esas sesiones son de un solo uso y expiran rápido, no sirven
embebidas en un correo que se puede abrir horas/días después. El usuario entra al
dashboard y genera su propio link fresco al hacer clic en "Administrar mi suscripción".
Copy: avisa que el cobro falló, sin alarmismo, invita a actualizar el método de pago.

### 8. Admin de solo lectura

`core/tenant_management/admin.py`: se registra `SubscriptionAdmin` con
`list_display = ('tenant', 'plan', 'status', 'trial_ends_at', 'cancel_at_period_end', 'stripe_customer_id')`,
todos los campos en `readonly_fields`, y `has_add_permission`/`has_delete_permission`
retornando `False` — solo consulta, ninguna acción destructiva posible desde ahí.

## Fuera de alcance (v1 de este sub-proyecto)

- Cambios de plan (upgrade/downgrade) — no existe ningún plan pago más que el actual.
- Reembolsos — se manejan manualmente en Stripe si algún día hace falta.
- Retry/backoff propio sobre cobros fallidos — Stripe ya lo hace (Smart Retries); no se
  construye lógica paralela.
- Acciones desde el admin (cancelar/reactivar desde ahí) — decisión explícita de dejarlo
  solo lectura.

## Testing

- `checkout.session.completed`: además de lo ya cubierto en el plan anterior, verificar
  que `stripe_customer_id`/`stripe_subscription_id` quedan guardados.
- `customer.subscription.updated`: `cancel_at_period_end` se sincroniza, `status` no
  cambia.
- `customer.subscription.deleted`: `status` pasa a `'canceled'`.
- `invoice.payment_failed`: `status` pasa a `'past_due'`, se llama
  `send_payment_failed` una vez; tenant desconocido no crashea (mismo patrón que los
  demás handlers).
- `invoice.payment_succeeded`: `status` pasa a `'active'`.
- Gate: `status='canceled'` bloquea igual que `'trial_expired'`; `status='past_due'` NO
  bloquea.
- `manage_subscription_view`: sin `stripe_customer_id` redirige a dashboard sin llamar a
  Stripe; con `stripe_customer_id` llama a `stripe.billing_portal.Session.create` con
  los argumentos correctos y redirige a la URL que devuelve.
- `deactivate_account_view`: con suscripción activa, llama a
  `stripe.Subscription.modify(..., cancel_at_period_end=True)`; si Stripe lanza una
  excepción, la cuenta se elimina igual (no bloquea el flujo existente).
- **Verificación en vivo (no solo tests automatizados):** la suscripción de prueba real
  que quedó activa en modo test durante la sesión anterior (Payment Link
  `https://buy.stripe.com/test_eVqcN65vG95T73j9lJaEE00`, cobrando $10 MXN/mes) se deja
  activa a propósito para usarla como caso de prueba real de este flujo — una vez
  implementado, cancelar desde el Customer Portal generado por la app y confirmar que
  el webhook `customer.subscription.deleted` la sincroniza correctamente.
