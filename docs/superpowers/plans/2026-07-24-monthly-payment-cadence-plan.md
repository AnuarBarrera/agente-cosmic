# Pago puntual mensual + CTA temprano Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el ciclo semanal de feedback (`WeeklyFeedback`, `generate_next_week`,
`calendar_feedback_api`) por generación mensual disparada directamente por el pago
puntual de Stripe — sin banner de "¿continuar?", sin suscripción recurrente. Agregar un
CTA temprano (día 1) y 2 mejoras de UX del flujo diario.

**Architecture:** El gate de pago se calcula al vuelo comparando `Subscription.status`/
`paid_until` contra la fecha actual — sin tabla intermedia. `checkout.session.completed`
(cualquier pago, primero o repetido) pone `paid_until = now+28d` y encola
`generate_next_month`, que reutiliza el mismo patrón de generación ya probado
(`TextGenerator`/`ImageGenerator`/etc.) 4 veces seguidas para producir 28 días de una.

**Tech Stack:** Django 5.2, django-rq, pytest-django, librería `stripe` (ya instalada).

## Global Constraints

- Spec de referencia: `docs/superpowers/specs/2026-07-24-monthly-payment-cadence-design.md`
  — ante cualquier ambigüedad no cubierta aquí, esa spec es la autoridad.
- **Nunca la palabra "suscripción" en copy visible al usuario** (botones, banners,
  correos) — el mensaje es "generas tu mes al momento, ahorras tiempo", nunca "pago
  mensual automático".
- `paid_until = now + timedelta(days=28)` en cada pago confirmado — mismo tratamiento
  sin importar si es el primer pago o el repago número 10.
- El gate se calcula siempre así (mismo criterio en `calendar_review_view` y en
  cualquier otro lugar que lo necesite):
  ```python
  payment_needed = subscription and (
      subscription.status == 'trial_expired'
      or (subscription.paid_until and subscription.paid_until <= timezone.now())
  )
  ```
  `'past_due'` **nunca** entra en esta condición.
- **Fechas en español sin depender de i18n de Django** — `LANGUAGE_CODE = 'en-us'` en
  `saas_chatbot/settings.py:389`, así que `{{ fecha|date:"F" }}` renderizaría el mes en
  inglés. Se usa una tabla de meses en español hecha a mano (`_MESES_ES`), nunca el
  filtro `date` de Django para nombres de mes.
- Los handlers de webhook de suscripción recurrente
  (`customer.subscription.updated/deleted`, `invoice.payment_failed/succeeded`) **no se
  tocan ni se borran** en este plan — quedan tal cual, sin uso, por decisión explícita
  de Anuar.
- El campo `ContentCalendar.next_week_generating` **no se renombra** — sigue
  cumpliendo el mismo rol (flag de "hay una generación en curso"), solo cambia qué lo
  setea y qué texto ve el usuario. Minimizar diff, evitar una migración/rename
  innecesarios.
- Fuera de alcance (no crear tareas para esto): reconfigurar el Payment Link en el
  Dashboard de Stripe a modo pago puntual (paso manual de Anuar), prorrateo de pagos
  anticipados, cambios al modelo `Plan`/precios reales.

---

### Task 1: `Subscription.paid_until` + migración

**Files:**
- Modify: `core/tenant_management/models.py:67-86` (clase `Subscription`)
- Create: `core/tenant_management/migrations/0023_subscription_paid_until.py`
- Test: `core/tenant_management/tests/test_models.py`

**Interfaces:**
- Produces: `Subscription.paid_until` (`DateTimeField`, `null=True, blank=True`) —
  usado por las Tasks 4, 5, 6.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `core/tenant_management/tests/test_models.py`:

```python
def test_subscription_paid_until_defaults_to_none():
    plan = Plan.objects.create(name='Plan Test Paid Until')
    tenant = TenantModel.objects.create(name='Tenant Paid Until', status='active')
    sub = Subscription.objects.create(tenant=tenant, plan=plan)
    assert sub.paid_until is None


def test_subscription_paid_until_accepts_datetime():
    plan = Plan.objects.create(name='Plan Test Paid Until 2')
    tenant = TenantModel.objects.create(name='Tenant Paid Until 2', status='active')
    paid_until = timezone.now() + timezone.timedelta(days=28)
    sub = Subscription.objects.create(tenant=tenant, plan=plan, status='active', paid_until=paid_until)
    sub.refresh_from_db()
    assert sub.status == 'active'
    assert sub.paid_until == paid_until
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/tenant_management/tests/test_models.py::test_subscription_paid_until_defaults_to_none -v`
Expected: FAIL — `TypeError: Subscription() got unexpected keyword arguments: 'paid_until'`.

- [ ] **Step 3: Agregar el campo**

En `core/tenant_management/models.py`, agregar después de `cancel_at_period_end`
(línea 77):

```python
    paid_until = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 4: Generar la migración**

Run: `docker compose exec backend python manage.py makemigrations tenant_management`
Expected: crea `core/tenant_management/migrations/0023_subscription_paid_until.py`
(renombrar el archivo a ese nombre si el generado automático difiere). Verificar que
coincide con:

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_management', '0022_subscription_stripe_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='paid_until',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
```

- [ ] **Step 5: Aplicar y correr los tests**

Run: `docker compose exec backend python manage.py migrate tenant_management`
Run: `docker compose exec backend pytest core/tenant_management/tests/test_models.py -v`
Expected: PASS (6 tests: 4 preexistentes + 2 nuevos).

- [ ] **Step 6: Commit**

```bash
git add core/tenant_management/models.py core/tenant_management/migrations/0023_subscription_paid_until.py core/tenant_management/tests/test_models.py
GIT_EDITOR=true git commit -m "feat(monthly-payment): agrega Subscription.paid_until"
```

---

### Task 2: Retirar `WeeklyFeedback` por completo

**Files:**
- Modify: `core/content_pipeline/models.py` (quitar la clase `WeeklyFeedback`)
- Create: `core/content_pipeline/migrations/000X_delete_weeklyfeedback.py`
- Modify: `core/tenant_management/admin.py` (quitar `WeeklyFeedbackAdmin` + import + registro)
- Modify: `core/content_pipeline/tests/test_models.py` (quitar los 2 tests de `WeeklyFeedback`)

**Interfaces:**
- Consumes: nada (esta tarea es de eliminación).
- Produces: nada — la Task 5 depende de que este modelo ya no exista antes de escribir
  el nuevo cálculo del gate.

- [ ] **Step 1: Quitar la clase del modelo**

En `core/content_pipeline/models.py`, eliminar la clase `WeeklyFeedback` completa
(actualmente líneas 84-109, buscar `class WeeklyFeedback(models.Model):` hasta el
`return f"Feedback semana...")` que la cierra).

- [ ] **Step 2: Quitar los tests que la usan**

En `core/content_pipeline/tests/test_models.py`, quitar:
- El import `WeeklyFeedback` de la línea 6 (queda
  `from core.content_pipeline.models import ContentCalendar, ContentPost`).
- Las funciones `test_weekly_feedback_defaults` (líneas 55-62) y
  `test_weekly_feedback_unique_per_calendar_and_week` (líneas 65-70), y el import
  `IntegrityError, transaction` de la línea 2 si no se usa en ningún otro test del
  archivo (confirmar con `grep -n "IntegrityError\|transaction" core/content_pipeline/tests/test_models.py`
  antes de quitarlo — si solo lo usaban esos 2 tests, se quita también).

- [ ] **Step 3: Generar la migración de borrado**

Run: `docker compose exec backend python manage.py makemigrations content_pipeline`
Expected: crea una migración con `migrations.DeleteModel(name='WeeklyFeedback')`.
Verificar el nombre real del archivo generado (depende del número de la última
migración de `content_pipeline` en el repo — revisar
`ls core/content_pipeline/migrations/` antes de este paso para saber el número
siguiente) y que el `Meta.dependencies` apunte a la migración anterior real de esa
app.

- [ ] **Step 4: Quitar del admin**

En `core/tenant_management/admin.py`:
- Quitar el import de `WeeklyFeedback` (línea 12:
  `from core.content_pipeline.models import WeeklyFeedback`).
- Quitar la clase `WeeklyFeedbackAdmin` completa (líneas 126-140).
- Quitar la línea de registro `cosmic_admin.register(WeeklyFeedback, WeeklyFeedbackAdmin)`.

- [ ] **Step 5: Aplicar la migración y correr los tests**

Run: `docker compose exec backend python manage.py migrate content_pipeline`
Run: `docker compose exec backend pytest core/content_pipeline/tests/test_models.py core/tenant_management/tests/test_admin_access.py -v`
Expected: PASS — sin errores de importación ni de tabla faltante.

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/models.py core/content_pipeline/migrations/ core/content_pipeline/tests/test_models.py core/tenant_management/admin.py
GIT_EDITOR=true git commit -m "feat(monthly-payment): retira WeeklyFeedback por completo"
```

---

### Task 3: `generate_next_month` reemplaza `generate_next_week`

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Modify: `core/content_pipeline/email_sender.py`
- Modify: `core/content_pipeline/tests/test_tasks.py`
- Modify: `core/content_pipeline/tests/test_email_sender.py`
- Modify: `core/shared/tests/test_check_rq_safe_to_deploy_command.py`

**Interfaces:**
- Consumes: nada nuevo (mismos generadores ya usados en `content_generation_task`).
- Produces: `generate_next_month(calendar_id: str) -> None` en
  `core.content_pipeline.tasks` — usado por la Task 4 (webhook).
  `EmailSender.send_month_ready(self, job: AnalysisJob, brand_dna: BrandDNA) -> None`
  en `core.content_pipeline.email_sender`.

- [ ] **Step 1: Escribir el test de `send_month_ready` que falla**

Agregar al final de `core/content_pipeline/tests/test_email_sender.py`:

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_month_ready_email_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_month_ready(job=job, brand_dna=dna)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    subject = mock_send.call_args[0][0]
    assert 'Tu Web MX' in subject
    assert 'mes' in subject.lower()
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py::test_send_month_ready_email_calls_django_send -v`
Expected: FAIL — `AttributeError: 'EmailSender' object has no attribute 'send_month_ready'`.

- [ ] **Step 3: Implementar `send_month_ready`, quitar `send_week_ready`**

En `core/content_pipeline/email_sender.py`, reemplazar el método `send_week_ready`
completo (líneas 35-53) por:

```python
    def send_month_ready(self, job: AnalysisJob, brand_dna: BrandDNA) -> None:
        calendar_url = settings.COSMIC_BASE_URL + reverse('calendar_review', args=[job.id])
        html = render_to_string('content_pipeline/email_initial.html', {
            'brand_dna': brand_dna,
            'calendar_url': calendar_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'✅ Tu mes de contenido está listo — {name}' if name else '✅ Tu mes de contenido está listo — Agente Cosmic'
        plain = f'Tu mes de contenido de {name} está listo.' if name else 'Tu mes de contenido está listo.'
        send_mail(
            subject,
            plain,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email],
            html_message=html,
            fail_silently=False,
        )
        EMAILS_SENT.labels(type='month_ready').inc()
        logger.info(f"Email de mes listo enviado a {job.email} para job {job.id}")
```

- [ ] **Step 4: Correr el test de email para confirmar que pasa**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py -v`
Expected: PASS. Si existía un test para `send_week_ready`, buscarlo
(`grep -n "send_week_ready" core/content_pipeline/tests/test_email_sender.py`) y
quitarlo — ya no existe el método.

- [ ] **Step 5: Escribir los tests de `generate_next_month` que fallan**

En `core/content_pipeline/tests/test_tasks.py`, quitar los 3 tests de
`generate_next_week` (`test_generate_next_week_creates_posts_for_week_2`,
`test_generate_next_week_does_not_collide_with_last_post_date`,
`test_generate_next_week_resets_flag_even_on_failure` — buscar
`def test_generate_next_week` para ubicarlos exactos) y reemplazarlos por:

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
def test_generate_next_month_creates_28_posts(job_with_dna):
    from core.content_pipeline.tasks import content_generation_task, generate_next_month
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
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


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_generate_next_month_sends_month_ready_email(job_with_dna):
    from core.content_pipeline.tasks import content_generation_task, generate_next_month
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        content_generation_task(str(job_with_dna.id))
        calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
        MockEmail.reset_mock()

        generate_next_month(str(calendar.id))

    MockEmail.return_value.send_month_ready.assert_called_once()


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_generate_next_month_resets_flag_even_on_failure(job_with_dna):
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
```

- [ ] **Step 6: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py::test_generate_next_month_creates_28_posts -v`
Expected: FAIL — `ImportError: cannot import name 'generate_next_month'`.

- [ ] **Step 7: Implementar `generate_next_month`, quitar `generate_next_week`**

En `core/content_pipeline/tasks.py`, reemplazar la función `generate_next_week`
completa (líneas 250-313) por:

```python
def generate_next_month(calendar_id: str) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    brand_dna = calendar.brand_dna
    job_id = str(brand_dna.job.id)
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

        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        text_gen = TextGenerator()

        for batch in range(4):
            posts_data = text_gen.generate(brand_dna)
            for i, post_data in enumerate(posts_data, start=1):
                day_number = base_day + (batch * 7) + i
                scheduled = scheduled_dates[batch * 7 + i - 1]
                image_url, image_urls, video_url = _generate_post_media(
                    image_gen, reel_script_gen, reel_gen,
                    fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                    filename=f"{job_id}-day{day_number}",
                    caption=post_data['caption'],
                    colors=brand_dna.primary_colors,
                    tone=brand_dna.tone,
                    brand_name=brand_dna.business_name,
                    keywords=brand_dna.keywords,
                    description=brand_dna.description,
                    audience=brand_dna.audience,
                    business_url=brand_dna.business_url,
                    brand_dna=brand_dna,
                    post_data=post_data,
                )
                ContentPost.objects.create(
                    calendar=calendar,
                    day_number=day_number,
                    caption=post_data['caption'],
                    image_url=image_url,
                    image_urls=image_urls,
                    video_url=video_url,
                    format=post_data.get('format', ContentPost.FORMAT_SINGLE),
                    suggested_time=scheduled.astimezone(MEXICO_TZ).time(),
                    hashtags=post_data.get('hashtags', []),
                    scheduled_at=scheduled,
                )

        schedule_daily_emails(calendar)

        try:
            EmailSender().send_month_ready(job=brand_dna.job, brand_dna=brand_dna)
        except Exception as email_err:
            logger.error(f"Email de mes listo falló para calendar {calendar_id} (no fatal): {email_err}")
    except Exception as e:
        logger.error(f"generate_next_month error para calendar {calendar_id}: {e}")
    finally:
        calendar.next_week_generating = False
        calendar.save(update_fields=['next_week_generating'])
```

- [ ] **Step 8: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: PASS — todos, incluyendo los 3 nuevos de `generate_next_month`.

- [ ] **Step 9: Actualizar la referencia de ejemplo en `check_rq_safe_to_deploy`**

En `core/shared/tests/test_check_rq_safe_to_deploy_command.py`, cambiar (línea 23):

```python
    fake_job.func_name = 'core.content_pipeline.tasks.generate_next_week'
```

por:

```python
    fake_job.func_name = 'core.content_pipeline.tasks.generate_next_month'
```

y (línea 24):

```python
    fake_job.args = ('calendar-id', 2)
```

por:

```python
    fake_job.args = ('calendar-id',)
```

y (línea 37):

```python
    assert 'generate_next_week' in out.getvalue()
```

por:

```python
    assert 'generate_next_month' in out.getvalue()
```

Run: `docker compose exec backend pytest core/shared/tests/test_check_rq_safe_to_deploy_command.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/email_sender.py core/content_pipeline/tests/test_tasks.py core/content_pipeline/tests/test_email_sender.py core/shared/tests/test_check_rq_safe_to_deploy_command.py
GIT_EDITOR=true git commit -m "feat(monthly-payment): generate_next_month reemplaza generate_next_week"
```

---

### Task 4: El webhook dispara `generate_next_month` al confirmar el pago

**Files:**
- Modify: `core/brand_dna/stripe_views.py`
- Modify: `core/brand_dna/tests/test_stripe_views.py`

**Interfaces:**
- Consumes: `Subscription.paid_until` (Task 1), `generate_next_month` (Task 3).
- Produces: nada nuevo para otras tareas.

- [ ] **Step 1: Escribir los tests que fallan**

En `core/brand_dna/tests/test_stripe_views.py`, actualizar el test
`test_webhook_valid_signature_activates_subscription` (agregar la aserción de
`paid_until`, y agregar `setup` de `AnalysisJob`/`BrandDNA`/`ContentCalendar` para que
el nuevo código pueda resolver el calendario) — reemplazar la función completa por:

```python
@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_valid_signature_activates_subscription(tenant_with_subscription):
    from core.brand_dna.models import AnalysisJob, BrandDNA
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username='t1@t.com', email='t1@t.com', password='pass1234')
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
               return_value=_fake_event('evt_1', tenant_with_subscription.id)), \
         patch('core.brand_dna.stripe_views.django_rq') as mock_rq:
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'active'
    assert sub.trial_ends_at is None
    assert sub.paid_until is not None
    assert sub.paid_until > timezone.now() + timedelta(days=27)
    assert sub.stripe_customer_id == 'cus_test1'
    mock_rq.enqueue.assert_called_once()
```

`ContentCalendar` no existe todavía para este `job` — el gate de este test es exigir
que, aun así, el webhook responda 200 y active la suscripción; el `mock_rq.enqueue` se
verifica en un test aparte con calendario real (siguiente). Agregar
`from datetime import timedelta` y `from django.utils import timezone` a los imports
del archivo si no están ya.

Agregar al final del archivo:

```python
@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_payment_enqueues_generate_next_month(tenant_with_subscription):
    from core.brand_dna.models import AnalysisJob, BrandDNA
    from core.content_pipeline.models import ContentCalendar
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username='t2@t.com', email='t2@t.com', password='pass1234')
    user.tenant = tenant_with_subscription
    user.save(update_fields=['tenant'])
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com', user=user,
        status=AnalysisJob.STATUS_DONE, generation_mode=AnalysisJob.MODE_FULL,
    )
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    calendar = ContentCalendar.objects.create(brand_dna=dna)
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_event('evt_2', tenant_with_subscription.id)), \
         patch('core.brand_dna.stripe_views.django_rq') as mock_rq:
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    mock_rq.enqueue.assert_called_once()
    enqueue_args = mock_rq.enqueue.call_args[0]
    assert enqueue_args[1] == str(calendar.id)
    calendar.refresh_from_db()
    assert calendar.next_week_generating is True
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py -v`
Expected: FAIL — `paid_until` sigue en `None`, `mock_rq.enqueue` nunca se llama
(todavía no existe ese código).

- [ ] **Step 3: Implementar**

En `core/brand_dna/stripe_views.py`, agregar los imports al inicio del archivo:

```python
import django_rq
from datetime import timedelta
from django.utils import timezone
```

Cambiar la firma de `_job_for_tenant` (recibe `tenant_id` en vez de un objeto
`TenantModel`):

```python
def _job_for_tenant(tenant_id):
    return AnalysisJob.objects.filter(
        user__tenant_id=tenant_id, generation_mode=AnalysisJob.MODE_FULL,
    ).order_by('-created_at').first()
```

Actualizar el único call site existente (dentro del bloque `invoice.payment_failed`,
que no se toca en su lógica, solo el argumento):

```python
            job = _job_for_tenant(sub.tenant_id)
```

Reemplazar el bloque `if event_type == 'checkout.session.completed':` completo por:

```python
    if event_type == 'checkout.session.completed':
        session = event['data']['object']
        tenant_id = getattr(session, 'client_reference_id', None)
        updated = Subscription.objects.filter(tenant_id=tenant_id).update(
            status='active',
            trial_ends_at=None,
            paid_until=timezone.now() + timedelta(days=28),
            stripe_customer_id=getattr(session, 'customer', '') or '',
        )
        if not updated:
            logger.error(f"Webhook de Stripe: no se encontro tenant {tenant_id} para el evento {event['id']}")
        else:
            logger.info(f"Pago confirmado para tenant {tenant_id} via Stripe")
            from core.content_pipeline.tasks import generate_next_month
            job = _job_for_tenant(tenant_id)
            if job and hasattr(job, 'brand_dna') and hasattr(job.brand_dna, 'calendar'):
                calendar = job.brand_dna.calendar
                calendar.next_week_generating = True
                calendar.save(update_fields=['next_week_generating'])
                django_rq.enqueue(generate_next_month, str(calendar.id), job_timeout=2400)
            else:
                logger.warning(f"No se encontro calendario para tenant {tenant_id} — pago confirmado sin generar mes")
```

- [ ] **Step 4: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_stripe_views.py -v`
Expected: PASS — todos, incluyendo los 2 nuevos/actualizados.

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/stripe_views.py core/brand_dna/tests/test_stripe_views.py
GIT_EDITOR=true git commit -m "feat(monthly-payment): el webhook dispara generate_next_month al confirmar el pago"
```

---

### Task 5: Gate calculado al vuelo — retira `calendar_feedback_api`

**Files:**
- Modify: `core/brand_dna/views.py`
- Modify: `core/brand_dna/urls.py`
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html`
- Modify: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: `Subscription.status`/`paid_until` (Task 1).
- Produces: contexto `payment_needed`, `early_cta`, `payment_url` en
  `calendar_review_view` — usado también por la Task 8 (copy/CTA en el mismo
  template).

- [ ] **Step 1: Escribir los tests que fallan**

En `core/brand_dna/tests/test_views.py`, quitar TODAS las funciones cuyo nombre
empieza con `test_calendar_feedback_api_` (10 funciones: `..._no_rating_is_valid`,
`..._no_decision_does_not_generate`, `..._yes_triggers_generate_next_week`,
`..._yes_allowed_when_trialing`, `..._yes_blocked_when_trial_expired`,
`..._yes_blocked_when_canceled`, `..._yes_allowed_when_past_due`,
`..._yes_without_tenant_does_not_crash`, `..._requires_ownership`,
`..._invalid_rating_returns_400`, `..._invalid_continue_decision_returns_400`) y
también `test_calendar_review_exposes_pending_feedback`,
`test_calendar_review_no_pending_feedback_when_none_exists`,
`test_calendar_review_shows_feedback_banner_when_pending`,
`test_calendar_review_shows_banner_again_after_payment_blocked`,
`test_calendar_review_feedback_js_handles_payment_required` — todas dependen de
`calendar_feedback_api`/`WeeklyFeedback`, que ya no existen.

En la fixture `job_with_calendar` (línea ~343), quitar la línea
`WeeklyFeedback.objects.create(calendar=calendar, week_number=1)` — el resto de la
fixture se queda igual. Quitar también `WeeklyFeedback` del import de
`core.content_pipeline.models` en la cabecera del archivo si no se usa en ningún otro
lugar (`grep -n "WeeklyFeedback" core/brand_dna/tests/test_views.py` después de
quitar los tests de arriba para confirmar).

Agregar estos tests nuevos al final del archivo:

```python
def test_calendar_review_shows_payment_banner_when_trial_expired(client, user, job_with_calendar, settings):
    settings.STRIPE_PAYMENT_LINK_URL = 'https://buy.stripe.com/test123'
    user.tenant.subscription.status = 'trial_expired'
    user.tenant.subscription.save(update_fields=['status'])
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert response.context['payment_needed'] is True
    assert response.context['payment_url'] == f'https://buy.stripe.com/test123?client_reference_id={user.tenant_id}'


def test_calendar_review_shows_payment_banner_when_paid_until_passed(client, user, job_with_calendar, settings):
    settings.STRIPE_PAYMENT_LINK_URL = 'https://buy.stripe.com/test123'
    user.tenant.subscription.status = 'active'
    user.tenant.subscription.paid_until = timezone.now() - timedelta(hours=1)
    user.tenant.subscription.save(update_fields=['status', 'paid_until'])
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert response.context['payment_needed'] is True


def test_calendar_review_no_payment_banner_when_paid_until_future(client, user, job_with_calendar):
    user.tenant.subscription.status = 'active'
    user.tenant.subscription.paid_until = timezone.now() + timedelta(days=10)
    user.tenant.subscription.save(update_fields=['status', 'paid_until'])
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert response.context['payment_needed'] is False


def test_calendar_review_no_payment_banner_when_past_due(client, user, job_with_calendar):
    user.tenant.subscription.status = 'past_due'
    user.tenant.subscription.save(update_fields=['status'])
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert response.context['payment_needed'] is False


def test_calendar_review_shows_early_cta_when_trialing(client, user, job_with_calendar, settings):
    settings.STRIPE_PAYMENT_LINK_URL = 'https://buy.stripe.com/test123'
    user.tenant.subscription.status = 'trialing'
    user.tenant.subscription.save(update_fields=['status'])
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert response.context['early_cta'] is True
    assert response.context['payment_needed'] is False


def test_calendar_review_no_early_cta_when_active(client, user, job_with_calendar):
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert response.context['early_cta'] is False


def test_calendar_review_url_no_longer_exists(client, user, job_with_calendar):
    client.force_login(user)
    response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {})
    assert response.status_code == 404
```

Nota: `user` fixture por defecto tiene `Subscription.status='active'` y
`paid_until=None` — confirma que `test_calendar_review_no_early_cta_when_active` y
cualquier test que no cambie el status siguen sin ver ni el banner de pago ni el CTA
temprano.

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py::test_calendar_review_shows_payment_banner_when_trial_expired -v`
Expected: FAIL — `KeyError: 'payment_needed'` (la vista todavía no pasa esa variable).

- [ ] **Step 3: Implementar en `calendar_review_view`**

En `core/brand_dna/views.py`, reemplazar el bloque completo de `calendar_review_view`
(líneas 238-288) por:

```python
@login_required
def calendar_review_view(request, job_id):
    from core.brand_dna.rate_limits import get_user_plan
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    calendar = getattr(brand_dna, 'calendar', None) if brand_dna else None
    posts = list(calendar.posts.order_by('day_number')) if calendar else []
    plan = get_user_plan(request.user)
    total_regens = sum(p.regen_count for p in posts)
    total_edits = sum(p.edit_count for p in posts)

    subscription = getattr(getattr(job.user, 'tenant', None), 'subscription', None)
    payment_needed = bool(subscription and (
        subscription.status == 'trial_expired'
        or (subscription.paid_until and subscription.paid_until <= timezone.now())
    ))
    early_cta = bool(subscription and not payment_needed and subscription.status == 'trialing')
    payment_url = ''
    if payment_needed or early_cta:
        payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={job.user.tenant_id}"

    from core.brand_dna.rate_limits import can_create_calendar
    can_create, _ = can_create_calendar(request.user)

    week_groups = []
    if posts:
        posts_by_week = {}
        for p in posts:
            week_num = ((p.day_number - 1) // 7) + 1
            posts_by_week.setdefault(week_num, []).append(p)
        current_week = max(posts_by_week)
        for week_num in sorted(posts_by_week, reverse=True):
            week_posts = posts_by_week[week_num]
            week_groups.append({
                'week_number': week_num,
                'posts': week_posts,
                'is_current': week_num == current_week,
                'start_iso': min(p.scheduled_at for p in week_posts).isoformat(),
                'end_iso': max(p.scheduled_at for p in week_posts).isoformat(),
            })

    return render(request, 'brand_dna/calendar_review.html', {
        'job': job,
        'brand_dna': brand_dna,
        'calendar': calendar,
        'posts': posts,
        'week_groups': week_groups,
        'max_regenerations': plan.max_post_regenerations,
        'max_edits': plan.max_post_edits,
        'total_regens': total_regens,
        'total_edits': total_edits,
        'can_create_calendar': can_create,
        'payment_needed': payment_needed,
        'early_cta': early_cta,
        'payment_url': payment_url,
    })
```

(`timezone` ya está importado en este archivo en otros lugares — confirmar con
`grep -n "^from django.utils import timezone\|^import.*timezone" core/brand_dna/views.py`;
si no está a nivel de módulo, agregar `from django.utils import timezone` al inicio
del archivo.)

- [ ] **Step 4: Quitar `calendar_feedback_api` y su URL**

En `core/brand_dna/views.py`, quitar la función `calendar_feedback_api` completa
(líneas 519-559, desde `@login_required` que la precede hasta el final de la
función).

En `core/brand_dna/urls.py`, quitar la línea:

```python
    path('api/calendar/<uuid:job_id>/feedback/', views.calendar_feedback_api, name='calendar_feedback_api'),
```

- [ ] **Step 5: Actualizar el banner en `calendar_review.html`**

Reemplazar el bloque `{% if pending_feedback %}...{% endif %}` (líneas 164-171) por:

```html
  {% if payment_needed %}
  <div id="payment-banner" style="background:#1a1a2e;border:1px solid #e94560;border-radius:12px;padding:20px;margin-bottom:24px;">
    <h2 style="font-size:1.1rem;margin-bottom:4px;">💳 Genera tu próximo mes</h2>
    <p style="color:#aaa;font-size:0.85rem;margin-bottom:16px;">Paga para generar tu próximo mes de contenido — listo en minutos.</p>
    <a href="{{ payment_url }}" style="display:block;text-align:center;width:100%;padding:14px;background:#e94560;color:#fff;border-radius:8px;font-weight:700;font-size:1rem;text-decoration:none;">Genera tu próximo mes →</a>
  </div>
  {% elif early_cta %}
  <div id="early-cta-banner" style="background:#1a1a2e;border:1px solid #4a9eff;border-radius:12px;padding:20px;margin-bottom:24px;">
    <h2 style="font-size:1.1rem;margin-bottom:4px;">⚡ ¿Quieres el mes completo desde hoy?</h2>
    <p style="color:#aaa;font-size:0.85rem;margin-bottom:16px;">Paga ahora y genera un mes completo de contenido de inmediato — ahorra horas de trabajo cada semana.</p>
    <a href="{{ payment_url }}" style="display:block;text-align:center;width:100%;padding:14px;background:#4a9eff;color:#fff;border-radius:8px;font-weight:700;font-size:1rem;text-decoration:none;">Genera tu mes completo →</a>
  </div>
  {% endif %}
```

Quitar la función `submitFeedback` completa del `<script>` (buscar
`async function submitFeedback(decision, btn) {` hasta la llave `}` que la cierra,
justo antes de `</script>`).

- [ ] **Step 6: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -v`
Expected: PASS — todos, sin ningún test de `calendar_feedback_api` restante.

- [ ] **Step 7: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/urls.py core/brand_dna/templates/brand_dna/calendar_review.html core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(monthly-payment): gate calculado al vuelo, retira calendar_feedback_api"
```

---

### Task 6: Job de vencimiento cubre también el mes pagado

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Modify: `core/content_pipeline/email_sender.py`
- Create: `core/content_pipeline/templates/content_pipeline/email_month_expired.html`
- Modify: `core/content_pipeline/tests/test_tasks.py`
- Modify: `core/content_pipeline/tests/test_email_sender.py`

**Interfaces:**
- Consumes: `Subscription.paid_until` (Task 1).
- Produces: `EmailSender.send_month_expired(self, job: AnalysisJob, brand_dna: BrandDNA) -> None`.

- [ ] **Step 1: Escribir el test de email que falla**

Agregar al final de `core/content_pipeline/tests/test_email_sender.py`:

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_send_month_expired_email_calls_django_send(full_setup):
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
        sender.send_month_expired(job=job, brand_dna=dna)

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    html = call_kwargs[1]['html_message']
    assert f'https://buy.stripe.com/test123?client_reference_id={tenant.id}' in html
    assert 'suscripción' not in html.lower()
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py::test_send_month_expired_email_calls_django_send -v`
Expected: FAIL — `AttributeError: 'EmailSender' object has no attribute 'send_month_expired'`.

- [ ] **Step 3: Crear el template**

Crear `core/content_pipeline/templates/content_pipeline/email_month_expired.html`:

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Ya pasó un mes — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 580px; margin: 0 auto; padding: 24px 20px; color: #333;">

  <p style="margin: 0 0 16px; font-size: 0.72rem; color: #999; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">📬 Notificación automática</p>

  <p style="font-size: 1.1rem; margin-bottom: 8px;">Hola,</p>
  <p>Ya pasó un mes desde tu última generación de contenido para <strong>{{ brand_dna.business_name }}</strong>.</p>

  <p>Genera un mes nuevo ahora y vuelve a sentir la experiencia de ganar tiempo:</p>

  <div style="text-align: center; margin: 28px 0;">
    <a href="{{ payment_url }}"
       style="display: inline-block; padding: 14px 32px; background: #e94560; color: #fff;
              border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 1rem;">
      Generar mi próximo mes →
    </a>
  </div>

  <p style="color: #555;">Tu contenido ya generado sigue disponible sin restricción — esto
  solo afecta la generación de tu próximo mes.</p>

  <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">
  <p style="font-size: 11px; color: #bbb; margin: 0;">Agente Cosmic — Powered by Google Cloud</p>

</body>
</html>
```

- [ ] **Step 4: Implementar el método**

En `core/content_pipeline/email_sender.py`, agregar al final de la clase:

```python

    def send_month_expired(self, job: AnalysisJob, brand_dna: BrandDNA) -> None:
        payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={job.user.tenant_id}"
        html = render_to_string('content_pipeline/email_month_expired.html', {
            'brand_dna': brand_dna,
            'payment_url': payment_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'⏳ Ya pasó un mes — {name}' if name else '⏳ Ya pasó un mes desde tu última generación'
        plain = (
            f'Ya pasó un mes desde tu última generación de contenido para {name}. '
            f'Genera un mes nuevo ahora: {payment_url}'
        ) if name else f'Ya pasó un mes desde tu última generación de contenido. Genera un mes nuevo ahora: {payment_url}'
        send_mail(
            subject,
            plain,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email],
            html_message=html,
            fail_silently=False,
        )
        EMAILS_SENT.labels(type='month_expired').inc()
        logger.info(f"Email de mes vencido enviado a {job.email} para job {job.id}")
```

- [ ] **Step 5: Correr el test de email para confirmar que pasa**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py -v`
Expected: PASS.

- [ ] **Step 6: Escribir los tests del job que fallan**

Agregar al final de `core/content_pipeline/tests/test_tasks.py`:

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_expire_stale_trials_expires_lapsed_paid_month(job_with_dna_and_tenant):
    from core.tenant_management.models import Subscription
    from core.content_pipeline.models import ContentCalendar
    Subscription.objects.filter(tenant=job_with_dna_and_tenant.user.tenant).update(
        status='active', paid_until=timezone.now() - timedelta(hours=1),
    )
    ContentCalendar.objects.create(brand_dna=job_with_dna_and_tenant.brand_dna)

    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()

    MockEmail.return_value.send_month_expired.assert_called_once()
    MockEmail.return_value.send_trial_expired.assert_not_called()
    sub = Subscription.objects.get(tenant=job_with_dna_and_tenant.user.tenant)
    assert sub.status == 'trial_expired'


def test_expire_stale_trials_ignores_active_with_future_paid_until(job_with_dna_and_tenant):
    from core.tenant_management.models import Subscription
    Subscription.objects.filter(tenant=job_with_dna_and_tenant.user.tenant).update(
        status='active', paid_until=timezone.now() + timedelta(days=10),
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()

    MockEmail.return_value.send_month_expired.assert_not_called()
    sub = Subscription.objects.get(tenant=job_with_dna_and_tenant.user.tenant)
    assert sub.status == 'active'
```

`job_with_dna_and_tenant` ya existe (Task 2 del plan anterior de trial+webhook,
`core/content_pipeline/tests/test_tasks.py`).

- [ ] **Step 7: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py::test_expire_stale_trials_expires_lapsed_paid_month -v`
Expected: FAIL — `AttributeError` en `send_month_expired` (todavía no se llama desde
`expire_stale_trials_task`) o el status no cambia.

- [ ] **Step 8: Implementar**

En `core/content_pipeline/tasks.py`, reemplazar la función `expire_stale_trials_task`
completa por:

```python
def expire_stale_trials_task() -> None:
    now = timezone.now()
    expired_trials = Subscription.objects.filter(
        status='trialing', trial_ends_at__lte=now
    ).select_related('tenant')
    expired_months = Subscription.objects.filter(
        status='active', paid_until__lte=now
    ).select_related('tenant')

    for sub, email_method in [(s, 'send_trial_expired') for s in expired_trials] + \
                              [(s, 'send_month_expired') for s in expired_months]:
        job = AnalysisJob.objects.filter(
            user__tenant=sub.tenant, generation_mode=AnalysisJob.MODE_FULL,
        ).order_by('-created_at').first()
        if job and hasattr(job, 'brand_dna'):
            try:
                getattr(EmailSender(), email_method)(job=job, brand_dna=job.brand_dna)
            except Exception as email_err:
                logger.error(f"Email de vencimiento falló para tenant {sub.tenant_id} (no fatal): {email_err}")
        else:
            logger.warning(f"No se encontró AnalysisJob completo para tenant {sub.tenant_id} — vence sin correo")
        sub.status = 'trial_expired'
        sub.save(update_fields=['status'])
```

- [ ] **Step 9: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: PASS — todos, incluyendo los 2 nuevos y sin regresión en los tests ya
existentes de `expire_stale_trials_task` (trial normal).

- [ ] **Step 10: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/email_sender.py core/content_pipeline/templates/content_pipeline/email_month_expired.html core/content_pipeline/tests/test_tasks.py core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "feat(monthly-payment): expire_stale_trials_task tambien cubre el mes pagado vencido"
```

---

### Task 7: Correo diario — descarga lo cancela, copy con fecha real

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Modify: `core/content_pipeline/email_sender.py`
- Modify: `core/content_pipeline/templates/content_pipeline/email_daily.html`
- Modify: `core/content_pipeline/tests/test_tasks.py`
- Modify: `core/content_pipeline/tests/test_email_sender.py`

**Interfaces:**
- Consumes: `ContentPost.downloaded_at` (ya existe, `core/content_pipeline/models.py:57`).
- Produces: nada nuevo para otras tareas.

- [ ] **Step 1: Escribir el test de `send_daily_email_task` que falla**

En `core/content_pipeline/tests/test_tasks.py`, agregar después de
`test_send_daily_email_task_weekly_feedback_idempotent` (esa función y las 2
anteriores de `WeeklyFeedback` ya deberían haberse quitado en la Task 2 de este plan —
confirmar que ya no existen antes de seguir):

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_task_skips_when_already_downloaded(calendar_with_dna):
    post = _make_post(calendar_with_dna, 3, downloaded_at=timezone.now())
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        from core.content_pipeline.tasks import send_daily_email_task
        send_daily_email_task(str(post.id))
    mock_send.assert_not_called()
    post.refresh_from_db()
    assert post.status == ContentPost.STATUS_PENDING


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_task_sends_when_not_downloaded(calendar_with_dna):
    post = _make_post(calendar_with_dna, 3)
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        from core.content_pipeline.tasks import send_daily_email_task
        send_daily_email_task(str(post.id))
    mock_send.assert_called_once()
    post.refresh_from_db()
    assert post.status == ContentPost.STATUS_SENT
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py::test_send_daily_email_task_skips_when_already_downloaded -v`
Expected: FAIL — hoy sí manda el correo aunque `downloaded_at` esté seteado.

- [ ] **Step 3: Implementar el guard de descarga y quitar la creación de `WeeklyFeedback`**

En `core/content_pipeline/tasks.py`, reemplazar `send_daily_email_task` completa por:

```python
def send_daily_email_task(post_id: str) -> None:
    post = ContentPost.objects.select_related('calendar__brand_dna__job').get(id=post_id)
    if post.calendar.brand_dna.job.deleted_at is not None:
        logger.info(f"Post {post_id} omitido — calendario eliminado por el usuario")
        return
    if post.downloaded_at is not None:
        logger.info(f"Post {post_id} ya descargado — se omite el correo diario")
        return
    # Fallback defensivo: las imágenes ya se generan todas en content_generation_task,
    # esto solo cubre el caso raro de que una generación individual haya fallado.
    if not post.image_url:
        _generate_missing_image(post)
    EmailSender().send_daily(post=post)
```

Quitar el import `WeeklyFeedback` de la cabecera del archivo si ya no se usa en
ningún otro lugar de `tasks.py` (`grep -n "WeeklyFeedback" core/content_pipeline/tasks.py`
después de este cambio — debería quedar en 0 ocurrencias).

- [ ] **Step 4: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: PASS.

- [ ] **Step 5: Escribir el test de copy con fecha que falla**

En `core/content_pipeline/tests/test_email_sender.py`, reemplazar
`test_send_daily_email_weekend_cta_on_day_7` y
`test_send_daily_email_no_weekend_cta_on_other_days` (ya no aplican — ese banner de
"esta fue tu última pieza de la semana" se quita) por:

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_uses_real_date_not_day_number(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    post = posts[0]
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_daily(post=post)
    subject = mock_send.call_args[0][0]
    plain = mock_send.call_args[0][1]
    assert 'Día 1' not in subject
    assert str(post.scheduled_at.day) in subject
    assert 'No se te olvide publicar' in plain
```

- [ ] **Step 6: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py::test_send_daily_email_uses_real_date_not_day_number -v`
Expected: FAIL — el asunto actual sigue diciendo "Día 1".

- [ ] **Step 7: Implementar la fecha en español sin depender de i18n**

En `core/content_pipeline/email_sender.py`, agregar cerca del inicio del archivo
(después de los imports, antes de `class EmailSender`):

```python
_MESES_ES = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
]


def _fecha_es(dt) -> str:
    return f"{dt.day} de {_MESES_ES[dt.month - 1]}"
```

Reemplazar el método `send_daily` completo por:

```python
    def send_daily(self, post: ContentPost) -> None:
        calendar_review_url = settings.COSMIC_BASE_URL + reverse(
            'calendar_review', args=[post.calendar.brand_dna.job.id]
        )
        fecha = _fecha_es(post.scheduled_at)
        html = render_to_string('content_pipeline/email_daily.html', {
            'post': post,
            'calendar_review_url': calendar_review_url,
            'fecha': fecha,
        })
        business_name = (post.calendar.brand_dna.business_name or '').strip()
        email = post.calendar.brand_dna.job.email
        subject = f'🔔 No se te olvide publicar hoy ({fecha}) — {business_name}' if business_name else f'🔔 No se te olvide publicar hoy ({fecha}) — Agente Cosmic'
        send_mail(
            subject,
            f'No se te olvide publicar el día de hoy ({fecha}).',
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=html,
            fail_silently=False,
        )
        post.status = ContentPost.STATUS_SENT
        post.sent_at = timezone.now()
        post.save(update_fields=['status', 'sent_at'])
        EMAILS_SENT.labels(type='daily_post').inc()
        logger.info(f"Email dia {post.day_number} enviado a {email}")
```

- [ ] **Step 8: Reescribir el template**

Reemplazar `core/content_pipeline/templates/content_pipeline/email_daily.html`
completo:

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>No se te olvide publicar — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
  <p style="margin: 0 0 16px; font-size: 0.72rem; color: #999; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">📬 Recordatorio automático</p>

  <h1 style="color: #1a1a2e;">No se te olvide publicar el día de hoy ({{ fecha }})</h1>
  <p>Tu post para <strong>{{ post.calendar.brand_dna.business_name }}</strong> ya está listo — solo falta que lo publiques.</p>

  <div style="text-align: center; margin: 28px 0;">
    <a href="{{ calendar_review_url }}"
       style="display: inline-block; padding: 14px 32px; background: #e94560; color: #fff;
              border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 1rem;">
      Ver mi post de hoy →
    </a>
  </div>

  <hr>
  <p style="font-size: 12px; color: #999;">Agente Cosmic — Powered by Google Cloud</p>
</body>
</html>
```

- [ ] **Step 9: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_email_sender.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/email_sender.py core/content_pipeline/templates/content_pipeline/email_daily.html core/content_pipeline/tests/test_tasks.py core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "feat(monthly-payment): descarga cancela el correo diario, copy con fecha real"
```

---

### Task 8: Copy — sin "suscripción", CTA temprano en dashboard, "mes" en vez de "semana"

**Files:**
- Modify: `core/brand_dna/auth_views.py` (`dashboard_view`)
- Modify: `core/brand_dna/templates/brand_dna/dashboard.html`
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html`
- Modify: `core/content_pipeline/templates/content_pipeline/email_trial_expired.html`
- Modify: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: `Subscription.status` (Task 1), `payment_needed`/`early_cta`/`payment_url`
  ya calculados en `calendar_review_view` (Task 5) — se replica el mismo cálculo en
  `dashboard_view`, que es una vista distinta.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/brand_dna/tests/test_views.py`:

```python
def test_dashboard_shows_early_cta_when_trialing(client, user, settings):
    settings.STRIPE_PAYMENT_LINK_URL = 'https://buy.stripe.com/test123'
    user.tenant.subscription.status = 'trialing'
    user.tenant.subscription.save(update_fields=['status'])
    client.force_login(user)
    response = client.get('/dashboard/')
    assert b'mes completo' in response.content
    assert f'https://buy.stripe.com/test123?client_reference_id={user.tenant_id}'.encode() in response.content


def test_dashboard_hides_early_cta_when_active(client, user):
    client.force_login(user)
    response = client.get('/dashboard/')
    assert b'mes completo' not in response.content


def test_dashboard_manage_payment_method_button_renamed(client, user):
    user.tenant.subscription.stripe_customer_id = 'cus_test1'
    user.tenant.subscription.save(update_fields=['stripe_customer_id'])
    client.force_login(user)
    response = client.get('/dashboard/')
    assert 'Administrar mi método de pago'.encode() in response.content
    assert b'Administrar mi suscripci\xc3\xb3n' not in response.content
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py::test_dashboard_shows_early_cta_when_trialing -v`
Expected: FAIL — el dashboard hoy no tiene ningún CTA temprano.

- [ ] **Step 3: Implementar en `dashboard_view`**

En `core/brand_dna/auth_views.py`, dentro de `dashboard_view`, agregar antes del
`return render(...)` (justo después del bloque `plan = get_user_plan(request.user)`):

```python
    subscription = getattr(getattr(request.user, 'tenant', None), 'subscription', None)
    early_cta = bool(subscription and subscription.status == 'trialing')
    payment_url = ''
    if early_cta:
        payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={request.user.tenant_id}"
```

Y agregar `'early_cta': early_cta, 'payment_url': payment_url,` al diccionario de
contexto del `render(...)` existente.

- [ ] **Step 4: Actualizar `dashboard.html`**

Insertar este bloque justo antes de `{% if user.tenant.subscription.stripe_customer_id %}`
(línea 146):

```html
  {% if early_cta %}
  <div style="max-width:700px;margin:24px auto;background:#1a1a2e;border:1px solid #4a9eff;border-radius:12px;padding:20px;text-align:center;">
    <h2 style="font-size:1.1rem;margin-bottom:4px;">⚡ ¿Quieres el mes completo desde hoy?</h2>
    <p style="color:#aaa;font-size:0.85rem;margin-bottom:16px;">Paga ahora y genera un mes completo de contenido de inmediato — ahorra horas de trabajo cada semana.</p>
    <a href="{{ payment_url }}" style="display:inline-block;padding:14px 32px;background:#4a9eff;color:#fff;border-radius:8px;font-weight:700;text-decoration:none;">Genera tu mes completo →</a>
  </div>
  {% endif %}
```

Cambiar el texto del botón del Customer Portal (línea ~151, dentro del bloque
`{% if user.tenant.subscription.stripe_customer_id %}`):

```html
        Administrar mi suscripción
```

por:

```html
        Administrar mi método de pago
```

Cambiar el banner de "próxima semana se está generando" (línea 87-89):

```html
      ☕ <strong style="color:#a8e6c8;">Tu próxima semana se está generando.</strong> No hace falta que esperes aquí — te avisamos por correo en cuanto esté lista.
```

por:

```html
      ☕ <strong style="color:#a8e6c8;">Tu próximo mes se está generando.</strong> No hace falta que esperes aquí — te avisamos por correo en cuanto esté listo.
```

Y el badge de la tarjeta de job (línea 128):

```html
        <span class="status-badge status-processing" style="margin-top:6px;">🔄 Generando próxima semana</span>
```

por:

```html
        <span class="status-badge status-processing" style="margin-top:6px;">🔄 Generando próximo mes</span>
```

- [ ] **Step 5: Actualizar `calendar_review.html`**

Cambiar el banner de "próxima semana se está generando" (líneas 158-161, el que
depende de `calendar.next_week_generating`):

```html
    ☕ <strong style="color:#a8e6c8;">Tu próxima semana se está generando.</strong> No hace falta que esperes aquí — te avisamos por correo en cuanto esté lista.
```

por:

```html
    ☕ <strong style="color:#a8e6c8;">Tu próximo mes se está generando.</strong> No hace falta que esperes aquí — te avisamos por correo en cuanto esté listo.
```

- [ ] **Step 6: Actualizar `email_trial_expired.html`**

Cambiar:

```html
  <p>Para seguir recibiendo contenido nuevo cada semana, activa tu suscripción:</p>
```

por:

```html
  <p>Genera tu próximo mes de contenido:</p>
```

Cambiar el texto del botón:

```html
      Activar mi suscripción →
```

por:

```html
      Generar mi próximo mes →
```

Cambiar:

```html
  <p style="color: #555;">Tu contenido ya generado sigue disponible sin restricción — esto
  solo afecta la generación de tu próxima semana.</p>
```

por:

```html
  <p style="color: #555;">Tu contenido ya generado sigue disponible sin restricción — esto
  solo afecta la generación de tu próximo mes.</p>
```

- [ ] **Step 7: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -v`
Expected: PASS — todos, incluyendo los 3 nuevos.

- [ ] **Step 8: Commit**

```bash
git add core/brand_dna/auth_views.py core/brand_dna/templates/brand_dna/dashboard.html core/brand_dna/templates/brand_dna/calendar_review.html core/content_pipeline/templates/content_pipeline/email_trial_expired.html core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(monthly-payment): CTA temprano + copy sin suscripcion en dashboard/calendar_review"
```

---

### Task 9: Quitar el zoom de imagen (solo imágenes, no video)

**Files:**
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html`
- Modify: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: nada.
- Produces: nada nuevo para otras tareas.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/brand_dna/tests/test_views.py`:

```python
def test_calendar_review_single_image_has_no_zoom_link(client, user, job_with_calendar):
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert b'title="Ver imagen completa"' not in response.content


def test_calendar_review_video_keeps_controls(client, user, job_with_calendar):
    post = job_with_calendar.brand_dna.calendar.posts.get(day_number=1)
    post.format = 'reel'
    post.video_url = 'https://storage.googleapis.com/agente-cosmic-assets/reel.mp4'
    post.save(update_fields=['format', 'video_url'])
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert b'<video controls' in response.content
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py::test_calendar_review_single_image_has_no_zoom_link -v`
Expected: FAIL — hoy sí existe ese `title="Ver imagen completa"`.

- [ ] **Step 3: Implementar**

En `core/brand_dna/templates/brand_dna/calendar_review.html`, 3 cambios:

**(a)** Imagen individual (líneas 195-198), reemplazar:

```html
        {% elif post.image_url %}
          <a href="{{ post.image_url }}" target="_blank" rel="noopener" title="Ver imagen completa" style="display:block;width:100%;height:100%;">
            <img src="{{ post.image_url }}" alt="Día {{ post.day_number }}" loading="lazy" style="cursor:zoom-in;">
          </a>
```

por:

```html
        {% elif post.image_url %}
          <img src="{{ post.image_url }}" alt="Día {{ post.day_number }}" loading="lazy">
```

**(b)** Slides de carrusel (líneas 188-193), reemplazar:

```html
        {% elif post.format == 'carousel' and post.image_urls %}
          {% for slide_url in post.image_urls %}
          <a href="{{ slide_url }}" target="_blank" rel="noopener" title="Slide {{ forloop.counter }}">
            <img src="{{ slide_url }}" alt="Slide {{ forloop.counter }} del día {{ post.day_number }}" loading="lazy" style="cursor:zoom-in;">
          </a>
          {% endfor %}
```

por:

```html
        {% elif post.format == 'carousel' and post.image_urls %}
          {% for slide_url in post.image_urls %}
          <img src="{{ slide_url }}" alt="Slide {{ forloop.counter }} del día {{ post.day_number }}" loading="lazy">
          {% endfor %}
```

**(c)** Versión regenerada por JS (líneas 384-397), reemplazar el bloque completo:

```javascript
        if (data.image_urls && data.image_urls.length) {
          imgContainer.classList.add('carousel-grid');
          imgContainer.innerHTML = data.image_urls.map(function(url, i) {
            return '<a href="' + url + '" target="_blank" rel="noopener" title="Slide ' + (i + 1) + '">'
              + '<img src="' + url + '?t=' + t + '" alt="Slide ' + (i + 1) + '" loading="lazy" style="cursor:zoom-in;"></a>';
          }).join('') + '<div class="carousel-badge">🎠 Carrusel</div>';
        } else {
          imgContainer.classList.remove('carousel-grid');
          const imgEl = imgContainer.querySelector('img');
          const linkEl = imgContainer.querySelector('a');
          const freshUrl = data.image_url + '?t=' + t;
          if (imgEl) imgEl.src = freshUrl;
          if (linkEl) linkEl.href = data.image_url;
        }
```

por:

```javascript
        if (data.image_urls && data.image_urls.length) {
          imgContainer.classList.add('carousel-grid');
          imgContainer.innerHTML = data.image_urls.map(function(url, i) {
            return '<img src="' + url + '?t=' + t + '" alt="Slide ' + (i + 1) + '" loading="lazy">';
          }).join('') + '<div class="carousel-badge">🎠 Carrusel</div>';
        } else {
          imgContainer.classList.remove('carousel-grid');
          const imgEl = imgContainer.querySelector('img');
          const freshUrl = data.image_url + '?t=' + t;
          if (imgEl) imgEl.src = freshUrl;
        }
```

El `<video controls>` (línea 184) no se toca.

- [ ] **Step 4: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -v`
Expected: PASS — todos, incluyendo los 2 nuevos.

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/templates/brand_dna/calendar_review.html core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(monthly-payment): quita el zoom de imagen, obliga a descargar"
```

---

### Verificación final

- [ ] Correr la suite completa:

Run: `docker compose exec backend pytest core/ -v`
Expected: 0 failures.

- [ ] Confirmar que ninguna referencia a `WeeklyFeedback`, `calendar_feedback_api`, o
`generate_next_week` sobrevive en el código real (no en git history):

Run: `grep -rn "WeeklyFeedback\|calendar_feedback_api\|generate_next_week" --include="*.py" --include="*.html" core/ | grep -v __pycache__ | grep -v migrations/`
Expected: sin resultados (o solo comentarios explicativos, si alguno quedó a
propósito — revisar caso por caso).

- [ ] Nota para Anuar, fuera del alcance de este plan: reconfigurar el Payment Link en
el Dashboard de Stripe a modo pago puntual antes de que este comportamiento sea real
en producción — mientras siga en modo `subscription`, Stripe seguirá cobrando
automáticamente cada mes por su cuenta, sin pasar por `generate_next_month` en el
segundo cobro en adelante (el webhook `checkout.session.completed` solo dispara en el
primer pago de una suscripción de Stripe, los renovaciones mandan
`invoice.payment_succeeded`, que en este plan se deja sin usar a propósito).
