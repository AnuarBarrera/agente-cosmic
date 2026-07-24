# Suscripciones con Stripe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conectar el mecanismo ya existente de "generar la siguiente semana" (banner de
feedback en `calendar_review.html` → `calendar_feedback_api` → `generate_next_week`) con
Stripe, para que solo usuarios con la semana gratis vigente o ya pagada puedan seguir
generando contenido.

**Architecture:** Un campo nuevo (`Subscription.trial_ends_at`) arranca el trial de 7 días
cuando se genera el primer calendario completo. Un webhook de Stripe activa la suscripción
al confirmar el pago. Un gate dentro de `calendar_feedback_api` (no un job aparte) decide si
`generate_next_week` se ejecuta. Un job diario/management command notifica y degrada el
estado de los trials vencidos sin pago.

**Tech Stack:** Django 5.2, django-rq, pytest-django, librería oficial `stripe` (nueva
dependencia).

## Global Constraints

- Spec de referencia: `docs/superpowers/specs/2026-07-24-stripe-subscriptions-design.md` —
  ante cualquier ambigüedad no cubierta aquí, esa spec es la autoridad.
- El trial arranca **solo** en `content_generation_task` (modo `AnalysisJob.MODE_FULL`).
  `generate_sample_task` (modo muestra) nunca toca `Subscription`.
- Duración del trial: exactamente 7 días desde que se crea el `ContentCalendar`.
- Valores de `Subscription.status` usados en este trabajo: `'trialing'`, `'trial_expired'`,
  además de los ya existentes `'active'`/`'canceled'`/`'past_due'`. No se agregan choices
  formales al campo (sigue siendo `CharField` libre, mismo patrón que hoy).
- Regla de acceso (fija, no cambiar en ninguna tarea): `calendar_feedback_api` permite
  `generate_next_week` cuando `subscription.status in ('active', 'trialing')`; lo bloquea
  únicamente cuando `subscription.status == 'trial_expired'`.
- Contrato JSON del bloqueo de pago: `{'status': 'payment_required', 'payment_url': <str>}`
  — sin la clave `continue_decision` (para diferenciarlo del contrato `{'status': 'ok',
  'continue_decision': ...}` que ya existe).
- Construcción del link de pago, **idéntica en los dos lugares que la usan** (gate y correo):
  `f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={tenant_id}"`.
- Todo el código nuevo va en `core.brand_dna` y `core.content_pipeline` (las apps reales y
  enrutadas). No tocar `core.tenant_management.interfaces` (DRF, confirmado no enrutado) —
  el modelo `Subscription` sí vive en `core.tenant_management.models` y se importa desde ahí,
  pero no se toca esa carpeta `interfaces/`.
- Nuevas variables de entorno, mismo patrón `get_env(key, default=None, cast=str)` de
  `saas_chatbot/settings.py:46`: `STRIPE_WEBHOOK_SECRET` (default `''`),
  `STRIPE_PAYMENT_LINK_URL` (default `''`).
- Sin tabla de deduplicación de eventos de webhook, sin guard de staleness adicional en el
  job diario más allá de la idempotencia natural del cambio de estado — riesgos aceptados
  explícitos en la spec, no construir infraestructura para ellos.
- Fuera de alcance (no crear tareas para esto): facturación recurrente de Stripe en sí,
  cancelación/downgrade de suscripción, Checkout Session dinámica vía API, los otros 2
  disparadores de reactivación de `ultimosCambios.md` (calendario sin descargar, registro
  sin análisis).

---

### Task 1: Campo `trial_ends_at`, settings de Stripe y dependencia

**Files:**
- Modify: `core/tenant_management/models.py:67-81` (clase `Subscription`)
- Create: `core/tenant_management/migrations/0021_subscription_trial_ends_at.py`
- Modify: `saas_chatbot/settings.py` (después de la línea 164, bloque Mailgun)
- Modify: `requirements.txt` (agregar dependencia)
- Test: `core/tenant_management/tests/test_models.py` (archivo nuevo)

**Interfaces:**
- Produces: `Subscription.trial_ends_at` (`DateTimeField`, `null=True, blank=True`) — usado
  por las Tasks 2 y 4. `settings.STRIPE_WEBHOOK_SECRET` y `settings.STRIPE_PAYMENT_LINK_URL`
  — usados por las Tasks 3 y 5.

- [ ] **Step 1: Escribir el test que falla**

Crear `core/tenant_management/tests/test_models.py`:

```python
import pytest
from django.utils import timezone
from core.tenant_management.models import TenantModel, Plan, Subscription

pytestmark = pytest.mark.django_db


def test_subscription_trial_ends_at_defaults_to_none():
    plan = Plan.objects.create(name='Plan Test Trial')
    tenant = TenantModel.objects.create(name='Tenant Test', status='active')
    sub = Subscription.objects.create(tenant=tenant, plan=plan)
    assert sub.trial_ends_at is None


def test_subscription_trial_ends_at_accepts_datetime():
    plan = Plan.objects.create(name='Plan Test Trial 2')
    tenant = TenantModel.objects.create(name='Tenant Test 2', status='active')
    ends_at = timezone.now() + timezone.timedelta(days=7)
    sub = Subscription.objects.create(
        tenant=tenant, plan=plan, status='trialing', trial_ends_at=ends_at,
    )
    sub.refresh_from_db()
    assert sub.status == 'trialing'
    assert sub.trial_ends_at == ends_at
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/tenant_management/tests/test_models.py -v`
Expected: FAIL — `TypeError: Subscription() got unexpected keyword arguments: 'trial_ends_at'`
(el campo todavía no existe).

- [ ] **Step 3: Agregar el campo al modelo**

En `core/tenant_management/models.py`, reemplazar la clase `Subscription` completa
(líneas 67-81):

```python
class Subscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(TenantModel, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    start_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=50, default='active')  # e.g., active, trialing, trial_expired, canceled, past_due
    trial_ends_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.tenant.name} - {self.plan.name}"

    class Meta:
        db_table = 'subscriptions'
        verbose_name = 'Subscription'
        verbose_name_plural = 'Subscriptions'
```

- [ ] **Step 4: Generar la migración**

Run: `docker compose exec backend python manage.py makemigrations tenant_management`
Expected: crea `core/tenant_management/migrations/0021_subscription_trial_ends_at.py`.
Verificar que el contenido generado coincide con esto (si el nombre automático difiere,
renombrar el archivo a `0021_subscription_trial_ends_at.py`):

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_management', '0020_plan_allows_sample_generation'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='trial_ends_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
```

- [ ] **Step 5: Aplicar la migración y correr el test**

Run: `docker compose exec backend python manage.py migrate tenant_management`
Run: `docker compose exec backend pytest core/tenant_management/tests/test_models.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Agregar settings de Stripe**

En `saas_chatbot/settings.py`, justo después de la línea 164
(`DEFAULT_TENANT_NOTIFICATION_EMAIL = get_env(...)`, fin del bloque Mailgun), agregar:

```python
# Stripe Settings
STRIPE_WEBHOOK_SECRET = get_env('STRIPE_WEBHOOK_SECRET', default='')
STRIPE_PAYMENT_LINK_URL = get_env('STRIPE_PAYMENT_LINK_URL', default='')
```

- [ ] **Step 7: Agregar la dependencia**

En `requirements.txt`, agregar como última línea del archivo:

```
stripe>=10.0.0
```

Run: `docker compose exec backend pip install stripe>=10.0.0`
Expected: instala sin error. (La imagen de docker se reconstruirá con la dependencia en el
próximo build; para esta sesión de desarrollo basta con instalarla en el contenedor activo.)

- [ ] **Step 8: Commit**

```bash
git add core/tenant_management/models.py core/tenant_management/migrations/0021_subscription_trial_ends_at.py core/tenant_management/tests/test_models.py saas_chatbot/settings.py requirements.txt
GIT_EDITOR=true git commit -m "feat(subscriptions): agrega trial_ends_at y config de Stripe"
```

---

### Task 2: `content_generation_task` arranca el trial

**Files:**
- Modify: `core/content_pipeline/tasks.py:1-19` (imports), `:44-119` (`content_generation_task`)
- Test: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: `Subscription.trial_ends_at`, `Subscription.status` (Task 1).
- Produces: nada nuevo para otras tareas — este comportamiento es terminal (solo lo lee
  el gate de la Task 6 y el job de la Task 4, ambos consumen `Subscription.status`/
  `trial_ends_at` directo del modelo, no de esta función).

- [ ] **Step 1: Escribir el test que falla**

En `core/content_pipeline/tests/test_tasks.py`, agregar el import de `timedelta` ya existe
(línea 5). Agregar esta fixture justo después de `job_with_dna` (después de la línea 28):

```python
@pytest.fixture
def job_with_dna_and_tenant(job_with_dna):
    from django.contrib.auth import get_user_model
    from core.tenant_management.models import TenantModel, Subscription, Plan
    UserModel = get_user_model()
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    user = UserModel.objects.create_user(
        username='trial@test.com', email='trial@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=plan)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job_with_dna.user = user
    job_with_dna.save(update_fields=['user'])
    return job_with_dna
```

Agregar estos 2 tests después de `test_content_generation_creates_calendar` (después de la
línea 52):

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_content_generation_starts_trial_for_tenant(job_with_dna_and_tenant):
    from core.tenant_management.models import Subscription
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna_and_tenant.id))

    sub = Subscription.objects.get(tenant=job_with_dna_and_tenant.user.tenant)
    assert sub.status == 'trialing'
    assert sub.trial_ends_at is not None
    assert sub.trial_ends_at > timezone.now() + timedelta(days=6)
    assert sub.trial_ends_at < timezone.now() + timedelta(days=8)


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_content_generation_without_user_does_not_crash(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    job_with_dna.refresh_from_db()
    assert job_with_dna.status == AnalysisJob.STATUS_DONE
```

Nota: `job_with_dna` (sin tenant) es la fixture ya existente que usan
`test_content_generation_creates_calendar` y varios tests más — esos tests **no** deben
romperse por este cambio; `test_content_generation_without_user_does_not_crash` es la
prueba explícita de esa garantía.

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py::test_content_generation_starts_trial_for_tenant -v`
Expected: FAIL — `Subscription.DoesNotExist` o `sub.status == 'active'` (el código todavía
no toca `Subscription`).

- [ ] **Step 3: Implementar**

En `core/content_pipeline/tasks.py`, agregar el import después de la línea 9
(`from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback`):

```python
from core.tenant_management.models import Subscription
```

Modificar `content_generation_task` — reemplazar las líneas 56-57:

```python
        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        CALENDARS_CREATED.inc()
```

por:

```python
        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        CALENDARS_CREATED.inc()
        if job.user and job.user.tenant:
            Subscription.objects.filter(tenant=job.user.tenant).update(
                status='trialing',
                trial_ends_at=timezone.now() + timedelta(days=7),
            )
```

- [ ] **Step 4: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: PASS — todos los tests del archivo (los preexistentes de `content_generation_task`
y `generate_sample_task` siguen en verde, más los 2 nuevos).

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
GIT_EDITOR=true git commit -m "feat(subscriptions): content_generation_task arranca el trial de 7 dias"
```

---

### Task 3: Correo "tu semana gratis terminó"

**Files:**
- Modify: `core/content_pipeline/email_sender.py`
- Create: `core/content_pipeline/templates/content_pipeline/email_trial_expired.html`
- Test: `core/content_pipeline/tests/test_email_sender.py`

**Interfaces:**
- Consumes: `settings.STRIPE_PAYMENT_LINK_URL` (Task 1).
- Produces: `EmailSender.send_trial_expired(self, job: AnalysisJob, brand_dna: BrandDNA) -> None`
  — usado por la Task 4.

- [ ] **Step 1: Escribir el test que falla**

En `core/content_pipeline/tests/test_email_sender.py`, agregar al final del archivo:

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_send_trial_expired_email_calls_django_send(full_setup):
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
    Subscription.objects.create(tenant=tenant, plan=plan, status='trial_expired')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job.user = user
    job.save(update_fields=['user'])

    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_trial_expired(job=job, brand_dna=dna)

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    html = call_kwargs[1]['html_message']
    assert f'https://buy.stripe.com/test123?client_reference_id={tenant.id}' in html
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py::test_send_trial_expired_email_calls_django_send -v`
Expected: FAIL — `AttributeError: 'EmailSender' object has no attribute 'send_trial_expired'`.

- [ ] **Step 3: Crear el template**

Crear `core/content_pipeline/templates/content_pipeline/email_trial_expired.html`:

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Tu semana gratis terminó — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 580px; margin: 0 auto; padding: 24px 20px; color: #333;">

  <p style="margin: 0 0 16px; font-size: 0.72rem; color: #999; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">📬 Notificación automática</p>

  <p style="font-size: 1.1rem; margin-bottom: 8px;">Hola,</p>
  <p>Tu semana gratis de contenido para <strong>{{ brand_dna.business_name }}</strong> ya terminó.</p>

  <p>Para seguir recibiendo contenido nuevo cada semana, activa tu suscripción:</p>

  <div style="text-align: center; margin: 28px 0;">
    <a href="{{ payment_url }}"
       style="display: inline-block; padding: 14px 32px; background: #e94560; color: #fff;
              border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 1rem;">
      Activar mi suscripción →
    </a>
  </div>

  <p style="color: #555;">Tu contenido ya generado sigue disponible sin restricción — esto
  solo afecta la generación de tu próxima semana.</p>

  <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">
  <p style="font-size: 11px; color: #bbb; margin: 0;">Agente Cosmic — Powered by Google Cloud</p>

</body>
</html>
```

- [ ] **Step 4: Implementar el método**

En `core/content_pipeline/email_sender.py`, agregar al final de la clase `EmailSender`
(después del método `send_daily`, que termina en la línea 78):

```python

    def send_trial_expired(self, job: AnalysisJob, brand_dna: BrandDNA) -> None:
        payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={job.user.tenant_id}"
        html = render_to_string('content_pipeline/email_trial_expired.html', {
            'brand_dna': brand_dna,
            'payment_url': payment_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'⏳ Tu semana gratis de {name} terminó' if name else '⏳ Tu semana gratis terminó'
        plain = (
            f'Tu semana gratis de contenido para {name} terminó. '
            f'Paga para seguir generando contenido: {payment_url}'
        ) if name else f'Tu semana gratis terminó. Paga para seguir generando contenido: {payment_url}'
        send_mail(
            subject,
            plain,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email],
            html_message=html,
            fail_silently=False,
        )
        EMAILS_SENT.labels(type='trial_expired').inc()
        logger.info(f"Email de trial expirado enviado a {job.email} para job {job.id}")
```

- [ ] **Step 5: Correr el test para confirmar que pasa**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py -v`
Expected: PASS (6 tests: 5 preexistentes + el nuevo).

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/email_sender.py core/content_pipeline/templates/content_pipeline/email_trial_expired.html core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "feat(subscriptions): correo de trial expirado con link de pago"
```

---

### Task 4: Job de expiración de trials + management command

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Create: `core/tenant_management/management/commands/expire_stale_trials.py`
- Test: `core/content_pipeline/tests/test_tasks.py`, `core/tenant_management/tests/test_commands.py`

**Interfaces:**
- Consumes: `EmailSender.send_trial_expired` (Task 3), `Subscription.status`/`trial_ends_at`
  (Task 1).
- Produces: `expire_stale_trials_task() -> None` en `core.content_pipeline.tasks` — invocado
  por el management command `expire_stale_trials` (que un cron externo al repo debe llamar
  diario; configurar ese cron en el servidor queda fuera de este plan, es un paso de infra
  que Anuar hace manualmente, mismo criterio que ya aplica hoy a `reset_daily_usage` y
  `cleanup_deactivated_images`, ninguno de los 2 tiene wiring de cron en el repo tampoco).

- [ ] **Step 1: Escribir el test que falla**

En `core/content_pipeline/tests/test_tasks.py`, agregar esta fixture después de
`job_with_dna_and_tenant` (la que se creó en la Task 2):

```python
@pytest.fixture
def trialing_job_with_tenant(job_with_dna_and_tenant):
    from core.tenant_management.models import Subscription
    Subscription.objects.filter(tenant=job_with_dna_and_tenant.user.tenant).update(
        status='trialing', trial_ends_at=timezone.now() - timedelta(hours=1),
    )
    return job_with_dna_and_tenant
```

Agregar estos tests al final del archivo:

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_expire_stale_trials_sends_email_and_expires_subscription(trialing_job_with_tenant):
    from core.tenant_management.models import Subscription
    from core.content_pipeline.models import ContentCalendar
    ContentCalendar.objects.create(brand_dna=trialing_job_with_tenant.brand_dna)

    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()

    MockEmail.return_value.send_trial_expired.assert_called_once()
    call_kwargs = MockEmail.return_value.send_trial_expired.call_args[1]
    assert call_kwargs['job'] == trialing_job_with_tenant

    sub = Subscription.objects.get(tenant=trialing_job_with_tenant.user.tenant)
    assert sub.status == 'trial_expired'


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_expire_stale_trials_ignores_active_subscriptions(job_with_dna_and_tenant):
    from core.tenant_management.models import Subscription
    Subscription.objects.filter(tenant=job_with_dna_and_tenant.user.tenant).update(
        status='active', trial_ends_at=None,
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()

    MockEmail.return_value.send_trial_expired.assert_not_called()
    sub = Subscription.objects.get(tenant=job_with_dna_and_tenant.user.tenant)
    assert sub.status == 'active'


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_expire_stale_trials_is_idempotent(trialing_job_with_tenant):
    from core.tenant_management.models import Subscription
    from core.content_pipeline.models import ContentCalendar
    ContentCalendar.objects.create(brand_dna=trialing_job_with_tenant.brand_dna)

    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()
        expire_stale_trials_task()

    assert MockEmail.return_value.send_trial_expired.call_count == 1
```

En `core/tenant_management/tests/test_commands.py`, agregar al final del archivo (dentro de
la clase `TestManagementCommands`, mismo nivel de indentación que `test_reset_daily_usage_command`):

```python
    def test_expire_stale_trials_command(self):
        """
        Verifica que el comando expire_stale_trials se ejecuta sin errores.
        """
        from unittest.mock import patch
        with patch('core.content_pipeline.tasks.expire_stale_trials_task') as mock_task:
            call_command('expire_stale_trials')
        mock_task.assert_called_once()
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py::test_expire_stale_trials_sends_email_and_expires_subscription -v`
Expected: FAIL — `ImportError: cannot import name 'expire_stale_trials_task'`.

- [ ] **Step 3: Implementar la tarea**

En `core/content_pipeline/tasks.py`, agregar al final del archivo:

```python


def expire_stale_trials_task() -> None:
    expired = Subscription.objects.filter(
        status='trialing', trial_ends_at__lte=timezone.now()
    ).select_related('tenant')
    for sub in expired:
        job = AnalysisJob.objects.filter(
            user__tenant=sub.tenant, generation_mode=AnalysisJob.MODE_FULL,
        ).order_by('-created_at').first()
        if job and hasattr(job, 'brand_dna'):
            try:
                EmailSender().send_trial_expired(job=job, brand_dna=job.brand_dna)
            except Exception as email_err:
                logger.error(f"Email de trial expirado falló para tenant {sub.tenant_id} (no fatal): {email_err}")
        else:
            logger.warning(f"No se encontró AnalysisJob completo para tenant {sub.tenant_id} — trial expira sin correo")
        sub.status = 'trial_expired'
        sub.save(update_fields=['status'])
```

- [ ] **Step 4: Crear el management command**

Crear `core/tenant_management/management/commands/expire_stale_trials.py`:

```python
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Notifica y degrada a trial_expired las suscripciones cuyo trial de 7 dias vencio sin pago'

    def handle(self, *args, **options):
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()
        self.stdout.write(self.style.SUCCESS('Proceso de expiracion de trials finalizado.'))
```

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`
Run: `docker compose exec backend pytest core/tenant_management/tests/test_commands.py -v`
Expected: PASS en ambos archivos.

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py core/tenant_management/management/commands/expire_stale_trials.py core/tenant_management/tests/test_commands.py
GIT_EDITOR=true git commit -m "feat(subscriptions): job de expiracion diaria de trials vencidos"
```

---

### Task 5: Webhook de Stripe

**Files:**
- Create: `core/brand_dna/stripe_views.py`
- Modify: `core/brand_dna/urls.py`
- Test: `core/brand_dna/tests/test_stripe_views.py` (archivo nuevo)

**Interfaces:**
- Consumes: `settings.STRIPE_WEBHOOK_SECRET` (Task 1), `Subscription` (Task 1).
- Produces: endpoint `POST /stripe/webhook/` (`name='stripe_webhook'`) — no lo consume
  ninguna otra tarea de este plan (Stripe lo llama desde fuera del sistema).

- [ ] **Step 1: Escribir el test que falla**

Crear `core/brand_dna/tests/test_stripe_views.py`:

```python
import uuid
import pytest
import stripe
from unittest.mock import patch
from django.test import Client, override_settings
from core.tenant_management.models import TenantModel, Subscription, Plan

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_with_subscription():
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    tenant = TenantModel.objects.create(name='Tenant Test', status='active')
    Subscription.objects.create(tenant=tenant, plan=plan, status='trialing')
    return tenant


def _fake_event(event_id, tenant_id):
    return {
        'id': event_id,
        'type': 'checkout.session.completed',
        'data': {'object': {'client_reference_id': str(tenant_id)}},
    }


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


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_invalid_signature_returns_400_without_changes(tenant_with_subscription):
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               side_effect=stripe.error.SignatureVerificationError('bad sig', 'sig_header')):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='bad',
        )
    assert response.status_code == 400
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'trialing'


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_unknown_tenant_returns_200_and_logs(tenant_with_subscription):
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_event('evt_2', uuid.uuid4())):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'trialing'


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_repeated_event_is_idempotent(tenant_with_subscription):
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_event('evt_3', tenant_with_subscription.id)):
        c.post('/stripe/webhook/', data=b'{}', content_type='application/json', HTTP_STRIPE_SIGNATURE='t=1,v1=fake')
        response = c.post('/stripe/webhook/', data=b'{}', content_type='application/json', HTTP_STRIPE_SIGNATURE='t=1,v1=fake')
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'active'
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.brand_dna.stripe_views'`.

- [ ] **Step 3: Implementar la vista**

Crear `core/brand_dna/stripe_views.py`:

```python
import logging
import stripe
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from core.tenant_management.models import Subscription

logger = logging.getLogger(__name__)


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

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        tenant_id = session.get('client_reference_id')
        updated = Subscription.objects.filter(tenant_id=tenant_id).update(
            status='active', trial_ends_at=None,
        )
        if not updated:
            logger.error(f"Webhook de Stripe: no se encontro tenant {tenant_id} para el evento {event['id']}")
        else:
            logger.info(f"Suscripcion activada para tenant {tenant_id} via Stripe")

    return HttpResponse(status=200)
```

- [ ] **Step 4: Registrar la URL**

En `core/brand_dna/urls.py`, cambiar el import de la línea 2:

```python
from . import views, auth_views
```

por:

```python
from . import views, auth_views, stripe_views
```

Y agregar esta línea al final de `urlpatterns` (después de la línea 33,
`path('api/calendar/<uuid:job_id>/regenerate/', ...)`):

```python
    path('stripe/webhook/', stripe_views.stripe_webhook_view, name='stripe_webhook'),
```

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add core/brand_dna/stripe_views.py core/brand_dna/urls.py core/brand_dna/tests/test_stripe_views.py
GIT_EDITOR=true git commit -m "feat(subscriptions): webhook de Stripe para activar suscripciones"
```

---

### Task 6: Gate de pago en `calendar_feedback_api` + UI

**Files:**
- Modify: `core/brand_dna/views.py:519-558`
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html:430-462`
- Test: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: `settings.STRIPE_PAYMENT_LINK_URL` (Task 1), `Subscription.status` (Task 1,
  poblado por Tasks 2/4/5).
- Produces: nada nuevo para otras tareas de este plan — es el punto de consumo final del
  campo `status`.

- [ ] **Step 1: Escribir los tests que fallan**

En `core/brand_dna/tests/test_views.py`, agregar después de
`test_calendar_feedback_api_yes_triggers_generate_next_week` (después de la línea 625):

```python
def test_calendar_feedback_api_yes_allowed_when_trialing(client, user, job_with_calendar):
    user.tenant.subscription.status = 'trialing'
    user.tenant.subscription.trial_ends_at = timezone.now() + timedelta(days=2)
    user.tenant.subscription.save(update_fields=['status', 'trial_ends_at'])
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


@override_settings(STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_calendar_feedback_api_yes_blocked_when_trial_expired(client, user, job_with_calendar):
    user.tenant.subscription.status = 'trial_expired'
    user.tenant.subscription.save(update_fields=['status'])
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '5',
            'continue_decision': 'yes',
        })
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'payment_required'
    assert data['payment_url'] == f'https://buy.stripe.com/test123?client_reference_id={user.tenant_id}'
    mock_rq.enqueue.assert_not_called()

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_YES
    calendar.refresh_from_db()
    assert calendar.next_week_generating is False
```

`test_views.py` no importa `override_settings` todavía (su línea 6 es
`from django.test import Client`). Cambiar esa línea a:

```python
from django.test import Client, override_settings
```

Agregar también este test para la parte de UI (mismo estilo que
`test_calendar_review_shows_feedback_banner_when_pending`, que ya assert-ea sobre el HTML
crudo de la respuesta):

```python
def test_calendar_review_feedback_js_handles_payment_required(client, user, job_with_calendar):
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert b"data.status === 'payment_required'" in response.content
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py::test_calendar_feedback_api_yes_blocked_when_trial_expired -v`
Expected: FAIL — la respuesta trae `continue_decision` en vez de `status: 'payment_required'`
(el gate todavía no existe), y `mock_rq.enqueue` sí fue llamado.

- [ ] **Step 3: Implementar el gate**

En `core/brand_dna/views.py`, reemplazar las líneas 552-556:

```python
    if feedback.continue_decision == WeeklyFeedback.CONTINUE_YES:
        next_week = feedback.week_number + 1
        calendar.next_week_generating = True
        calendar.save(update_fields=['next_week_generating'])
        django_rq.enqueue(generate_next_week, str(calendar.id), next_week, job_timeout=2400)
```

por:

```python
    if feedback.continue_decision == WeeklyFeedback.CONTINUE_YES:
        subscription = job.user.tenant.subscription
        if subscription.status == 'trial_expired':
            payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={job.user.tenant_id}"
            return JsonResponse({'status': 'payment_required', 'payment_url': payment_url})
        next_week = feedback.week_number + 1
        calendar.next_week_generating = True
        calendar.save(update_fields=['next_week_generating'])
        django_rq.enqueue(generate_next_week, str(calendar.id), next_week, job_timeout=2400)
```

- [ ] **Step 4: Implementar el cambio en el JS**

En `core/brand_dna/templates/brand_dna/calendar_review.html`, dentro de `submitFeedback`,
reemplazar el bloque (líneas 446-456):

```javascript
      const data = await res.json();
      if (data.status === 'ok') {
        showToast('¡Gracias! Tu próxima semana está en camino 🚀');
        banner.innerHTML = '<div style="color:#7ec8a4;font-size:0.9rem;line-height:1.6;">☕ <strong style="color:#a8e6c8;">Tu próxima semana se está generando.</strong> No hace falta que esperes aquí — te avisamos por correo en cuanto esté lista.</div>';
        banner.style.border = '1px solid #2d6a4f';
        banner.style.background = '#0d2218';
      } else {
        showToast('Error al enviar feedback', '#e74c3c', 5000);
        buttons.forEach(b => b.disabled = false);
        btn.textContent = originalText;
      }
```

por:

```javascript
      const data = await res.json();
      if (data.status === 'ok') {
        showToast('¡Gracias! Tu próxima semana está en camino 🚀');
        banner.innerHTML = '<div style="color:#7ec8a4;font-size:0.9rem;line-height:1.6;">☕ <strong style="color:#a8e6c8;">Tu próxima semana se está generando.</strong> No hace falta que esperes aquí — te avisamos por correo en cuanto esté lista.</div>';
        banner.style.border = '1px solid #2d6a4f';
        banner.style.background = '#0d2218';
      } else if (data.status === 'payment_required') {
        banner.innerHTML = `<div style="color:#f0c040;font-size:0.9rem;line-height:1.6;">
          💳 <strong style="color:#ffda6a;">Tu semana gratis terminó.</strong> Activa tu suscripción para generar tu próxima semana.
          <a href="${data.payment_url}" style="display:inline-block;margin-top:12px;padding:10px 20px;background:#e94560;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">Activar mi suscripción →</a>
        </div>`;
        banner.style.border = '1px solid #e94560';
      } else {
        showToast('Error al enviar feedback', '#e74c3c', 5000);
        buttons.forEach(b => b.disabled = false);
        btn.textContent = originalText;
      }
```

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -v`
Expected: PASS — todos los tests del archivo, incluyendo los 3 nuevos y sin regresión en
`test_calendar_feedback_api_yes_triggers_generate_next_week` (usa `status='active'` por
default, sigue permitiendo la generación).

- [ ] **Step 6: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/templates/brand_dna/calendar_review.html core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(subscriptions): gate de pago en calendar_feedback_api"
```

---

### Verificación final

- [ ] Correr la suite completa antes de dar por terminado el plan:

Run: `docker compose exec backend pytest core/ -v`
Expected: 0 failures.

- [ ] Confirmar manualmente (fuera de tests automatizados, con el dev server corriendo) que
`/stripe/webhook/` responde 400 ante una petición sin firma válida y que el banner de
`calendar_review.html` muestra el botón de pago cuando se fuerza `status='trial_expired'`
en un tenant de prueba vía Django shell.

- [ ] Nota para Anuar, fuera del alcance de este plan: configurar en el servidor (fuera del
repo) el cron que llame `python manage.py expire_stale_trials` una vez al día, y dar de alta
`STRIPE_WEBHOOK_SECRET`/`STRIPE_PAYMENT_LINK_URL` en `.env`/`.env.prod`.
