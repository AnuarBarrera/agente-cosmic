# Motor de Payment Link por plan + migración de testers al plan Fundador — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que cada `Plan` tenga su propio Payment Link de Stripe (en vez del único link global fijo de hoy), y usar ese motor para migrar a todos los usuarios Tester al nuevo plan Fundador, conservando solo su calendario más reciente.

**Architecture:** Un campo nuevo en `Plan` (`stripe_payment_link_url`) + un helper centralizado (`get_payment_url(user)`) que reemplaza la construcción manual de la URL en los 4 lugares que hoy la arman con `settings.STRIPE_PAYMENT_LINK_URL`. Sobre esa base, un management command idempotente y con dry-run por default hace la migración real de datos.

**Tech Stack:** Django 5.2, pytest (corre dentro del contenedor docker: `docker compose exec -T backend python -m pytest <path> -q`), management commands de Django (`django.core.management.base.BaseCommand`).

**Spec:** `docs/superpowers/specs/2026-08-18-founder-plan-payment-link-engine-design.md`

## Global Constraints

- Convención de commits de este repo: `GIT_EDITOR=true git commit -m "msg"` (nunca heredoc). `git add` de archivos exactos, nunca `-A`/`-a`.
- Sin rama de feature — commits van directo a `main`, local. NO hacer `git push` salvo que Anuar lo pida explícitamente.
- El campo nuevo `Plan.stripe_payment_link_url` vacío (`''`, el default) debe preservar el comportamiento de hoy exactamente — caer al link global `settings.STRIPE_PAYMENT_LINK_URL`. Ningún plan existente (`User`, `Tester`, `Admin`) se toca en las Tareas 1-3.
- El management command de la Tarea 4 es **destructivo** (borra `AnalysisJob`s completos vía cascada) — por diseño, el modo por default es dry-run (solo imprime), y requiere el flag explícito `--apply` para ejecutar de verdad.
- Tests: `docker compose exec -T backend python -m pytest <path> -q`. Los contenedores ya están arriba (`docker compose ps` para confirmar).

---

### Task 1: Campo `Plan.stripe_payment_link_url`

**Files:**
- Modify: `core/tenant_management/models.py:30-56` (clase `Plan`)
- Create: `core/tenant_management/migrations/0027_plan_stripe_payment_link_url.py`
- Test: `core/tenant_management/tests/test_models.py`

**Interfaces:**
- Produces: `Plan.stripe_payment_link_url` (`CharField`, `blank=True`, `default=''`) — usado por la Tarea 2 (`get_payment_url`) y la Tarea 4 (management command).

- [ ] **Step 1: Escribe el test que falla**

Agrega al final de `core/tenant_management/tests/test_models.py` (después de `test_subscription_stripe_fields_default_empty`, mismo estilo que los tests de esa clase):

```python
def test_plan_stripe_payment_link_url_defaults_empty():
    plan = Plan.objects.create(name='Plan Test Payment Link')
    assert plan.stripe_payment_link_url == ''


def test_plan_stripe_payment_link_url_accepts_value():
    plan = Plan.objects.create(
        name='Plan Test Payment Link 2',
        stripe_payment_link_url='https://buy.stripe.com/founder123',
    )
    plan.refresh_from_db()
    assert plan.stripe_payment_link_url == 'https://buy.stripe.com/founder123'
```

- [ ] **Step 2: Corre el test para confirmar que falla**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_models.py -k stripe_payment_link_url -v`
Expected: FAIL con `TypeError: 'stripe_payment_link_url' is an invalid keyword argument for this function` (el campo no existe todavía).

- [ ] **Step 3: Agrega el campo al modelo**

En `core/tenant_management/models.py`, dentro de la clase `Plan`, inserta la línea nueva justo después de `allows_sample_generation` y antes de `price` (línea 50-51 actual):

```python
    # Permite generar 1 sola pieza de muestra (imagen o reel) desde el
    # formulario de analisis, en vez del calendario completo de 7 dias —
    # pensado para prospeccion. Activado hoy solo en el Plan Admin.
    allows_sample_generation = models.BooleanField(default=False)
    # Payment Link de Stripe propio de este plan -- vacio (default) cae al
    # link global settings.STRIPE_PAYMENT_LINK_URL, retrocompatible. Ver
    # core/brand_dna/rate_limits.py:get_payment_url.
    stripe_payment_link_url = models.CharField(max_length=255, blank=True, default='')
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
```

- [ ] **Step 4: Genera la migración**

Run: `docker compose exec -T backend python manage.py makemigrations tenant_management`
Expected: crea `core/tenant_management/migrations/0027_plan_stripe_payment_link_url.py`. Confirma que su contenido coincide con esto (ajusta solo la fecha del comentario si Django la genera distinta):

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_management', '0026_plan_max_product_reference_photos'),
    ]

    operations = [
        migrations.AddField(
            model_name='plan',
            name='stripe_payment_link_url',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
```

- [ ] **Step 5: Aplica la migración**

Run: `docker compose exec -T backend python manage.py migrate tenant_management`
Expected: `Applying tenant_management.0027_plan_stripe_payment_link_url... OK`

- [ ] **Step 6: Corre el test para confirmar que pasa**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_models.py -k stripe_payment_link_url -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Corre la suite completa de tenant_management para confirmar que nada se rompió**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/ -q`
Expected: todos los tests pasan (0 failures)

- [ ] **Step 8: Commit**

```bash
git add core/tenant_management/models.py core/tenant_management/migrations/0027_plan_stripe_payment_link_url.py core/tenant_management/tests/test_models.py
GIT_EDITOR=true git commit -m "feat(tenant_management): agrega Plan.stripe_payment_link_url"
```

---

### Task 2: `get_payment_url(user)` en `rate_limits.py`

**Files:**
- Modify: `core/brand_dna/rate_limits.py` (agregar función nueva junto a `get_user_plan`)
- Test: `core/brand_dna/tests/test_rate_limits.py` (crear si no existe — confirma con `ls core/brand_dna/tests/test_rate_limits.py` antes de asumir; si ya existe, agrega los tests ahí siguiendo su estilo)

**Interfaces:**
- Consumes: `Plan.stripe_payment_link_url` (Task 1), `get_user_plan(user)` (ya existe en el mismo archivo, línea 6-23).
- Produces: `get_payment_url(user) -> str` — usado por la Tarea 3 en los 4 call sites.

- [ ] **Step 1: Confirma si ya existe un archivo de tests para rate_limits.py**

Run: `ls core/brand_dna/tests/ | grep rate_limit`

Si el archivo `test_rate_limits.py` existe, ábrelo y seguí su estilo de imports/fixtures. Si no existe, créalo con este contenido base (imports mínimos, sin fixtures compartidas — cada test crea sus propios objetos, igual que `test_models.py` de tenant_management):

```python
import pytest
from django.test import override_settings
from django.contrib.auth import get_user_model
from core.tenant_management.models import TenantModel, Subscription, Plan

pytestmark = pytest.mark.django_db

UserModel = get_user_model()


def _user_with_plan(plan):
    user = UserModel.objects.create_user(
        username=f'{plan.name}@test.com', email=f'{plan.name}@test.com', password='pass1234',
    )
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=plan)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    return user
```

- [ ] **Step 2: Escribe los tests que fallan**

Agrega al mismo archivo:

```python
@override_settings(STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/global123')
def test_get_payment_url_falls_back_to_global_link_when_plan_has_none():
    from core.brand_dna.rate_limits import get_payment_url
    plan = Plan.objects.create(name='Plan Sin Link')
    user = _user_with_plan(plan)
    url = get_payment_url(user)
    assert url == f'https://buy.stripe.com/global123?client_reference_id={user.tenant_id}'


@override_settings(STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/global123')
def test_get_payment_url_uses_plan_specific_link_when_set():
    from core.brand_dna.rate_limits import get_payment_url
    plan = Plan.objects.create(
        name='Plan Con Link', stripe_payment_link_url='https://buy.stripe.com/founder123',
    )
    user = _user_with_plan(plan)
    url = get_payment_url(user)
    assert url == f'https://buy.stripe.com/founder123?client_reference_id={user.tenant_id}'
```

- [ ] **Step 3: Corre los tests para confirmar que fallan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_rate_limits.py -k get_payment_url -v`
Expected: FAIL con `ImportError: cannot import name 'get_payment_url'`

- [ ] **Step 4: Implementa `get_payment_url`**

En `core/brand_dna/rate_limits.py`, agrega esta función justo después de `get_user_plan` (línea 23, antes de `can_create_calendar`):

```python
def get_payment_url(user) -> str:
    """Payment Link de Stripe del plan actual del usuario, con
    client_reference_id ya adjunto. plan.stripe_payment_link_url vacio
    (default) cae al link global settings.STRIPE_PAYMENT_LINK_URL --
    retrocompatible, ningun plan existente necesita configurarse."""
    from django.conf import settings
    plan = get_user_plan(user)
    base_url = plan.stripe_payment_link_url or settings.STRIPE_PAYMENT_LINK_URL
    return f"{base_url}?client_reference_id={user.tenant_id}"
```

- [ ] **Step 5: Corre los tests para confirmar que pasan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_rate_limits.py -k get_payment_url -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add core/brand_dna/rate_limits.py core/brand_dna/tests/test_rate_limits.py
GIT_EDITOR=true git commit -m "feat(brand_dna): agrega get_payment_url, motor de link de pago por plan"
```

---

### Task 3: Reemplaza los 4 call sites por `get_payment_url`

**Files:**
- Modify: `core/brand_dna/views.py:376-378` (`calendar_review_view`)
- Modify: `core/brand_dna/auth_views.py` (`dashboard_view`, líneas con `payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}..."`)
- Modify: `core/content_pipeline/email_sender.py:121-122` (`send_trial_expired`) y `:166-167` (`send_month_expired`)
- Test: `core/brand_dna/tests/test_views.py`, `core/content_pipeline/tests/test_email_sender.py`

**Interfaces:**
- Consumes: `get_payment_url(user)` (Task 2).

- [ ] **Step 1: Escribe el test que falla para `calendar_review_view`**

En `core/brand_dna/tests/test_views.py`, agrega este test junto a `test_calendar_review_shows_payment_banner_when_trial_expired` (usa el mismo fixture `job_with_calendar`, `free_plan`):

```python
def test_calendar_review_uses_plan_specific_payment_link(client, user, job_with_calendar, free_plan, settings):
    settings.STRIPE_PAYMENT_LINK_URL = 'https://buy.stripe.com/global123'
    free_plan.stripe_payment_link_url = 'https://buy.stripe.com/founder123'
    free_plan.save(update_fields=['stripe_payment_link_url'])
    user.tenant.subscription.status = 'trial_expired'
    user.tenant.subscription.save(update_fields=['status'])
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert response.context['payment_url'] == f'https://buy.stripe.com/founder123?client_reference_id={user.tenant_id}'
```

- [ ] **Step 2: Corre el test para confirmar que falla**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -k uses_plan_specific_payment_link -v`
Expected: FAIL (`payment_url` sigue usando `settings.STRIPE_PAYMENT_LINK_URL` directo, ignora `free_plan.stripe_payment_link_url`)

- [ ] **Step 3: Reemplaza el call site de `calendar_review_view`**

En `core/brand_dna/views.py`, dentro de `calendar_review_view`, reemplaza:

```python
    payment_url = ''
    if payment_needed or early_cta:
        payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={job.user.tenant_id}"
```

por:

```python
    from core.brand_dna.rate_limits import get_payment_url
    payment_url = ''
    if payment_needed or early_cta:
        payment_url = get_payment_url(job.user)
```

- [ ] **Step 4: Corre el test para confirmar que pasa**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -k uses_plan_specific_payment_link -v`
Expected: PASS

- [ ] **Step 5: Repite el ciclo para `dashboard_view`**

Test nuevo en `core/brand_dna/tests/test_views.py`, junto a `test_dashboard_shows_early_cta_when_trialing`:

```python
def test_dashboard_uses_plan_specific_payment_link(client, user, free_plan, settings):
    settings.STRIPE_PAYMENT_LINK_URL = 'https://buy.stripe.com/global123'
    free_plan.stripe_payment_link_url = 'https://buy.stripe.com/founder123'
    free_plan.save(update_fields=['stripe_payment_link_url'])
    user.tenant.subscription.status = 'trialing'
    user.tenant.subscription.save(update_fields=['status'])
    client.force_login(user)
    response = client.get('/dashboard/')
    assert f'https://buy.stripe.com/founder123?client_reference_id={user.tenant_id}'.encode() in response.content
```

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -k dashboard_uses_plan_specific_payment_link -v`
Expected: FAIL

En `core/brand_dna/auth_views.py`, dentro de `dashboard_view`, reemplaza:

```python
    payment_url = ''
    if early_cta:
        payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={request.user.tenant_id}"
```

por:

```python
    from core.brand_dna.rate_limits import get_payment_url
    payment_url = ''
    if early_cta:
        payment_url = get_payment_url(request.user)
```

(`get_user_plan` ya se importa en esta vista con el mismo patrón `from core.brand_dna.rate_limits import get_user_plan` — agrega el import de `get_payment_url` junto a ese, no dupliques el `import` si ya existe una línea `from core.brand_dna.rate_limits import ...` cerca; en ese caso solo agrega `get_payment_url` a esa misma línea de import.)

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -k dashboard_uses_plan_specific_payment_link -v`
Expected: PASS

- [ ] **Step 6: Repite el ciclo para `send_trial_expired` y `send_month_expired`**

En `core/content_pipeline/tests/test_email_sender.py`, agrega junto a `test_send_trial_expired_email_calls_django_send`:

```python
@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/global123')
def test_send_trial_expired_uses_plan_specific_payment_link(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    from core.tenant_management.models import TenantModel, Subscription, Plan
    job, dna, calendar, posts = full_setup
    plan = Plan.objects.create(
        name='Plan Founder Email Test', max_calendars_per_week=2, max_post_regenerations=2,
        max_post_edits=2, price=0, stripe_payment_link_url='https://buy.stripe.com/founder123',
    )
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

    html = mock_send.call_args[1]['html_message']
    assert f'https://buy.stripe.com/founder123?client_reference_id={tenant.id}' in html
```

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_email_sender.py -k send_trial_expired_uses_plan_specific -v`
Expected: FAIL

En `core/content_pipeline/email_sender.py`, agrega el import al principio del archivo (junto a los demás imports de `core.brand_dna`):

```python
from core.brand_dna.rate_limits import get_payment_url
```

Reemplaza en `send_trial_expired` (línea 122):

```python
        payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={job.user.tenant_id}"
```

por:

```python
        payment_url = get_payment_url(job.user)
```

Y en `send_month_expired` (línea 167), el mismo reemplazo:

```python
        payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={job.user.tenant_id}"
```

por:

```python
        payment_url = get_payment_url(job.user)
```

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_email_sender.py -k send_trial_expired_uses_plan_specific -v`
Expected: PASS

Escribe el mismo test para `send_month_expired` (copia el test anterior, cambia `sender.send_trial_expired(job=job, brand_dna=dna)` por `sender.send_month_expired(job=job, brand_dna=dna)` y el nombre del test a `test_send_month_expired_uses_plan_specific_payment_link`), corre y confirma que pasa:

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_email_sender.py -k month_expired_uses_plan_specific -v`
Expected: PASS

- [ ] **Step 7: Corre la suite completa de los 3 módulos tocados**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py core/content_pipeline/tests/test_email_sender.py core/brand_dna/tests/test_rate_limits.py -q`
Expected: todos pasan, 0 failures (confirma en particular que los tests viejos de `payment_url` con el link global siguen pasando sin cambios — el fallback debe seguir funcionando igual).

- [ ] **Step 8: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/auth_views.py core/content_pipeline/email_sender.py core/brand_dna/tests/test_views.py core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "refactor(brand_dna): usa get_payment_url en los 4 lugares que armaban el link de Stripe a mano"
```

---

### Task 4: Management command `migrate_testers_to_founder`

**Files:**
- Create: `core/tenant_management/management/commands/migrate_testers_to_founder.py`
- Test: `core/tenant_management/tests/test_migrate_testers_to_founder_command.py`

**Interfaces:**
- Consumes: `Plan.stripe_payment_link_url` (Task 1). No depende de `get_payment_url` (Task 2) directamente — opera sobre el modelo, no arma URLs.

- [ ] **Step 1: Escribe los tests que fallan**

Crea `core/tenant_management/tests/test_migrate_testers_to_founder_command.py`:

```python
import pytest
from io import StringIO
from datetime import timedelta
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.utils import timezone
from core.tenant_management.models import TenantModel, Subscription, Plan
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db

UserModel = get_user_model()


def _plan(name, **overrides):
    defaults = dict(
        max_calendars_per_week=2, max_post_regenerations=2, max_post_edits=2,
        max_photo_prechecks_per_day=10, max_product_reference_photos=7,
        allows_sample_generation=False, price=0,
    )
    defaults.update(overrides)
    return Plan.objects.create(name=name, **defaults)


def _tester_with_jobs(email, job_count, plan):
    user = UserModel.objects.create_user(username=email, email=email, password='pass1234')
    tenant = TenantModel.objects.create(name=email, status='active')
    sub = Subscription.objects.create(tenant=tenant, plan=plan, status='active')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    jobs = []
    for i in range(job_count):
        job = AnalysisJob.objects.create(
            email=email, business_url='https://tuwebmx.com', user=user,
            status=AnalysisJob.STATUS_DONE, stage=AnalysisJob.STAGE_COMPLETE, progress=100,
        )
        # created_at tiene auto_now_add=True -- se fuerza el orden con update()
        # para no depender de sleeps entre creaciones.
        AnalysisJob.objects.filter(id=job.id).update(created_at=timezone.now() - timedelta(days=job_count - i))
        job.refresh_from_db()
        jobs.append(job)
    return user, sub, jobs


def test_dry_run_creates_no_founder_plan_and_changes_nothing():
    tester_plan = _plan('Tester')
    user, sub, jobs = _tester_with_jobs('t1@test.com', 2, tester_plan)

    out = StringIO()
    call_command(
        'migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123',
        stdout=out,
    )

    assert not Plan.objects.filter(name='Fundador').exists()
    sub.refresh_from_db()
    assert sub.plan == tester_plan
    assert sub.status == 'active'
    assert AnalysisJob.objects.filter(user=user).count() == 2
    assert '[dry-run]' in out.getvalue()


def test_apply_creates_founder_plan_copying_user_plan_limits():
    _plan('User', max_calendars_per_week=4, max_post_regenerations=5, max_post_edits=6,
          max_photo_prechecks_per_day=20, max_product_reference_photos=14,
          allows_sample_generation=True)
    tester_plan = _plan('Tester')
    _tester_with_jobs('t2@test.com', 1, tester_plan)

    out = StringIO()
    call_command(
        'migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123',
        '--apply', stdout=out,
    )

    founder = Plan.objects.get(name='Fundador')
    assert founder.max_calendars_per_week == 4
    assert founder.max_post_regenerations == 5
    assert founder.max_post_edits == 6
    assert founder.max_photo_prechecks_per_day == 20
    assert founder.max_product_reference_photos == 14
    assert founder.allows_sample_generation is True
    assert founder.stripe_payment_link_url == 'https://buy.stripe.com/founder123'


def test_apply_second_run_does_not_duplicate_founder_plan():
    _plan('User')
    tester_plan = _plan('Tester')
    _tester_with_jobs('t3@test.com', 1, tester_plan)

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/v1', '--apply')
    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/v2', '--apply')

    assert Plan.objects.filter(name='Fundador').count() == 1
    assert Plan.objects.get(name='Fundador').stripe_payment_link_url == 'https://buy.stripe.com/v2'


def test_apply_prunes_all_but_most_recent_calendar():
    _plan('User')
    tester_plan = _plan('Tester')
    user, sub, jobs = _tester_with_jobs('t4@test.com', 3, tester_plan)
    most_recent = jobs[-1]

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123', '--apply')

    remaining = list(AnalysisJob.objects.filter(user=user))
    assert len(remaining) == 1
    assert remaining[0].id == most_recent.id


def test_apply_changes_plan_and_forces_trial_expired_status():
    _plan('User')
    tester_plan = _plan('Tester')
    user, sub, jobs = _tester_with_jobs('t5@test.com', 1, tester_plan)

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123', '--apply')

    sub.refresh_from_db()
    assert sub.plan.name == 'Fundador'
    assert sub.status == 'trial_expired'


def test_apply_ignores_non_tester_subscriptions():
    _plan('User')
    user_plan = Plan.objects.get(name='User')
    other_user, other_sub, _ = _tester_with_jobs('other@test.com', 1, user_plan)

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123', '--apply')

    other_sub.refresh_from_db()
    assert other_sub.plan.name == 'User'
    assert other_sub.status == 'active'


def test_apply_prunes_via_cascade_deleting_brand_dna_of_pruned_jobs():
    _plan('User')
    tester_plan = _plan('Tester')
    user, sub, jobs = _tester_with_jobs('t6@test.com', 2, tester_plan)
    older_job = jobs[0]
    BrandDNA.objects.create(
        job=older_job, business_name='Negocio viejo', business_url='https://tuwebmx.com',
        description='desc', keywords=['k'], audience='a', tone='profesional', primary_colors=['#000'],
    )

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123', '--apply')

    assert not BrandDNA.objects.filter(job=older_job).exists()
```

- [ ] **Step 2: Corre los tests para confirmar que fallan**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_migrate_testers_to_founder_command.py -v`
Expected: FAIL con `CommandError: Unknown command: 'migrate_testers_to_founder'` (el comando no existe todavía)

- [ ] **Step 3: Implementa el comando**

Crea `core/tenant_management/management/commands/migrate_testers_to_founder.py`:

```python
import logging
from django.core.management.base import BaseCommand
from core.tenant_management.models import Plan, Subscription
from core.brand_dna.models import AnalysisJob

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Migra todas las Subscription con plan=Tester al plan Fundador (creado o '
        'actualizado con los limites del plan User + el Payment Link recibido), '
        'podando cada tenant a solo su AnalysisJob mas reciente y forzando '
        'status=trial_expired para que el boton de pago aparezca de inmediato. '
        'Dry-run por default -- requiere --apply para ejecutar de verdad.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--payment-link-url', required=True,
            help='Payment Link de Stripe del plan Fundador.',
        )
        parser.add_argument(
            '--apply', action='store_true',
            help='Ejecuta los cambios de verdad. Sin este flag, solo se imprime que se haria.',
        )

    def handle(self, *args, **options):
        payment_link_url = options['payment_link_url']
        apply_changes = options['apply']

        subscriptions = (
            Subscription.objects
            .filter(plan__name='Tester')
            .select_related('tenant', 'plan')
        )
        if not subscriptions.exists():
            self.stdout.write('No hay suscripciones con plan Tester. Nada que hacer.')
            return

        founder_plan = None
        if apply_changes:
            user_plan = Plan.objects.get(name='User')
            founder_plan, created = Plan.objects.get_or_create(
                name='Fundador',
                defaults=dict(
                    max_daily_interactions=user_plan.max_daily_interactions,
                    max_monthly_interactions=user_plan.max_monthly_interactions,
                    max_calendars_per_week=user_plan.max_calendars_per_week,
                    max_post_regenerations=user_plan.max_post_regenerations,
                    max_post_edits=user_plan.max_post_edits,
                    max_photo_prechecks_per_day=user_plan.max_photo_prechecks_per_day,
                    max_product_reference_photos=user_plan.max_product_reference_photos,
                    allows_sample_generation=user_plan.allows_sample_generation,
                    price=user_plan.price,
                    stripe_payment_link_url=payment_link_url,
                ),
            )
            if not created:
                founder_plan.stripe_payment_link_url = payment_link_url
                founder_plan.save(update_fields=['stripe_payment_link_url'])
        else:
            self.stdout.write(
                f"[dry-run] Se crearia/actualizaria el plan 'Fundador' con "
                f"stripe_payment_link_url={payment_link_url!r}, copiando limites del plan 'User'."
            )

        for sub in subscriptions:
            tenant_jobs = list(
                AnalysisJob.objects.filter(user__tenant=sub.tenant).order_by('-created_at')
            )
            to_keep = tenant_jobs[0] if tenant_jobs else None
            to_prune = tenant_jobs[1:]

            if not apply_changes:
                self.stdout.write(
                    f"[dry-run] Tenant {sub.tenant.name}: plan Tester -> Fundador, "
                    f"status {sub.status!r} -> 'trial_expired', "
                    f"{len(to_prune)} calendario(s) a eliminar "
                    f"(se conserva {to_keep.id if to_keep else 'ninguno'})."
                )
                for job in to_prune:
                    self.stdout.write(f"    - borraria AnalysisJob {job.id} (creado {job.created_at})")
                continue

            for job in to_prune:
                job.delete()

            sub.plan = founder_plan
            sub.status = 'trial_expired'
            sub.save(update_fields=['plan', 'status'])
            logger.info(f"Tenant {sub.tenant.name} migrado a Fundador, {len(to_prune)} calendario(s) podado(s)")

        verb = 'Migrados' if apply_changes else '[dry-run] Se migrarian'
        self.stdout.write(self.style.SUCCESS(f'{verb} {subscriptions.count()} tester(s) al plan Fundador.'))
```

- [ ] **Step 4: Corre los tests para confirmar que pasan**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_migrate_testers_to_founder_command.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Corre la suite completa del repo**

Run: `docker compose exec -T backend python -m pytest -q`
Expected: todos los tests del repo pasan, 0 failures (confirma que no rompiste nada en `tenant_management`, `brand_dna`, `content_pipeline` con los 4 tasks combinados).

- [ ] **Step 6: Commit**

```bash
git add core/tenant_management/management/commands/migrate_testers_to_founder.py core/tenant_management/tests/test_migrate_testers_to_founder_command.py
GIT_EDITOR=true git commit -m "feat(tenant_management): comando migrate_testers_to_founder (dry-run por default)"
```

---

## Después de implementar (no es parte de este plan, para que Anuar lo sepa)

- El comando NO se corre automáticamente contra los testers reales de este entorno de desarrollo ni contra producción — eso es una decisión explícita de Anuar, con el Payment Link real de Stripe del plan Fundador como argumento (`--payment-link-url`). Correrlo aquí primero en modo dry-run (sin `--apply`) para ver la lista de testers/calendarios que afectaría, antes de decidir aplicarlo.
- Aplicarlo en producción requiere el mismo comando vía la sesión `CosmicProd`, después de validarlo aquí.
