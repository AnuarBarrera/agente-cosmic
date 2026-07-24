# Ciclo de vida de suscripciones Stripe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conectar la cancelación real de Stripe (Customer Portal), la sincronización de
eventos de ciclo de vida (cancelación, cobro fallido/exitoso) y la visibilidad en el admin,
cerrando el hueco donde una cuenta eliminada seguía siendo cobrada indefinidamente.

**Architecture:** Se agregan 3 campos a `Subscription` (`stripe_customer_id`,
`stripe_subscription_id`, `cancel_at_period_end`) capturados desde el primer webhook, se
extiende `stripe_webhook_view` con 4 eventos nuevos que resuelven el tenant vía
`stripe_customer_id` (no `client_reference_id`, que no existe en esos eventos), se agrega
un endpoint que genera sesiones de Customer Portal por petición, se actualiza el gate de
`calendar_feedback_api` para bloquear también `status='canceled'`, se hace que eliminar
cuenta cancele de verdad en Stripe, y se registra `Subscription` en el admin (solo lectura).

**Tech Stack:** Django 5.2, librería `stripe` (ya instalada, plan anterior).

## Global Constraints

- Spec de referencia: `docs/superpowers/specs/2026-07-24-stripe-subscription-lifecycle-design.md`.
- Los objetos de Stripe (`event['data']['object']`, y todo lo que cuelga de ahí) **solo
  soportan acceso por atributo/`getattr`, nunca `.get()` de dict** — confirmado con un bug
  real en el plan anterior. Todo el código nuevo en `stripe_views.py` debe usar
  `getattr(obj, 'campo', default)`, nunca `obj.get('campo')`.
- Los eventos de ciclo de vida de suscripción (`customer.subscription.*`,
  `invoice.*`) **no traen `client_reference_id`** — solo `customer` (el id de cliente de
  Stripe). Todo el matching de estos eventos a un tenant debe ser vía
  `Subscription.stripe_customer_id`, nunca vía `client_reference_id`.
- Regla de acceso del gate (`calendar_feedback_api`), tras este plan: bloquea cuando
  `status in ('trial_expired', 'canceled')`. `'past_due'` **nunca** bloquea — es solo
  informativo (correo de aviso), Stripe ya reintenta el cobro automáticamente.
- Cancelación: siempre `cancel_at_period_end=True` (nunca cancelación inmediata) — tanto
  desde el Customer Portal (lo maneja Stripe solo) como desde `deactivate_account_view`
  (llamada explícita a `stripe.Subscription.modify`).
- El admin de `Subscription` es de solo lectura — sin `has_add_permission`, con todos los
  campos en `readonly_fields`, mismo patrón que `AnalysisJobAdmin`/`WeeklyFeedbackAdmin`
  en `core/tenant_management/admin.py`.
- Fuera de alcance (no crear tareas para esto): cambios de plan (upgrade/downgrade),
  reembolsos, lógica de reintento propia sobre cobros fallidos (Stripe ya lo hace),
  acciones (cancelar/reactivar) desde el admin.
- No se cancela manualmente la suscripción de prueba real que quedó activa en modo test
  (Payment Link `https://buy.stripe.com/test_eVqcN65vG95T73j9lJaEE00`) — se deja a
  propósito para verificar este plan en vivo al final.

---

### Task 1: Campos nuevos en `Subscription` + migración

**Files:**
- Modify: `core/tenant_management/models.py:67-83` (clase `Subscription`)
- Create: `core/tenant_management/migrations/0022_subscription_stripe_fields.py`
- Test: `core/tenant_management/tests/test_models.py`

**Interfaces:**
- Produces: `Subscription.stripe_customer_id` (`CharField`, `blank=True, default=''`),
  `Subscription.stripe_subscription_id` (`CharField`, `blank=True, default=''`),
  `Subscription.cancel_at_period_end` (`BooleanField`, `default=False`) — usados por las
  Tasks 2, 3, 5 y 6.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `core/tenant_management/tests/test_models.py`:

```python
def test_subscription_stripe_fields_default_empty():
    plan = Plan.objects.create(name='Plan Test Stripe Fields')
    tenant = TenantModel.objects.create(name='Tenant Stripe Fields', status='active')
    sub = Subscription.objects.create(tenant=tenant, plan=plan)
    assert sub.stripe_customer_id == ''
    assert sub.stripe_subscription_id == ''
    assert sub.cancel_at_period_end is False


def test_subscription_stripe_fields_accept_values():
    plan = Plan.objects.create(name='Plan Test Stripe Fields 2')
    tenant = TenantModel.objects.create(name='Tenant Stripe Fields 2', status='active')
    sub = Subscription.objects.create(
        tenant=tenant, plan=plan,
        stripe_customer_id='cus_123', stripe_subscription_id='sub_123',
        cancel_at_period_end=True,
    )
    sub.refresh_from_db()
    assert sub.stripe_customer_id == 'cus_123'
    assert sub.stripe_subscription_id == 'sub_123'
    assert sub.cancel_at_period_end is True
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/tenant_management/tests/test_models.py::test_subscription_stripe_fields_default_empty -v`
Expected: FAIL — `TypeError: Subscription() got unexpected keyword arguments: 'stripe_customer_id'`.

- [ ] **Step 3: Agregar los campos al modelo**

En `core/tenant_management/models.py`, reemplazar la clase `Subscription` completa
(líneas 67-83):

```python
class Subscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(TenantModel, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    start_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=50, default='active')  # e.g., active, trialing, trial_expired, canceled, past_due
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    stripe_customer_id = models.CharField(max_length=255, blank=True, default='')
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default='')
    cancel_at_period_end = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.tenant.name} - {self.plan.name}"

    class Meta:
        db_table = 'subscriptions'
        verbose_name = 'Subscription'
        verbose_name_plural = 'Subscriptions'
```

- [ ] **Step 4: Generar la migración**

Run: `docker compose exec backend python manage.py makemigrations tenant_management`
Expected: crea `core/tenant_management/migrations/0022_subscription_stripe_fields.py`
(si el nombre automático difiere, renombrar el archivo a ese nombre). Verificar que el
contenido coincide con esto:

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_management', '0021_subscription_trial_ends_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='stripe_customer_id',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='subscription',
            name='stripe_subscription_id',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='subscription',
            name='cancel_at_period_end',
            field=models.BooleanField(default=False),
        ),
    ]
```

- [ ] **Step 5: Aplicar la migración y correr los tests**

Run: `docker compose exec backend python manage.py migrate tenant_management`
Run: `docker compose exec backend pytest core/tenant_management/tests/test_models.py -v`
Expected: PASS (4 tests: 2 preexistentes de `trial_ends_at` + 2 nuevos).

- [ ] **Step 6: Commit**

```bash
git add core/tenant_management/models.py core/tenant_management/migrations/0022_subscription_stripe_fields.py core/tenant_management/tests/test_models.py
GIT_EDITOR=true git commit -m "feat(subscriptions): agrega stripe_customer_id, stripe_subscription_id y cancel_at_period_end"
```

---

### Task 2: Webhook — captura de identificadores + eventos de cancelación

**Files:**
- Modify: `core/brand_dna/stripe_views.py`
- Modify: `core/brand_dna/tests/test_stripe_views.py`

**Interfaces:**
- Consumes: `Subscription.stripe_customer_id`, `stripe_subscription_id`,
  `cancel_at_period_end` (Task 1).
- Produces: `_subscription_for_customer(customer_id)` — helper reutilizado por la Task 3.

- [ ] **Step 1: Escribir los tests que fallan**

En `core/brand_dna/tests/test_stripe_views.py`, reemplazar `_fake_event` (actualmente
solo cubre `checkout.session.completed`) para incluir `customer`/`subscription`:

```python
def _fake_event(event_id, tenant_id, event_type='checkout.session.completed', customer='cus_test1', subscription='sub_test1'):
    return {
        'id': event_id,
        'type': event_type,
        'data': {'object': SimpleNamespace(
            client_reference_id=str(tenant_id), customer=customer, subscription=subscription,
        )},
    }


def _fake_subscription_event(event_id, customer_id, cancel_at_period_end=False):
    return {
        'id': event_id,
        'type': 'customer.subscription.updated',
        'data': {'object': SimpleNamespace(customer=customer_id, cancel_at_period_end=cancel_at_period_end)},
    }
```

Actualizar el test existente `test_webhook_valid_signature_activates_subscription` para
verificar también los identificadores nuevos (reemplazar el cuerpo de la función):

```python
@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_valid_signature_activates_subscription(tenant_with_subscription):
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_event('evt_1', tenant_with_subscription.id)):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'active'
    assert sub.trial_ends_at is None
    assert sub.stripe_customer_id == 'cus_test1'
    assert sub.stripe_subscription_id == 'sub_test1'
```

Agregar al final del archivo:

```python
@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_subscription_updated_syncs_cancel_at_period_end(tenant_with_subscription):
    tenant_with_subscription.subscription.stripe_customer_id = 'cus_test1'
    tenant_with_subscription.subscription.status = 'active'
    tenant_with_subscription.subscription.save(update_fields=['stripe_customer_id', 'status'])
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_subscription_event('evt_4', 'cus_test1', cancel_at_period_end=True)):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.cancel_at_period_end is True
    assert sub.status == 'active'


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_subscription_deleted_cancels(tenant_with_subscription):
    tenant_with_subscription.subscription.stripe_customer_id = 'cus_test1'
    tenant_with_subscription.subscription.status = 'active'
    tenant_with_subscription.subscription.cancel_at_period_end = True
    tenant_with_subscription.subscription.save(update_fields=['stripe_customer_id', 'status', 'cancel_at_period_end'])
    fake_event = {
        'id': 'evt_5',
        'type': 'customer.subscription.deleted',
        'data': {'object': SimpleNamespace(customer='cus_test1')},
    }
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event', return_value=fake_event):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'canceled'
    assert sub.cancel_at_period_end is False


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_subscription_updated_unknown_customer_returns_200_and_logs(tenant_with_subscription):
    fake_event = {
        'id': 'evt_6',
        'type': 'customer.subscription.updated',
        'data': {'object': SimpleNamespace(customer='cus_unknown', cancel_at_period_end=True)},
    }
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event', return_value=fake_event):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py -v`
Expected: FAIL — `test_webhook_valid_signature_activates_subscription` falla en las 2
aserciones nuevas (`stripe_customer_id`/`stripe_subscription_id` siguen vacíos); los 3
tests nuevos fallan porque `customer.subscription.updated`/`.deleted` no se manejan
todavía.

- [ ] **Step 3: Implementar**

Reemplazar `core/brand_dna/stripe_views.py` completo:

```python
import logging
import stripe
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from core.tenant_management.models import Subscription

logger = logging.getLogger(__name__)


def _subscription_for_customer(customer_id):
    if not customer_id:
        return None
    return Subscription.objects.filter(stripe_customer_id=customer_id).first()


@csrf_exempt
@require_POST
def stripe_webhook_view(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        logger.warning(f"Webhook de Stripe con firma invalida: {e}")
        return HttpResponseBadRequest('Invalid signature')

    event_type = event['type']

    if event_type == 'checkout.session.completed':
        session = event['data']['object']
        tenant_id = getattr(session, 'client_reference_id', None)
        updated = Subscription.objects.filter(tenant_id=tenant_id).update(
            status='active',
            trial_ends_at=None,
            stripe_customer_id=getattr(session, 'customer', '') or '',
            stripe_subscription_id=getattr(session, 'subscription', '') or '',
        )
        if not updated:
            logger.error(f"Webhook de Stripe: no se encontro tenant {tenant_id} para el evento {event['id']}")
        else:
            logger.info(f"Suscripcion activada para tenant {tenant_id} via Stripe")

    elif event_type == 'customer.subscription.updated':
        subscription_obj = event['data']['object']
        customer_id = getattr(subscription_obj, 'customer', None)
        sub = _subscription_for_customer(customer_id)
        if sub:
            sub.cancel_at_period_end = bool(getattr(subscription_obj, 'cancel_at_period_end', False))
            sub.save(update_fields=['cancel_at_period_end'])
        else:
            logger.error(f"Webhook de Stripe: no se encontro suscripcion para customer {customer_id} en evento {event['id']}")

    elif event_type == 'customer.subscription.deleted':
        subscription_obj = event['data']['object']
        customer_id = getattr(subscription_obj, 'customer', None)
        sub = _subscription_for_customer(customer_id)
        if sub:
            sub.status = 'canceled'
            sub.cancel_at_period_end = False
            sub.save(update_fields=['status', 'cancel_at_period_end'])
        else:
            logger.error(f"Webhook de Stripe: no se encontro suscripcion para customer {customer_id} en evento {event['id']}")

    return HttpResponse(status=200)
```

- [ ] **Step 4: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py -v`
Expected: PASS (8 tests: 4 preexistentes actualizados + 3 nuevos, más el de firma
inválida que sigue igual).

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/stripe_views.py core/brand_dna/tests/test_stripe_views.py
GIT_EDITOR=true git commit -m "feat(subscriptions): captura customer_id/subscription_id y sincroniza cancelacion via webhook"
```

---

### Task 3: Webhook — cobro fallido/exitoso + correo de aviso

**Files:**
- Modify: `core/brand_dna/stripe_views.py`
- Modify: `core/brand_dna/tests/test_stripe_views.py`
- Modify: `core/content_pipeline/email_sender.py`
- Create: `core/content_pipeline/templates/content_pipeline/email_payment_failed.html`
- Modify: `core/content_pipeline/tests/test_email_sender.py`

**Interfaces:**
- Consumes: `_subscription_for_customer` (Task 2).
- Produces: `EmailSender.send_payment_failed(self, job: AnalysisJob, brand_dna: BrandDNA) -> None`.

- [ ] **Step 1: Escribir el test de email que falla**

Agregar al final de `core/content_pipeline/tests/test_email_sender.py`:

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_payment_failed_email_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    from core.tenant_management.models import TenantModel, Subscription, Plan
    job, dna, calendar, posts = full_setup
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username=job.email, email=job.email, password='pass1234')
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=plan, status='past_due', stripe_customer_id='cus_test1')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job.user = user
    job.save(update_fields=['user'])

    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_payment_failed(job=job, brand_dna=dna)

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    html = call_kwargs[1]['html_message']
    assert 'https://cosmic.anuarbarrera.dev' in html
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py::test_send_payment_failed_email_calls_django_send -v`
Expected: FAIL — `AttributeError: 'EmailSender' object has no attribute 'send_payment_failed'`.

- [ ] **Step 3: Crear el template**

Crear `core/content_pipeline/templates/content_pipeline/email_payment_failed.html`:

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>No pudimos cobrar tu suscripción — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 580px; margin: 0 auto; padding: 24px 20px; color: #333;">

  <p style="margin: 0 0 16px; font-size: 0.72rem; color: #999; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">📬 Notificación automática</p>

  <p style="font-size: 1.1rem; margin-bottom: 8px;">Hola,</p>
  <p>No pudimos cobrar el pago de tu suscripción de <strong>{{ brand_dna.business_name }}</strong>.
  Puede ser una tarjeta vencida, fondos insuficientes o un rechazo temporal del banco.</p>

  <p>Vamos a reintentar el cobro automáticamente en los próximos días. Si quieres
  actualizar tu método de pago desde ahora, entra a tu panel:</p>

  <div style="text-align: center; margin: 28px 0;">
    <a href="{{ dashboard_url }}"
       style="display: inline-block; padding: 14px 32px; background: #e94560; color: #fff;
              border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 1rem;">
      Ir a mi panel →
    </a>
  </div>

  <p style="color: #555;">Ahí encontrarás el botón "Administrar mi suscripción" para
  actualizar tu tarjeta. Tu contenido sigue disponible mientras reintentamos el cobro.</p>

  <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">
  <p style="font-size: 11px; color: #bbb; margin: 0;">Agente Cosmic — Powered by Google Cloud</p>

</body>
</html>
```

- [ ] **Step 4: Implementar el método**

En `core/content_pipeline/email_sender.py`, agregar al final de la clase `EmailSender`:

```python

    def send_payment_failed(self, job: AnalysisJob, brand_dna: BrandDNA) -> None:
        dashboard_url = settings.COSMIC_BASE_URL + reverse('dashboard')
        html = render_to_string('content_pipeline/email_payment_failed.html', {
            'brand_dna': brand_dna,
            'dashboard_url': dashboard_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'⚠️ No pudimos cobrar tu suscripción — {name}' if name else '⚠️ No pudimos cobrar tu suscripción'
        plain = (
            f'No pudimos cobrar tu suscripción de {name}. Actualiza tu método de pago: {dashboard_url}'
        ) if name else f'No pudimos cobrar tu suscripción. Actualiza tu método de pago: {dashboard_url}'
        send_mail(
            subject,
            plain,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email],
            html_message=html,
            fail_silently=False,
        )
        EMAILS_SENT.labels(type='payment_failed').inc()
        logger.info(f"Email de cobro fallido enviado a {job.email} para job {job.id}")
```

- [ ] **Step 5: Correr el test de email para confirmar que pasa**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py -v`
Expected: PASS (7 tests: 6 preexistentes + el nuevo).

- [ ] **Step 6: Escribir los tests del webhook que fallan**

Agregar al final de `core/brand_dna/tests/test_stripe_views.py`:

```python
def _fake_invoice_event(event_id, event_type, customer_id):
    return {
        'id': event_id,
        'type': event_type,
        'data': {'object': SimpleNamespace(customer=customer_id)},
    }


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_payment_failed_marks_past_due_and_sends_email(tenant_with_subscription):
    from core.brand_dna.models import AnalysisJob, BrandDNA
    from django.contrib.auth import get_user_model
    tenant_with_subscription.subscription.stripe_customer_id = 'cus_test1'
    tenant_with_subscription.subscription.status = 'active'
    tenant_with_subscription.subscription.save(update_fields=['stripe_customer_id', 'status'])
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username='t@t.com', email='t@t.com', password='pass1234')
    user.tenant = tenant_with_subscription
    user.save(update_fields=['tenant'])
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com', user=user,
        status=AnalysisJob.STATUS_DONE, generation_mode=AnalysisJob.MODE_FULL,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_invoice_event('evt_7', 'invoice.payment_failed', 'cus_test1')), \
         patch('core.brand_dna.stripe_views.EmailSender') as MockEmail:
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'past_due'
    MockEmail.return_value.send_payment_failed.assert_called_once()


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_payment_succeeded_restores_active(tenant_with_subscription):
    tenant_with_subscription.subscription.stripe_customer_id = 'cus_test1'
    tenant_with_subscription.subscription.status = 'past_due'
    tenant_with_subscription.subscription.save(update_fields=['stripe_customer_id', 'status'])
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_invoice_event('evt_8', 'invoice.payment_succeeded', 'cus_test1')):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'active'
```

- [ ] **Step 7: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py -v`
Expected: FAIL — `invoice.payment_failed`/`invoice.payment_succeeded` no se manejan
todavía (los 2 tests nuevos fallan, el resto sigue en verde).

- [ ] **Step 8: Implementar los handlers en el webhook**

En `core/brand_dna/stripe_views.py`, agregar el import al inicio del archivo (después de
`from core.tenant_management.models import Subscription`):

```python
from core.brand_dna.models import AnalysisJob
from core.content_pipeline.email_sender import EmailSender
```

Agregar la función helper después de `_subscription_for_customer`:

```python


def _job_for_tenant(tenant):
    return AnalysisJob.objects.filter(
        user__tenant=tenant, generation_mode=AnalysisJob.MODE_FULL,
    ).order_by('-created_at').first()
```

Agregar estos 2 bloques `elif` al final de la cadena existente en `stripe_webhook_view`,
justo antes del `return HttpResponse(status=200)`:

```python

    elif event_type == 'invoice.payment_failed':
        invoice = event['data']['object']
        customer_id = getattr(invoice, 'customer', None)
        sub = _subscription_for_customer(customer_id)
        if sub:
            sub.status = 'past_due'
            sub.save(update_fields=['status'])
            job = _job_for_tenant(sub.tenant)
            if job and hasattr(job, 'brand_dna'):
                try:
                    EmailSender().send_payment_failed(job=job, brand_dna=job.brand_dna)
                except Exception as email_err:
                    logger.error(f"Email de cobro fallido fallo para tenant {sub.tenant_id} (no fatal): {email_err}")
        else:
            logger.error(f"Webhook de Stripe: no se encontro suscripcion para customer {customer_id} en evento {event['id']}")

    elif event_type == 'invoice.payment_succeeded':
        invoice = event['data']['object']
        customer_id = getattr(invoice, 'customer', None)
        sub = _subscription_for_customer(customer_id)
        if sub:
            sub.status = 'active'
            sub.save(update_fields=['status'])
        else:
            logger.error(f"Webhook de Stripe: no se encontro suscripcion para customer {customer_id} en evento {event['id']}")
```

- [ ] **Step 9: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py -v`
Expected: PASS (10 tests).

- [ ] **Step 10: Commit**

```bash
git add core/brand_dna/stripe_views.py core/brand_dna/tests/test_stripe_views.py core/content_pipeline/email_sender.py core/content_pipeline/templates/content_pipeline/email_payment_failed.html core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "feat(subscriptions): correo de cobro fallido + sincronizacion invoice.payment_failed/succeeded"
```

---

### Task 4: Gate — bloquear también `status='canceled'`

**Files:**
- Modify: `core/brand_dna/views.py`
- Modify: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: `Subscription.status` (Task 1, ya existente).

- [ ] **Step 1: Escribir el test que falla**

Agregar en `core/brand_dna/tests/test_views.py`, después de
`test_calendar_feedback_api_yes_blocked_when_trial_expired` (buscar esa función y agregar
inmediatamente después):

```python
@override_settings(STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_calendar_feedback_api_yes_blocked_when_canceled(client, user, job_with_calendar):
    user.tenant.subscription.status = 'canceled'
    user.tenant.subscription.save(update_fields=['status'])
    client.force_login(user)
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '5',
            'continue_decision': 'yes',
        })
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'payment_required'
    mock_rq.enqueue.assert_not_called()


def test_calendar_feedback_api_yes_allowed_when_past_due(client, user, job_with_calendar):
    user.tenant.subscription.status = 'past_due'
    user.tenant.subscription.save(update_fields=['status'])
    client.force_login(user)
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '5',
            'continue_decision': 'yes',
        })
    assert response.status_code == 200
    data = response.json()
    assert data['continue_decision'] == 'yes'
    mock_rq.enqueue.assert_called_once()
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py::test_calendar_feedback_api_yes_blocked_when_canceled -v`
Expected: FAIL — hoy `status='canceled'` no bloquea, así que `mock_rq.enqueue` sí se
llama y la respuesta no trae `payment_required`.

- [ ] **Step 3: Implementar**

En `core/brand_dna/views.py`, dentro de `calendar_feedback_api`, cambiar la línea:

```python
        if subscription and subscription.status == 'trial_expired':
```

por:

```python
        if subscription and subscription.status in ('trial_expired', 'canceled'):
```

- [ ] **Step 4: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -v`
Expected: PASS — todos, incluyendo los 2 nuevos, sin regresión en
`test_calendar_feedback_api_yes_allowed_when_trialing`/`..._yes_triggers_generate_next_week`
(siguen en `'active'`/`'trialing'`, sin cambio de comportamiento).

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(subscriptions): el gate tambien bloquea suscripciones canceladas"
```

---

### Task 5: Endpoint de Customer Portal + botón en el dashboard

**Files:**
- Modify: `core/brand_dna/stripe_views.py`
- Modify: `core/brand_dna/urls.py`
- Modify: `core/brand_dna/templates/brand_dna/dashboard.html`
- Modify: `core/brand_dna/tests/test_stripe_views.py`

**Interfaces:**
- Consumes: `Subscription.stripe_customer_id` (Task 1/2).
- Produces: endpoint `POST /dashboard/suscripcion/` (`name='manage_subscription'`).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/brand_dna/tests/test_stripe_views.py`:

```python
@pytest.fixture
def user_with_customer_id(django_user_model, tenant_with_subscription):
    tenant_with_subscription.subscription.stripe_customer_id = 'cus_test1'
    tenant_with_subscription.subscription.save(update_fields=['stripe_customer_id'])
    user = django_user_model.objects.create_user(
        username='portal@test.com', email='portal@test.com', password='pass1234'
    )
    user.tenant = tenant_with_subscription
    user.save(update_fields=['tenant'])
    return user


def test_manage_subscription_redirects_to_portal_session(user_with_customer_id):
    c = Client()
    c.force_login(user_with_customer_id)
    fake_session = SimpleNamespace(url='https://billing.stripe.com/p/session/test_abc')
    with patch('core.brand_dna.stripe_views.stripe.billing_portal.Session.create',
               return_value=fake_session) as mock_create:
        response = c.post('/dashboard/suscripcion/')
    assert response.status_code == 302
    assert response.url == 'https://billing.stripe.com/p/session/test_abc'
    mock_create.assert_called_once()
    assert mock_create.call_args[1]['customer'] == 'cus_test1'


def test_manage_subscription_without_customer_id_redirects_to_dashboard(django_user_model, tenant_with_subscription):
    user = django_user_model.objects.create_user(
        username='noportal@test.com', email='noportal@test.com', password='pass1234'
    )
    user.tenant = tenant_with_subscription
    user.save(update_fields=['tenant'])
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.stripe_views.stripe.billing_portal.Session.create') as mock_create:
        response = c.post('/dashboard/suscripcion/')
    assert response.status_code == 302
    assert response.url == '/dashboard/'
    mock_create.assert_not_called()


def test_manage_subscription_requires_login():
    c = Client()
    response = c.post('/dashboard/suscripcion/')
    assert response.status_code == 302
    assert '/auth/login/' in response.url
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py::test_manage_subscription_redirects_to_portal_session -v`
Expected: FAIL — `django.urls.exceptions.NoReverseMatch` o 404 (la ruta no existe
todavía).

- [ ] **Step 3: Implementar la vista**

En `core/brand_dna/stripe_views.py`, agregar los imports al inicio del archivo (junto a
los ya existentes):

```python
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse
```

Agregar la función al final del archivo:

```python


@login_required
@require_POST
def manage_subscription_view(request):
    subscription = getattr(getattr(request.user, 'tenant', None), 'subscription', None)
    if not subscription or not subscription.stripe_customer_id:
        return redirect('dashboard')
    portal_session = stripe.billing_portal.Session.create(
        customer=subscription.stripe_customer_id,
        return_url=settings.COSMIC_BASE_URL + reverse('dashboard'),
    )
    return redirect(portal_session.url)
```

- [ ] **Step 4: Registrar la URL**

En `core/brand_dna/urls.py`, agregar esta línea al final de `urlpatterns` (después de
`path('stripe/webhook/', ...)`):

```python
    path('dashboard/suscripcion/', stripe_views.manage_subscription_view, name='manage_subscription'),
```

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py -v`
Expected: PASS (13 tests).

- [ ] **Step 6: Agregar el botón al dashboard**

En `core/brand_dna/templates/brand_dna/dashboard.html`, insertar este bloque
inmediatamente antes de la línea que empieza con
`<details style="max-width:700px;margin:48px auto 32px;border:1px solid #c0392b...`
(la sección "Zona de peligro"):

```html
  {% if user.tenant.subscription.stripe_customer_id %}
  <div style="max-width:700px;margin:32px auto;text-align:center;">
    <form method="POST" action="{% url 'manage_subscription' %}">
      {% csrf_token %}
      <button type="submit" style="background:#4a9eff;color:#fff;border:none;padding:12px 28px;border-radius:8px;cursor:pointer;font-weight:600;">
        Administrar mi suscripción
      </button>
    </form>
  </div>
  {% endif %}
```

- [ ] **Step 7: Test del botón en el dashboard**

Agregar en `core/brand_dna/tests/test_views.py`:

```python
def test_dashboard_shows_manage_subscription_button_with_customer_id(client, user):
    user.tenant.subscription.stripe_customer_id = 'cus_test1'
    user.tenant.subscription.save(update_fields=['stripe_customer_id'])
    client.force_login(user)
    response = client.get('/dashboard/')
    assert b'Administrar mi suscripci\xc3\xb3n' in response.content


def test_dashboard_hides_manage_subscription_button_without_customer_id(client, user):
    client.force_login(user)
    response = client.get('/dashboard/')
    assert b'Administrar mi suscripci\xc3\xb3n' not in response.content
```

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -v`
Expected: PASS — todos, incluyendo los 2 nuevos.

- [ ] **Step 8: Commit**

```bash
git add core/brand_dna/stripe_views.py core/brand_dna/urls.py core/brand_dna/templates/brand_dna/dashboard.html core/brand_dna/tests/test_stripe_views.py core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(subscriptions): endpoint de Customer Portal + boton en el dashboard"
```

---

### Task 6: Eliminar cuenta cancela de verdad en Stripe

**Files:**
- Modify: `core/brand_dna/auth_views.py`
- Modify: `core/brand_dna/tests/test_account_deactivation.py`

**Interfaces:**
- Consumes: `Subscription.stripe_subscription_id` (Task 1/2).

- [ ] **Step 1: Escribir el test que falla**

Agregar en `core/brand_dna/tests/test_account_deactivation.py`, después de
`test_deactivate_account_sets_inactive`:

```python
def test_deactivate_account_cancels_real_stripe_subscription(user_with_tenant):
    from unittest.mock import patch
    user_with_tenant.tenant.subscription.stripe_subscription_id = 'sub_test1'
    user_with_tenant.tenant.subscription.status = 'active'
    user_with_tenant.tenant.subscription.save(update_fields=['stripe_subscription_id', 'status'])
    c = Client()
    c.force_login(user_with_tenant)
    with patch('stripe.Subscription.modify') as mock_modify:
        response = c.post('/dashboard/delete-account/', {'confirmation': 'ELIMINAR'})
    assert response.status_code == 302
    mock_modify.assert_called_once_with('sub_test1', cancel_at_period_end=True)

    user_with_tenant.refresh_from_db()
    assert user_with_tenant.tenant.subscription.status == 'canceled'


def test_deactivate_account_without_stripe_subscription_does_not_call_stripe(user_with_tenant):
    from unittest.mock import patch
    c = Client()
    c.force_login(user_with_tenant)
    with patch('stripe.Subscription.modify') as mock_modify:
        response = c.post('/dashboard/delete-account/', {'confirmation': 'ELIMINAR'})
    assert response.status_code == 302
    mock_modify.assert_not_called()


def test_deactivate_account_survives_stripe_api_error(user_with_tenant):
    from unittest.mock import patch
    user_with_tenant.tenant.subscription.stripe_subscription_id = 'sub_test1'
    user_with_tenant.tenant.subscription.status = 'active'
    user_with_tenant.tenant.subscription.save(update_fields=['stripe_subscription_id', 'status'])
    c = Client()
    c.force_login(user_with_tenant)
    with patch('stripe.Subscription.modify', side_effect=Exception('Stripe down')):
        response = c.post('/dashboard/delete-account/', {'confirmation': 'ELIMINAR'})
    assert response.status_code == 302
    assert '/auth/login/' in response.url

    user_with_tenant.refresh_from_db()
    assert user_with_tenant.is_active is False
    assert user_with_tenant.tenant.subscription.status == 'canceled'
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_account_deactivation.py::test_deactivate_account_cancels_real_stripe_subscription -v`
Expected: FAIL — `mock_modify.assert_called_once_with(...)` falla porque hoy nunca se
llama a `stripe.Subscription.modify`.

- [ ] **Step 3: Implementar**

En `core/brand_dna/auth_views.py`, reemplazar el bloque (líneas 570-578):

```python
    if user.tenant:
        user.tenant.status = 'deactivated'
        user.tenant.save(update_fields=['status'])
        try:
            sub = user.tenant.subscription
            sub.status = 'canceled'
            sub.save(update_fields=['status'])
        except Exception:
            pass
```

por:

```python
    if user.tenant:
        user.tenant.status = 'deactivated'
        user.tenant.save(update_fields=['status'])
        try:
            sub = user.tenant.subscription
            if sub.stripe_subscription_id and sub.status not in ('canceled', 'trial_expired'):
                import stripe
                try:
                    stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=True)
                except Exception:
                    pass
            sub.status = 'canceled'
            sub.save(update_fields=['status'])
        except Exception:
            pass
```

- [ ] **Step 4: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_account_deactivation.py -v`
Expected: PASS — todos (7 preexistentes + 3 nuevos).

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/auth_views.py core/brand_dna/tests/test_account_deactivation.py
GIT_EDITOR=true git commit -m "fix(subscriptions): eliminar cuenta cancela la suscripcion real en Stripe"
```

---

### Task 7: `Subscription` en el Django Admin (solo lectura)

**Files:**
- Modify: `core/tenant_management/admin.py`
- Test: `core/tenant_management/tests/test_admin_access.py`

**Interfaces:**
- Consumes: `Subscription` (Task 1).

- [ ] **Step 1: Escribir el test que falla**

`core/tenant_management/tests/test_admin_access.py` ya existe con este contenido
exacto (no reescribir todo el archivo, solo agregar el método nuevo):

```python
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client

User = get_user_model()


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def groups(db):
    Group.objects.get_or_create(name='admin')
    Group.objects.get_or_create(name='tester')
    Group.objects.get_or_create(name='user')


@pytest.mark.django_db
class TestAdminAccess:
    def test_non_staff_gets_404(self, client, groups):
        ...
    def test_staff_can_reach_admin_login(self, client, groups):
        ...
    def test_anonymous_gets_redirect_to_login(self, client):
        ...
```

Agregar este método dentro de la clase `TestAdminAccess` (mismo nivel de indentación
que los 3 métodos existentes):

```python
    def test_subscription_registered_read_only_in_admin(self, client, groups):
        from core.tenant_management.models import TenantModel, Subscription, Plan
        staff = User.objects.create_user(
            email='staff@test.com', password='TestPass123!x', username='staff@test.com',
            is_staff=True,
        )
        client.force_login(staff)

        plan = Plan.objects.create(name='Plan Admin Test')
        tenant = TenantModel.objects.create(name='Tenant Admin Test', status='active')
        sub = Subscription.objects.create(tenant=tenant, plan=plan, stripe_customer_id='cus_admin_test')

        response = client.get('/admin/tenant_management/subscription/')
        assert response.status_code == 200
        assert b'cus_admin_test' in response.content

        add_response = client.get('/admin/tenant_management/subscription/add/')
        assert add_response.status_code == 403

        change_response = client.get(f'/admin/tenant_management/subscription/{sub.id}/change/')
        assert change_response.status_code == 200
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/tenant_management/tests/test_admin_access.py::test_subscription_registered_read_only_in_admin -v`
Expected: FAIL — 404 en `/admin/tenant_management/subscription/` (no está registrado).

- [ ] **Step 3: Implementar**

En `core/tenant_management/admin.py`, cambiar el import (línea 13-15):

```python
from core.tenant_management.models import (
    InvitationCode, Plan, SecurityEvent, User,
)
```

por:

```python
from core.tenant_management.models import (
    InvitationCode, Plan, SecurityEvent, Subscription, User,
)
```

Agregar la clase `SubscriptionAdmin` después de `WeeklyFeedbackAdmin` (antes de
`class TOTPDeviceAdmin`):

```python
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'plan', 'status', 'trial_ends_at', 'cancel_at_period_end', 'stripe_customer_id')
    list_filter = ('status', 'plan')
    search_fields = ('tenant__name', 'stripe_customer_id', 'stripe_subscription_id')
    readonly_fields = (
        'id', 'tenant', 'plan', 'start_date', 'end_date', 'status',
        'trial_ends_at', 'cancel_at_period_end', 'stripe_customer_id', 'stripe_subscription_id',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
```

Agregar la línea de registro después de `cosmic_admin.register(WeeklyFeedback, WeeklyFeedbackAdmin)`:

```python
cosmic_admin.register(Subscription, SubscriptionAdmin)
```

- [ ] **Step 4: Correr el test para confirmar que pasa**

Run: `docker compose exec backend pytest core/tenant_management/tests/test_admin_access.py -v`
Expected: PASS — todos, incluyendo el nuevo.

- [ ] **Step 5: Commit**

```bash
git add core/tenant_management/admin.py core/tenant_management/tests/test_admin_access.py
GIT_EDITOR=true git commit -m "feat(subscriptions): registra Subscription en el admin de solo lectura"
```

---

### Verificación final

- [ ] Correr la suite completa antes de dar por terminado el plan:

Run: `docker compose exec backend pytest core/ -v`
Expected: 0 failures.

- [ ] **Verificación en vivo** (no solo tests automatizados): usando la suscripción de
prueba real que quedó activa en modo test (Payment Link
`https://buy.stripe.com/test_eVqcN65vG95T73j9lJaEE00`, cobrando $10 MXN/mes) — recrear
un tenant/usuario local ligado a ese `stripe_customer_id`/`stripe_subscription_id` real
(los mismos capturados en la prueba anterior: `cus_UwUiAYneD6v2nR` /
`sub_1TwbiwJnfVZIRnFulMJAEVxM`, confirmar que siguen siendo los correctos consultando el
Dashboard de Stripe en modo test antes de usarlos), generar una sesión de Customer
Portal real desde la app, cancelar ahí, y confirmar que `stripe listen` reenvía
`customer.subscription.updated` (con `cancel_at_period_end=true`) y finalmente
`customer.subscription.deleted` una vez que el periodo termine (o forzar la cancelación
inmediata solo para esta prueba, fuera del flujo normal, si Stripe lo permite desde el
propio portal de prueba) — confirmar que `Subscription.status` en la base de datos local
refleja cada paso correctamente.

- [ ] Nota para Anuar, fuera del alcance de este plan: activar el Customer Portal en el
Dashboard de Stripe (Settings → Billing → Customer portal) antes de que el botón
funcione en producción, y agregar los 4 eventos nuevos (`customer.subscription.updated`,
`customer.subscription.deleted`, `invoice.payment_failed`, `invoice.payment_succeeded`)
al webhook endpoint configurado en modo live (hoy el webhook de prueba local vía `stripe
listen` reenvía todos los eventos por defecto, pero un endpoint configurado a mano en el
Dashboard de Stripe solo reenvía los eventos que se seleccionen explícitamente ahí).
