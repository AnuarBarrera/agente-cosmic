# Rediseño del flujo de onboarding — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reducir la distancia entre registrarse y ver contenido propio, y
cerrar el consumo de IA sin control que hoy permite el botón de regenerar.

**Architecture:** Un helper único decide el aterrizaje tras cualquier
autenticación, en lugar de que cada vista redirija a `dashboard` por su
cuenta. La regeneración gratuita de calendario se elimina por completo y su
botón pasa a ser un CTA de pago que reusa el modal de fotos ya existente,
extraído a un partial compartido. El borrado de calendario se cierra con un
campo de plan, y la primera descarga se registra para disparar la venta.

**Tech Stack:** Django 5.2, PostgreSQL, RQ, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-24-onboarding-flow-redesign-design.md`

## Global Constraints

- Commits: `GIT_EDITOR=true git commit -m "msg"` — **nunca** heredoc, se cuelga.
- `git add` de archivos exactos. **Nunca** `git add -A` ni `-a`.
- Rama `main` directo, local. **No** hacer push a origin en ningún paso.
- Tests: `docker compose exec -T backend python -m pytest <path> -q`
- Tras cambiar código o templates:
  `docker compose up -d --force-recreate --no-deps backend rqworker`
  (`DEBUG=False` cachea templates; un restart simple no basta).
- El árbol tiene ruido preexistente sin trackear (`nginx.dev.conf`,
  `cambiosUI.md`, `consolidado.md`, WIP de hyperframes, varios `.md`
  borrados). **No tocarlo ni incluirlo en ningún commit.**
- Copy en español con acentos correctos. Los comentarios de código del repo
  van sin acentos (convención existente); respétala.
- El helper de aterrizaje devuelve **una URL ya resuelta** (string), no un
  nombre de ruta.

---

### Task 1: Helper de aterrizaje y auto-login al verificar el correo

**Files:**
- Modify: `core/brand_dna/auth_views.py` (nuevo helper; líneas 128, 246, 255, 280, 300, 479)
- Test: `core/brand_dna/tests/test_auth_views.py`

**Interfaces:**
- Consumes: nada de tareas anteriores.
- Produces: `_post_auth_destination(user) -> str` en
  `core/brand_dna/auth_views.py`. Devuelve una URL absoluta de path
  (ej. `/dashboard/`, `/nuevo-analisis/`, `/calendar/<uuid>/`).

- [ ] **Step 1: Escribir los tests que fallan**

Añadir al final de `core/brand_dna/tests/test_auth_views.py`:

```python
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar
from core.brand_dna.auth_views import _post_auth_destination


def _make_user(email='dest@example.com'):
    user = User.objects.create_user(email=email, password=_TEST_PWD, username=email)
    _make_tenant(user)
    return user


@pytest.mark.django_db
def test_destination_sin_calendarios_va_al_formulario():
    user = _make_user()
    assert _post_auth_destination(user) == '/nuevo-analisis/'


@pytest.mark.django_db
def test_destination_con_job_procesando_va_al_dashboard():
    user = _make_user('proc@example.com')
    AnalysisJob.objects.create(
        user=user, email=user.email, business_description='Taqueria',
        status=AnalysisJob.STATUS_PROCESSING,
    )
    assert _post_auth_destination(user) == '/dashboard/'


@pytest.mark.django_db
def test_destination_con_un_calendario_listo_va_a_ese_calendario():
    user = _make_user('uno@example.com')
    job = AnalysisJob.objects.create(
        user=user, email=user.email, business_description='Taqueria',
        status=AnalysisJob.STATUS_DONE,
    )
    dna = BrandDNA.objects.create(job=job, business_name='Taqueria')
    ContentCalendar.objects.create(brand_dna=dna)
    assert _post_auth_destination(user) == f'/calendar/{job.id}/'


@pytest.mark.django_db
def test_destination_con_dos_calendarios_va_al_dashboard():
    user = _make_user('dos@example.com')
    for i in range(2):
        job = AnalysisJob.objects.create(
            user=user, email=user.email, business_description=f'Negocio {i}',
            status=AnalysisJob.STATUS_DONE,
        )
        dna = BrandDNA.objects.create(job=job, business_name=f'Negocio {i}')
        ContentCalendar.objects.create(brand_dna=dna)
    assert _post_auth_destination(user) == '/dashboard/'


@pytest.mark.django_db
def test_destination_ignora_calendarios_borrados():
    from django.utils import timezone
    user = _make_user('borrado@example.com')
    job = AnalysisJob.objects.create(
        user=user, email=user.email, business_description='Borrado',
        status=AnalysisJob.STATUS_DONE, deleted_at=timezone.now(),
    )
    dna = BrandDNA.objects.create(job=job, business_name='Borrado')
    ContentCalendar.objects.create(brand_dna=dna)
    assert _post_auth_destination(user) == '/nuevo-analisis/'


@pytest.mark.django_db
def test_verificar_correo_deja_al_usuario_logueado(client, setup_plans_and_groups):
    from django.contrib.auth.hashers import make_password
    token = EmailVerificationToken.objects.create(
        email='nuevo@example.com',
        tenant_name='',
        user_data={'password': make_password(_TEST_PWD), 'invitation_code': ''},
    )
    resp = client.get(f'/auth/verify/{token.token}/')
    # Aterriza en el formulario, no en login, y con sesion iniciada
    assert resp.status_code == 302
    assert resp.url == '/nuevo-analisis/'
    assert '_auth_user_id' in client.session


@pytest.mark.django_db
def test_login_respeta_next_explicito(client, setup_plans_and_groups):
    user = _make_user('next@example.com')
    resp = client.post(
        '/auth/login/?next=/dashboard/',
        {'email': user.email, 'password': _TEST_PWD},
    )
    assert resp.status_code == 302
    assert resp.url == '/dashboard/'
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py -q -k destination`

Expected: FAIL con `ImportError: cannot import name '_post_auth_destination'`.

- [ ] **Step 3: Implementar el helper**

En `core/brand_dna/auth_views.py`, después de las constantes de rate limit
de login (cerca de la línea 250), añadir:

```python
def _post_auth_destination(user) -> str:
    """URL a la que mandar al usuario despues de autenticarse.

    Reemplaza el 'redirect(dashboard)' que cada punto de entrada decidia por
    su cuenta. Devuelve URL ya resuelta porque el caso del calendario
    necesita argumentos y los otros dos no.
    """
    from django.urls import reverse
    from core.brand_dna.models import AnalysisJob

    jobs = AnalysisJob.objects.filter(user=user, deleted_at__isnull=True)

    if jobs.filter(status__in=(AnalysisJob.STATUS_PENDING,
                               AnalysisJob.STATUS_PROCESSING)).exists():
        return reverse('dashboard')

    ready = list(
        jobs.filter(brand_dna__calendar__isnull=False).values_list('id', flat=True)[:2]
    )
    if len(ready) == 1:
        return reverse('calendar_review', args=[ready[0]])

    if ready:
        return reverse('dashboard')

    return reverse('new_analysis')
```

- [ ] **Step 4: Correr los tests del helper**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py -q -k destination`

Expected: PASS (5 tests).

- [ ] **Step 5: Aplicar el helper en los puntos de entrada**

En `core/brand_dna/auth_views.py`, sustituir:

`register_view` (línea ~128), `login_view` (línea ~255) y
`forgot_password_view` (línea ~300), en su guarda de "ya autenticado":

```python
    if request.user.is_authenticated:
        return redirect(_post_auth_destination(request.user))
```

`verify_email_view` (línea ~246), el `return redirect('login')` final:

```python
    notify_admin_new_user(user, invitation_code=invitation_code_str or None)

    # Auto-login: el token es de un solo uso, expira en 24h y ya tiene poder
    # para crear la cuenta -- loguearla no amplia la superficie, y ahorra al
    # usuario escribir su contrasena por segunda vez en el mismo minuto.
    login(request, user)
    return redirect(_post_auth_destination(user))
```

`login_view` (línea ~280), donde hoy hace `request.GET.get('next', 'dashboard')`:

```python
                    next_url = request.GET.get('next')
                    return redirect(next_url or _post_auth_destination(user))
```

`google_callback_view` (línea ~479), el `return redirect('dashboard')` final:

```python
    login(request, user)
    return redirect(_post_auth_destination(user))
```

- [ ] **Step 6: Correr toda la suite de auth**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py -q`

Expected: PASS, incluidos `test_verificar_correo_deja_al_usuario_logueado`
y `test_login_respeta_next_explicito`.

- [ ] **Step 7: Recrear contenedores y commitear**

```bash
docker compose up -d --force-recreate --no-deps backend rqworker
git add core/brand_dna/auth_views.py core/brand_dna/tests/test_auth_views.py
GIT_EDITOR=true git commit -m "feat(brand_dna): un solo helper decide el aterrizaje y verificar el correo ya deja logueado"
```

---

### Task 2: Cerrar el borrado de calendario por plan

**Files:**
- Modify: `core/tenant_management/models.py` (clase `Plan`, tras `allows_sample_generation`)
- Create: migración de esquema + datos en `core/tenant_management/migrations/`
- Modify: `core/brand_dna/views.py:456` (`delete_calendar_api`)
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html:136`
- Test: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: nada de la Task 1.
- Produces: `Plan.allows_calendar_deletion` (BooleanField, default `False`).
  La vista `calendar_review_view` pasa `can_delete_calendar` (bool) al
  contexto del template.

- [ ] **Step 1: Escribir el test que falla**

Añadir a `core/brand_dna/tests/test_views.py`:

```python
@pytest.mark.django_db
def test_borrar_calendario_prohibido_en_plan_sin_permiso(client):
    from core.tenant_management.models import Plan
    user = User.objects.create_user(
        email='nodelete@example.com', password=_TEST_PWD, username='nodelete@example.com')
    tenant = TenantModel.objects.create(name=user.email, status='active')
    plan, _ = Plan.objects.get_or_create(
        name='User', defaults={'max_calendars_per_week': 2})
    plan.allows_calendar_deletion = False
    plan.save(update_fields=['allows_calendar_deletion'])
    Subscription.objects.create(tenant=tenant, plan=plan)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job = AnalysisJob.objects.create(
        user=user, email=user.email, business_description='Taqueria',
        status=AnalysisJob.STATUS_DONE)
    client.force_login(user)

    resp = client.post(f'/api/calendar/{job.id}/delete/')

    assert resp.status_code == 403
    job.refresh_from_db()
    assert job.deleted_at is None


@pytest.mark.django_db
def test_borrar_calendario_permitido_en_plan_interno(client):
    from core.tenant_management.models import Plan
    user = User.objects.create_user(
        email='tester@example.com', password=_TEST_PWD, username='tester@example.com')
    tenant = TenantModel.objects.create(name=user.email, status='active')
    plan, _ = Plan.objects.get_or_create(
        name='Tester', defaults={'max_calendars_per_week': 5})
    plan.allows_calendar_deletion = True
    plan.save(update_fields=['allows_calendar_deletion'])
    Subscription.objects.create(tenant=tenant, plan=plan)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job = AnalysisJob.objects.create(
        user=user, email=user.email, business_description='Taqueria',
        status=AnalysisJob.STATUS_DONE)
    client.force_login(user)

    resp = client.post(f'/api/calendar/{job.id}/delete/')

    assert resp.status_code == 200
    job.refresh_from_db()
    assert job.deleted_at is not None
```

Si `test_views.py` no tiene ya los imports de `User`, `TenantModel`,
`Subscription`, `AnalysisJob` y `_TEST_PWD`, añadirlos siguiendo el mismo
patrón que `core/brand_dna/tests/test_auth_views.py` (cabecera del archivo,
`_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"`).

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -q -k borrar_calendario`

Expected: FAIL — hoy responde 200 en ambos casos y el campo no existe.

- [ ] **Step 3: Añadir el campo al modelo**

En `core/tenant_management/models.py`, justo después de
`allows_sample_generation`:

```python
    # Permite al usuario borrar su calendario desde la UI. Default False:
    # sin borrado, cada calendario ocupa cupo de forma permanente, que es el
    # control de costo de IA buscado -- un plan nuevo no hereda el permiso
    # por accidente. Solo Tester y Admin lo tienen encendido.
    allows_calendar_deletion = models.BooleanField(default=False)
```

- [ ] **Step 4: Crear la migración de esquema y datos**

```bash
docker compose exec -T backend python manage.py makemigrations tenant_management -n plan_allows_calendar_deletion
```

Editar la migración generada para añadir, después de la `AddField`, la
operación de datos:

```python
def encender_en_planes_internos(apps, schema_editor):
    Plan = apps.get_model('tenant_management', 'Plan')
    Plan.objects.filter(name__in=['Tester', 'Admin']).update(
        allows_calendar_deletion=True)


def apagar_en_planes_internos(apps, schema_editor):
    Plan = apps.get_model('tenant_management', 'Plan')
    Plan.objects.filter(name__in=['Tester', 'Admin']).update(
        allows_calendar_deletion=False)
```

y en `operations`, tras la `AddField`:

```python
        migrations.RunPython(encender_en_planes_internos, apagar_en_planes_internos),
```

- [ ] **Step 5: Aplicar la migración**

Run: `docker compose exec -T backend python manage.py migrate tenant_management`

Expected: la migración aplica sin error.

- [ ] **Step 6: Gate en el backend**

En `core/brand_dna/views.py`, al inicio de `delete_calendar_api` (línea
~456), antes de tocar `deleted_at`:

```python
def delete_calendar_api(request, job_id):
    from django.utils import timezone
    import django_rq
    from core.brand_dna.rate_limits import get_user_plan
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)

    # El boton se oculta en el template, pero este endpoint es llamable
    # directo -- la regla vive aqui y el template solo la refleja.
    if not get_user_plan(request.user).allows_calendar_deletion:
        return JsonResponse(
            {'error': 'Tu plan no permite eliminar calendarios. Escríbenos y lo vemos contigo.'},
            status=403,
        )

    job.deleted_at = timezone.now()
```

- [ ] **Step 7: Ocultar el botón en el template**

En `core/brand_dna/views.py`, dentro de `calendar_review_view`, junto a
`can_create, _ = can_create_calendar(request.user)` (línea ~386), añadir:

```python
    can_delete_calendar = plan.allows_calendar_deletion
```

y pasarlo en el `render(...)` de esa vista como
`'can_delete_calendar': can_delete_calendar,`.

En `core/brand_dna/templates/brand_dna/calendar_review.html:136`, envolver
el botón:

```html
    {% if can_delete_calendar %}<button onclick="deleteCalendar('{{ job.id }}')" class="nav-btn nav-btn-danger">🗑 Eliminar</button>{% endif %}
```

- [ ] **Step 8: Correr los tests**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -q`

Expected: PASS, incluidos los dos tests nuevos.

- [ ] **Step 9: Recrear contenedores y commitear**

```bash
docker compose up -d --force-recreate --no-deps backend rqworker
git add core/tenant_management/models.py core/tenant_management/migrations/ core/brand_dna/views.py core/brand_dna/templates/brand_dna/calendar_review.html core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(tenant_management,brand_dna): eliminar calendario solo en planes internos"
```

---

### Task 3: Registrar la primera descarga

**Files:**
- Modify: `core/content_pipeline/models.py` (clase `ContentCalendar`, tras `last_reactivation_email_at`)
- Create: migración en `core/content_pipeline/migrations/`
- Modify: `core/brand_dna/views.py:485` (`download_post_image`)
- Test: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: nada de tareas anteriores.
- Produces: `ContentCalendar.first_download_at` (`DateTimeField`,
  `null=True`, `blank=True`). Lo consume la Task 4 para condicionar el
  banner.

- [ ] **Step 1: Escribir el test que falla**

Añadir a `core/brand_dna/tests/test_views.py`:

```python
@pytest.mark.django_db
def test_primera_descarga_se_estampa_una_sola_vez(client):
    from unittest.mock import patch, MagicMock
    from core.brand_dna.models import BrandDNA
    from core.content_pipeline.models import ContentCalendar, ContentPost
    user = User.objects.create_user(
        email='dl@example.com', password=_TEST_PWD, username='dl@example.com')
    job = AnalysisJob.objects.create(
        user=user, email=user.email, business_description='Taqueria',
        status=AnalysisJob.STATUS_DONE)
    dna = BrandDNA.objects.create(job=job, business_name='Taqueria')
    calendar = ContentCalendar.objects.create(brand_dna=dna)
    post = ContentPost.objects.create(
        calendar=calendar, day_number=1, caption='Hola',
        image_url='https://storage.googleapis.com/bucket/img.png')
    client.force_login(user)

    fake = MagicMock(content=b'PNG')
    fake.raise_for_status = MagicMock()
    with patch('requests.get', return_value=fake):
        client.get(f'/api/post/{post.id}/download/')
    calendar.refresh_from_db()
    primera = calendar.first_download_at
    assert primera is not None

    with patch('requests.get', return_value=fake):
        client.get(f'/api/post/{post.id}/download/')
    calendar.refresh_from_db()
    assert calendar.first_download_at == primera
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -q -k primera_descarga`

Expected: FAIL con `AttributeError: 'ContentCalendar' object has no attribute 'first_download_at'`.

- [ ] **Step 3: Añadir el campo**

En `core/content_pipeline/models.py`, dentro de `ContentCalendar`, después
de `last_reactivation_email_at`:

```python
    # Momento en que el usuario se llevo su primer contenido. Es la senal de
    # valor entregado: dispara el banner de venta anticipada, que antes salia
    # antes de que el usuario tocara nada.
    first_download_at = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 4: Crear y aplicar la migración**

```bash
docker compose exec -T backend python manage.py makemigrations content_pipeline -n calendar_first_download_at
docker compose exec -T backend python manage.py migrate content_pipeline
```

- [ ] **Step 5: Estampar en la descarga**

En `core/brand_dna/views.py`, dentro de `download_post_image`, justo
después del `if not post.image_url: raise Http404` (línea ~500) y antes de
las tres ramas de formato, para que cuente cualquier tipo de descarga:

```python
    # Primera vez que este usuario se lleva algo de su calendario. Se estampa
    # antes de servir el archivo: si la descarga falla despues, el usuario ya
    # demostro intencion, y un banner de venta de mas es mejor que uno que
    # nunca aparece.
    calendar = post.calendar
    if calendar.first_download_at is None:
        from django.utils import timezone as _tz
        calendar.first_download_at = _tz.now()
        calendar.save(update_fields=['first_download_at'])
```

- [ ] **Step 6: Correr los tests**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -q`

Expected: PASS.

- [ ] **Step 7: Recrear contenedores y commitear**

```bash
docker compose up -d --force-recreate --no-deps backend rqworker
git add core/content_pipeline/models.py core/content_pipeline/migrations/ core/brand_dna/views.py core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(content_pipeline): registrar la primera descarga del calendario"
```

---

### Task 4: Mover el banner de venta y corregir el estado vacío

**Files:**
- Modify: `core/brand_dna/views.py` (`calendar_review_view`, cálculo de `early_cta`)
- Modify: `core/brand_dna/auth_views.py` (`dashboard_view`, cálculo de `early_cta`)
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html:183-193`
- Modify: `core/brand_dna/templates/brand_dna/dashboard.html:146` y `152-159`
- Test: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: `ContentCalendar.first_download_at` de la Task 3.
- Produces: nada que consuman tareas posteriores.

- [ ] **Step 1: Escribir el test que falla**

Añadir a `core/brand_dna/tests/test_views.py`:

```python
@pytest.mark.django_db
def test_banner_de_venta_no_aparece_antes_de_la_primera_descarga(client):
    from django.utils import timezone
    from core.brand_dna.models import BrandDNA
    from core.content_pipeline.models import ContentCalendar
    from core.tenant_management.models import Plan
    user = User.objects.create_user(
        email='cta@example.com', password=_TEST_PWD, username='cta@example.com')
    tenant = TenantModel.objects.create(name=user.email, status='active')
    plan, _ = Plan.objects.get_or_create(
        name='User', defaults={'max_calendars_per_week': 2})
    Subscription.objects.create(tenant=tenant, plan=plan, status='trialing')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job = AnalysisJob.objects.create(
        user=user, email=user.email, business_description='Taqueria',
        status=AnalysisJob.STATUS_DONE)
    dna = BrandDNA.objects.create(job=job, business_name='Taqueria')
    calendar = ContentCalendar.objects.create(brand_dna=dna)
    client.force_login(user)

    resp = client.get(f'/calendar/{job.id}/')
    assert resp.context['early_cta'] is False

    calendar.first_download_at = timezone.now()
    calendar.save(update_fields=['first_download_at'])

    resp = client.get(f'/calendar/{job.id}/')
    assert resp.context['early_cta'] is True
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -q -k banner_de_venta`

Expected: FAIL — hoy `early_cta` ya es `True` en la primera aserción.

- [ ] **Step 3: Condicionar `early_cta` en el calendario**

En `core/brand_dna/views.py`, en `calendar_review_view` (línea ~372),
sustituir el cálculo de `early_cta` por:

```python
    # La venta anticipada solo aparece cuando el usuario ya se llevo algo:
    # ofrecer el mes completo antes de que toque su contenido gratis se lee
    # como cobro por adelantado, no como oportunidad.
    early_cta = bool(
        subscription and not payment_needed and subscription.status == 'trialing'
        and job.status == AnalysisJob.STATUS_DONE
        and calendar is not None and calendar.first_download_at is not None
    )
```

- [ ] **Step 4: Condicionar `early_cta` en el dashboard**

En `core/brand_dna/auth_views.py`, en `dashboard_view` (línea ~515),
sustituir:

```python
    # Misma regla que en el calendario: la oferta no puede aparecer en una
    # pantalla mientras en la otra todavia no corresponde.
    ya_descargo = any(
        getattr(getattr(getattr(j, 'brand_dna', None), 'calendar', None),
                'first_download_at', None) is not None
        for j in jobs
    )
    early_cta = bool(
        subscription and subscription.status == 'trialing'
        and not has_processing and ya_descargo
    )
```

- [ ] **Step 5: Mover el banner debajo de los posts**

En `core/brand_dna/templates/brand_dna/calendar_review.html`, cortar el
bloque completo `{% elif early_cta %} … {% endif %}` (líneas ~183-193) de
su posición actual. El bloque de `payment_needed` se queda donde está y
cierra con su propio `{% endif %}`:

```html
  {% if payment_needed %}
  <div id="payment-banner" ...>
    ...
  </div>
  {% endif %}
```

Y el bloque de `early_cta` se pega **después** del último `{% endfor %}` de
`month_groups`, con su propia condición completa:

```html
  {% if early_cta %}
  <div id="early-cta-banner" style="background:#1a1a2e;border:1px solid #4a9eff;border-radius:12px;padding:20px;margin-top:32px;">
    <h2 style="font-size:1.1rem;margin-bottom:4px;">⚡ ¿Quieres el mes completo desde hoy?</h2>
    <p style="color:#aaa;font-size:0.85rem;margin-bottom:16px;">{% if plan_price %}Por <strong style="color:#f0f0f0;">${{ plan_price }} MXN</strong> genera un mes completo de inmediato — ahorra horas de trabajo cada semana.{% else %}Paga ahora y genera un mes completo de contenido de inmediato — ahorra horas de trabajo cada semana.{% endif %}</p>
    {% if photos_remaining > 0 %}
    <button type="button" onclick="openPhotoModal('{{ payment_url }}')" style="display:block;text-align:center;width:100%;padding:14px;background:#4a9eff;color:#fff;border:none;border-radius:8px;font-weight:700;font-size:1rem;cursor:pointer;">Genera tu mes completo →</button>
    {% else %}
    <a href="{{ payment_url }}" style="display:block;text-align:center;width:100%;padding:14px;background:#4a9eff;color:#fff;border-radius:8px;font-weight:700;font-size:1rem;text-decoration:none;">Genera tu mes completo →</a>
    {% endif %}
  </div>
  {% endif %}
```

Nota: `margin-bottom:24px` pasa a `margin-top:32px` porque ahora cierra la
página en vez de abrirla.

- [ ] **Step 6: Corregir el copy del estado vacío**

En `core/brand_dna/templates/brand_dna/dashboard.html:146`:

```html
        <p>Cuéntanos de tu negocio y en unos minutos tienes tu primera semana de contenido lista.</p>
```

El botón `Generar mi contenido` de la línea siguiente no cambia.

- [ ] **Step 7: Correr los tests**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/ -q`

Expected: PASS.

- [ ] **Step 8: Recrear contenedores y commitear**

```bash
docker compose up -d --force-recreate --no-deps backend rqworker
git add core/brand_dna/views.py core/brand_dna/auth_views.py core/brand_dna/templates/brand_dna/calendar_review.html core/brand_dna/templates/brand_dna/dashboard.html core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(brand_dna): la venta anticipada aparece tras la primera descarga, no antes"
```

---

### Task 5: Extraer el modal de pago a un partial compartido

**Files:**
- Create: `core/brand_dna/templates/brand_dna/_payment_photo_modal.html`
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html` (quitar HTML del modal en ~217-234 y su JS en ~468-674)
- Modify: `core/brand_dna/views.py` (nuevo helper `_payment_context`)
- Test: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: `first_download_at` (Task 3) vía `early_cta`.
- Produces:
  - Partial `brand_dna/_payment_photo_modal.html`, que requiere en contexto:
    `job` (con `.id`), `payment_url` (str), `photos_remaining` (int) y
    `csrf_token`. Expone al documento las funciones JS `openPhotoModal(url)`,
    `showPhotoUploadStep()`, `goToPayment()`, `uploadPhotosAndContinue()`,
    `updateModalContinueButtonState()` y `modalCompressImage(file)`.
  - `_payment_context(job, user) -> dict` en `core/brand_dna/views.py`, con
    las claves `payment_needed`, `early_cta`, `payment_url`, `plan_price`,
    `photos_remaining`.

- [ ] **Step 1: Escribir el test que falla**

Añadir a `core/brand_dna/tests/test_views.py`:

```python
@pytest.mark.django_db
def test_payment_context_devuelve_las_cinco_claves():
    from core.brand_dna.views import _payment_context
    from core.tenant_management.models import Plan
    user = User.objects.create_user(
        email='ctx@example.com', password=_TEST_PWD, username='ctx@example.com')
    tenant = TenantModel.objects.create(name=user.email, status='active')
    plan, _ = Plan.objects.get_or_create(
        name='User', defaults={'max_calendars_per_week': 2})
    Subscription.objects.create(tenant=tenant, plan=plan, status='trialing')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job = AnalysisJob.objects.create(
        user=user, email=user.email, business_description='Taqueria',
        status=AnalysisJob.STATUS_DONE)

    ctx = _payment_context(job, user)

    assert set(ctx) == {
        'payment_needed', 'early_cta', 'payment_url', 'plan_price',
        'photos_remaining',
    }
    assert ctx['photos_remaining'] == plan.max_product_reference_photos
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -q -k payment_context`

Expected: FAIL con `ImportError: cannot import name '_payment_context'`.

- [ ] **Step 3: Extraer el helper de contexto**

En `core/brand_dna/views.py`, antes de `calendar_review_view`, añadir:

```python
def _payment_context(job, user) -> dict:
    """Contexto del CTA de pago, compartido por el calendario y el ADN.

    Vivia inline en calendar_review_view; results lo necesita igual desde que
    el boton de regenerar se convirtio en CTA de pago.
    """
    from core.brand_dna.rate_limits import get_user_plan, get_payment_url
    plan = get_user_plan(user)
    subscription = getattr(getattr(job.user, 'tenant', None), 'subscription', None)
    calendar = getattr(getattr(job, 'brand_dna', None), 'calendar', None)

    payment_needed = bool(subscription and (
        subscription.status == 'trial_expired'
        or (subscription.paid_until and subscription.paid_until <= timezone.now())
    ))
    early_cta = bool(
        subscription and not payment_needed and subscription.status == 'trialing'
        and job.status == AnalysisJob.STATUS_DONE
        and calendar is not None and calendar.first_download_at is not None
    )
    payment_url = get_payment_url(job.user) if (payment_needed or early_cta) else ''
    return {
        'payment_needed': payment_needed,
        'early_cta': early_cta,
        'payment_url': payment_url,
        'plan_price': int(plan.price) if plan.price else 0,
        'photos_remaining': max(
            0, plan.max_product_reference_photos - len(job.product_reference_image_paths)),
    }
```

Y en `calendar_review_view`, sustituir el cálculo inline de
`payment_needed`, `early_cta`, `payment_url`, `plan_price` y
`photos_remaining` (líneas ~366-384) por:

```python
    pay_ctx = _payment_context(job, request.user)
```

pasando `**pay_ctx` en el `render(...)` de esa vista.

- [ ] **Step 4: Correr el test del helper**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -q -k payment_context`

Expected: PASS.

- [ ] **Step 5: Crear el partial**

Crear `core/brand_dna/templates/brand_dna/_payment_photo_modal.html` con el
HTML del modal (hoy en `calendar_review.html:217-234`) seguido de un
`<script>` que contenga, **movidas literalmente** desde
`calendar_review.html`:

- las variables `MODAL_PHOTOS_ROOM` (línea ~468), `modalSelectedPhotos`
  (~469) y `modalPaymentUrl` (~471);
- `compressImage` (~473), **renombrada a `modalCompressImage`** para que el
  partial no dependa de que el documento anfitrión la defina;
- `openPhotoModal`, `showPhotoUploadStep`, `updateModalContinueButtonState`,
  `goToPayment`, `renderModalPhotoCounter`, `renderModalPhotoThumbnails`,
  `setModalPrecheckStatus`, el listener de `modalPhotoInput` y
  `uploadPhotosAndContinue` (~497-674).

Dentro de `uploadPhotosAndContinue`, la llamada a `compressImage` pasa a
`modalCompressImage`. El partial define su propia constante de CSRF para no
depender del anfitrión:

```html
<script>
  var MODAL_CSRF = '{{ csrf_token }}';
```

y en `uploadPhotosAndContinue` el header pasa a
`headers: { 'X-CSRFToken': MODAL_CSRF }`.

- [ ] **Step 6: Incluir el partial en el calendario**

En `core/brand_dna/templates/brand_dna/calendar_review.html`, donde estaba
el HTML del modal:

```html
{% include "brand_dna/_payment_photo_modal.html" %}
```

y borrar del `<script>` de esa plantilla las funciones y variables que se
movieron al partial, incluida `compressImage` (que ya no usa nadie más en
ese archivo).

- [ ] **Step 7: Verificar que el calendario sigue funcionando**

Run: `docker compose up -d --force-recreate --no-deps backend rqworker`
Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/ -q`

Expected: PASS.

Verificación manual obligatoria: abrir un calendario de un usuario en
trial que ya descargó algo, y comprobar que el botón "Genera tu mes
completo" abre el modal, que el contador de fotos muestra el cupo, y que
"No, continuar al pago" navega a Stripe.

- [ ] **Step 8: Commitear**

```bash
git add core/brand_dna/templates/brand_dna/_payment_photo_modal.html core/brand_dna/templates/brand_dna/calendar_review.html core/brand_dna/views.py core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "refactor(brand_dna): el modal de pago con fotos sale a un partial reutilizable"
```

---

### Task 6: Eliminar la regeneración y convertir el botón del ADN en CTA de pago

**Files:**
- Modify: `core/brand_dna/views.py` (borrar `regenerate_calendar_api`, líneas ~918-978; añadir contexto de pago a `results`, línea ~298)
- Modify: `core/brand_dna/urls.py` (borrar la ruta `regenerate`)
- Modify: `core/brand_dna/templates/brand_dna/results.html` (banner ~338-341, JS ~506 y ~606-627)
- Modify: `core/shared/metrics.py` si la etiqueta vieja está enumerada ahí
- Test: `core/brand_dna/tests/test_brand_dna_edit.py` y `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: el partial `brand_dna/_payment_photo_modal.html` y
  `_payment_context(job, user)` de la Task 5.
- Produces: nada que consuman tareas posteriores.

- [ ] **Step 1: Escribir el test que falla**

Añadir a `core/brand_dna/tests/test_views.py`:

```python
@pytest.mark.django_db
def test_la_ruta_de_regenerar_calendario_ya_no_existe(client):
    from django.urls import NoReverseMatch, reverse
    with pytest.raises(NoReverseMatch):
        reverse('regenerate_calendar_api', args=['00000000-0000-0000-0000-000000000000'])
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -q -k regenerar_calendario`

Expected: FAIL — la ruta todavía resuelve.

- [ ] **Step 3: Borrar el endpoint y su ruta**

En `core/brand_dna/views.py`, borrar la función `regenerate_calendar_api`
completa (líneas ~918 hasta su `return`, incluido el decorador
`@login_required` / `@require_POST` que la precede).

En `core/brand_dna/urls.py`, borrar la línea:

```python
    path('api/calendar/<uuid:job_id>/regenerate/', views.regenerate_calendar_api, name='regenerate_calendar_api'),
```

Buscar y borrar los tests que ejercitan ese endpoint:

```bash
grep -rn "regenerate_calendar_api\|/regenerate/" core/brand_dna/tests/
```

- [ ] **Step 4: Dar contexto de pago a `results`**

En `core/brand_dna/views.py`, en la vista `results` (línea ~298),
sustituir el `render` por:

```python
    pay_ctx = _payment_context(job, request.user)
    return render(request, 'brand_dna/results.html', {
        'job': job,
        'brand_dna': brand_dna,
        'calendar': calendar,
        'can_create_calendar': can_create,
        'tone_choices': _ALLOWED_TONES,
        **pay_ctx,
    })
```

- [ ] **Step 5: Cambiar el banner del ADN**

En `core/brand_dna/templates/brand_dna/results.html`, sustituir el bloque
del banner de regeneración (líneas ~338-341) por:

```html
  <div class="regen-banner" id="regenBanner">
    <span id="regenBannerText">Hiciste cambios a tu ADN de marca.</span>
    {% if payment_needed or early_cta %}
      {% if photos_remaining > 0 %}
      <button id="regenBtn" type="button" onclick="openPhotoModal('{{ payment_url }}')">Genera tu contenido con estos cambios →</button>
      {% else %}
      <a id="regenBtn" href="{{ payment_url }}" style="display:inline-block;text-decoration:none;">Genera tu contenido con estos cambios →</a>
      {% endif %}
    {% endif %}
  </div>
  {% if calendar %}
  <p style="color:#888;font-size:0.85rem;text-align:center;margin-top:8px;">
    Tus cambios quedaron guardados. El contenido que ya tienes se generó con la información anterior.
  </p>
  {% endif %}
```

Añadir al final del `<body>` de esa plantilla, antes del `<script>`:

```html
{% include "brand_dna/_payment_photo_modal.html" %}
```

- [ ] **Step 6: Ajustar el JS del gate de aprobación**

En `core/brand_dna/templates/brand_dna/results.html`, `updateRegenButtonState`
(línea ~506) sigue existiendo pero cambia su texto y tolera que el botón no
esté renderizado:

```javascript
  // El CTA de pago solo se ofrece cuando TODOS los campos del ADN estan
  // aprobados -- misma regla que tenia el boton de regenerar. El boton puede
  // no existir (usuario con mes vigente, sin nada que comprar).
  function updateRegenButtonState() {
    const btn = document.getElementById('regenBtn');
    const hint = document.getElementById('regenBannerText');
    if (!btn) {
      if (hint) hint.textContent = 'Hiciste cambios a tu ADN de marca.';
      return;
    }
    if (allFieldsApproved()) {
      btn.disabled = false;
      btn.style.display = '';
      hint.textContent = 'Hiciste cambios a tu ADN de marca. Genera tu próximo mes para verlos aplicados.';
    } else {
      btn.disabled = true;
      btn.style.display = 'none';
      hint.textContent = 'Hiciste cambios a tu ADN de marca. Aprueba todos los campos de arriba para poder generar contenido con ellos.';
    }
  }
```

Borrar la función `regenerateCalendar()` completa (líneas ~606-627).

- [ ] **Step 7: Reemplazar la métrica**

Buscar la etiqueta vieja y sustituirla:

```bash
grep -rn "brand_dna_regenerated_calendar" core/
```

Donde el ADN abre el flujo de pago —en `results`, al construir el
contexto cuando `payment_needed or early_cta` es verdadero— incrementar:

```python
    if pay_ctx['payment_needed'] or pay_ctx['early_cta']:
        POST_ACTIONS.labels(action='brand_dna_payment_cta').inc()
```

- [ ] **Step 8: Correr la suite completa**

Run: `docker compose exec -T backend python -m pytest -q`

Expected: PASS. Si algún test falla por referirse a la regeneración
eliminada, borrarlo — el comportamiento ya no existe.

- [ ] **Step 9: Recrear contenedores y commitear**

```bash
docker compose up -d --force-recreate --no-deps backend rqworker
git add core/brand_dna/views.py core/brand_dna/urls.py core/brand_dna/templates/brand_dna/results.html core/brand_dna/tests/
GIT_EDITOR=true git commit -m "feat(brand_dna): sin regeneracion gratuita — el ADN editado lleva al pago del mes"
```

---

## Verificación final de rama

- [ ] `docker compose exec -T backend python -m pytest -q` — suite completa
      en verde.
- [ ] Recorrido manual del registro por correo: registrarse, abrir el enlace
      de verificación, confirmar que se aterriza en el formulario ya
      logueado, sin pasar por login.
- [ ] Recorrido manual del usuario que vuelve: con un calendario listo,
      iniciar sesión y confirmar que aterriza en su calendario.
- [ ] Confirmar en la UI que el botón de eliminar calendario no aparece en
      un usuario de plan User.
- [ ] Descargar un post y confirmar que el banner de venta anticipada
      aparece después, tanto en el calendario como en el dashboard.
