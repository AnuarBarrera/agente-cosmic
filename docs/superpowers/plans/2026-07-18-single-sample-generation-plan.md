# Generación de muestra individual (imagen o reel) — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que un usuario con el permiso adecuado (hoy solo el Plan Admin) genere, desde el mismo formulario `/analizar/`, una sola imagen o un solo reel de muestra en vez del calendario completo de 7 días — para prospección.

**Architecture:** Un permiso a nivel de `Plan` (`allows_sample_generation`) controla si el formulario muestra un selector de modo. `AnalysisJob` guarda el modo elegido (`generation_mode`). Al terminar el análisis de marca, `analyze_brand_task` bifurca: `'full'` mantiene el comportamiento actual (`content_generation_task`, calendario de 7 días); `'sample_image'`/`'sample_reel'` encola una tarea nueva y más corta (`generate_sample_task`) que genera un único post reutilizando `TextGenerator`/`_generate_post_media` ya existentes, sin programar correos.

**Tech Stack:** Django, RQ (django_rq), pytest.

## Global Constraints

- El selector de modo en el formulario `/analizar/` NUNCA debe aparecer para un usuario cuyo plan no tenga `allows_sample_generation=True`.
- El backend SIEMPRE revalida el permiso — un valor de `generation_mode` recibido de un usuario sin permiso se ignora y se fuerza a `'full'`.
- `generate_sample_task` NO debe llamar a `EmailSender`/`schedule_daily_emails` bajo ninguna circunstancia.
- No se toca `TextGenerator` (se reutiliza tal cual — día 1/índice 0 = reel vía `REEL_DAY`, el resto = single, salvo día 3/índice 2 = carousel vía `CAROUSEL_DAY`).
- No se modifica `calendar_review.html` — ya soporta un calendario con 1 solo post sin cambios.

---

## Task 1: Campos nuevos — `Plan.allows_sample_generation` y `AnalysisJob.generation_mode`

**Files:**
- Modify: `core/tenant_management/models.py`
- Modify: `core/brand_dna/models.py`
- Create: `core/tenant_management/migrations/0020_plan_allows_sample_generation.py` (generada por Django)
- Create: `core/brand_dna/migrations/0008_analysisjob_generation_mode.py` (generada por Django)

**Interfaces:**
- Produces: `Plan.allows_sample_generation: bool` (default `False`). `AnalysisJob.generation_mode: str`, choices `'full'`/`'sample_image'`/`'sample_reel'`, default `'full'`.

- [ ] **Step 1: Agregar el campo a `Plan`**

En `core/tenant_management/models.py`, dentro de la clase `Plan` (línea ~30), localiza:

```python
class Plan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    max_daily_interactions = models.PositiveIntegerField(default=100)
    max_monthly_interactions = models.PositiveIntegerField(default=1000)
    max_calendars_per_week = models.PositiveIntegerField(default=2)
    max_post_regenerations = models.PositiveIntegerField(default=2)
    max_post_edits = models.PositiveIntegerField(default=2)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Agrega el campo nuevo justo después de `max_post_edits`:

```python
class Plan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    max_daily_interactions = models.PositiveIntegerField(default=100)
    max_monthly_interactions = models.PositiveIntegerField(default=1000)
    max_calendars_per_week = models.PositiveIntegerField(default=2)
    max_post_regenerations = models.PositiveIntegerField(default=2)
    max_post_edits = models.PositiveIntegerField(default=2)
    # Permite generar 1 sola pieza de muestra (imagen o reel) desde el
    # formulario de analisis, en vez del calendario completo de 7 dias —
    # pensado para prospeccion. Activado hoy solo en el Plan Admin.
    allows_sample_generation = models.BooleanField(default=False)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

- [ ] **Step 2: Agregar el campo a `AnalysisJob`**

En `core/brand_dna/models.py`, dentro de la clase `AnalysisJob` (línea ~6), localiza el bloque de choices existente (`STAGE_CHOICES`, termina línea ~27) y el campo `profile_url` (línea ~40). Agrega el bloque de choices nuevo justo después de `STAGE_CHOICES`:

```python
    STAGE_CHOICES = [
        (STAGE_WEB, 'Analizando sitio web'),
        (STAGE_LOGO, 'Analizando logo'),
        (STAGE_POSTS, 'Analizando posts'),
        (STAGE_CONTENT, 'Generando contenido'),
        (STAGE_COMPLETE, 'Completo'),
    ]
    MODE_FULL = 'full'
    MODE_SAMPLE_IMAGE = 'sample_image'
    MODE_SAMPLE_REEL = 'sample_reel'
    MODE_CHOICES = [
        (MODE_FULL, 'Calendario completo'),
        (MODE_SAMPLE_IMAGE, 'Muestra: imagen'),
        (MODE_SAMPLE_REEL, 'Muestra: reel'),
    ]
```

Y agrega el campo `generation_mode` justo después de `profile_url`:

```python
    profile_url = models.URLField(blank=True, default='')
    generation_mode = models.CharField(max_length=20, choices=MODE_CHOICES, default=MODE_FULL)
```

- [ ] **Step 3: Generar y revisar las migraciones**

Run: `docker compose exec -T backend python manage.py makemigrations tenant_management brand_dna`
Expected: crea `core/tenant_management/migrations/0020_plan_allows_sample_generation.py` y `core/brand_dna/migrations/0008_analysisjob_generation_mode.py` (los números pueden variar si ya existe una migración más reciente — usa el número real que Django asigne).

Verifica el contenido de ambos archivos generados:
```bash
grep -n "allows_sample_generation" core/tenant_management/migrations/00*.py
grep -n "generation_mode" core/brand_dna/migrations/000*.py
```
Expected: ambos greps encuentran el campo nuevo en el archivo de migración recién creado.

- [ ] **Step 4: Aplicar las migraciones**

Run: `docker compose exec -T backend python manage.py migrate tenant_management brand_dna`
Expected: `Applying tenant_management.00XX_plan_allows_sample_generation... OK` y `Applying brand_dna.000X_analysisjob_generation_mode... OK`.

- [ ] **Step 5: Commit**

```bash
git add core/tenant_management/models.py core/brand_dna/models.py \
        core/tenant_management/migrations/ core/brand_dna/migrations/
git commit -m "feat(prospeccion): campos allows_sample_generation en Plan y generation_mode en AnalysisJob"
```

---

## Task 2: Formulario condicional + validación server-side en `analyze_submit`

**Files:**
- Modify: `core/brand_dna/views.py`
- Modify: `core/brand_dna/templates/brand_dna/new_analysis.html`
- Test: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: `Plan.allows_sample_generation` y `AnalysisJob.generation_mode`/`MODE_FULL`/`MODE_SAMPLE_IMAGE`/`MODE_SAMPLE_REEL` (Task 1). `get_user_plan(user)` de `core/brand_dna/rate_limits.py` (ya existe).
- Produces: `new_analysis` expone `allows_sample_generation` en el contexto del template. `analyze_submit` crea el `AnalysisJob` con `generation_mode` validado.

- [ ] **Step 1: Escribir los tests que fallan — vista `new_analysis` expone el permiso**

En `core/brand_dna/tests/test_views.py`, agrega estos tests después de `test_new_analysis_without_screenshots_hides_gallery` (línea ~67):

```python
def test_new_analysis_hides_sample_mode_selector_without_permission(user):
    c = Client()
    c.force_login(user)
    response = c.get('/nuevo-analisis/')
    assert response.status_code == 200
    assert response.context['allows_sample_generation'] is False
    assert b'name="generation_mode"' not in response.content


def test_new_analysis_shows_sample_mode_selector_with_permission(user, free_plan):
    free_plan.allows_sample_generation = True
    free_plan.save(update_fields=['allows_sample_generation'])
    c = Client()
    c.force_login(user)
    response = c.get('/nuevo-analisis/')
    assert response.status_code == 200
    assert response.context['allows_sample_generation'] is True
    assert b'name="generation_mode"' in response.content
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -k "sample_mode_selector" -v`
Expected: FAIL — `allows_sample_generation` no existe en el contexto todavía.

- [ ] **Step 3: Actualizar la vista `new_analysis`**

En `core/brand_dna/views.py`, localiza (línea ~74):

```python
def new_analysis(request):
    if not request.user.is_authenticated:
        return redirect('login')
    return render(request, 'brand_dna/new_analysis.html', _screenshots_context())
```

Reemplázalo por:

```python
def new_analysis(request):
    if not request.user.is_authenticated:
        return redirect('login')
    from core.brand_dna.rate_limits import get_user_plan
    context = _screenshots_context()
    context['allows_sample_generation'] = get_user_plan(request.user).allows_sample_generation
    return render(request, 'brand_dna/new_analysis.html', context)
```

- [ ] **Step 4: Agregar el selector al template**

En `core/brand_dna/templates/brand_dna/new_analysis.html`, localiza (línea ~100-105):

```html
      <div class="form-group">
        <label>Logo de tu marca <span class="optional-badge">opcional</span></label>
        <input type="file" name="logo" accept="image/*">
      </div>

      <p style="text-align:center;color:#888;font-size:0.85rem;margin-top:20px;margin-bottom:4px;">Vamos a crear tu plan de contenido de siete días basado en tu marca.</p>
```

Reemplázalo por (agrega el bloque condicional entre el logo y el párrafo existente):

```html
      <div class="form-group">
        <label>Logo de tu marca <span class="optional-badge">opcional</span></label>
        <input type="file" name="logo" accept="image/*">
      </div>

      {% if allows_sample_generation %}
      <div class="form-group">
        <label>¿Qué quieres generar?</label>
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:6px;">
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="full" checked> Calendario completo (7 días)
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_image"> Solo 1 imagen de muestra
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_reel"> Solo 1 reel de muestra
          </label>
        </div>
      </div>
      {% endif %}

      <p style="text-align:center;color:#888;font-size:0.85rem;margin-top:20px;margin-bottom:4px;">Vamos a crear tu plan de contenido de siete días basado en tu marca.</p>
```

- [ ] **Step 5: Incluir `generation_mode` en el FormData del JS**

En el mismo archivo, localiza el bloque JS (línea ~167):

```javascript
        var fields = ['business_url', 'business_name', 'business_description'];
        fields.forEach(function(name) {
          var el = form.querySelector('[name="' + name + '"]');
          if (el) fd.append(name, el.value);
        });
```

Reemplázalo por:

```javascript
        var fields = ['business_url', 'business_name', 'business_description'];
        fields.forEach(function(name) {
          var el = form.querySelector('[name="' + name + '"]');
          if (el) fd.append(name, el.value);
        });

        var selectedMode = form.querySelector('[name="generation_mode"]:checked');
        if (selectedMode) fd.append('generation_mode', selectedMode.value);
```

Nota: el submit es vía XHR con un `FormData` armado a mano — sin este paso, el radio existiría en el HTML pero su valor nunca llegaría al backend, sin importar qué tan correcto esté el resto.

- [ ] **Step 6: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -k "sample_mode_selector" -v`
Expected: 2 passed.

- [ ] **Step 7: Escribir los tests que fallan — validación server-side en `analyze_submit`**

En `core/brand_dna/tests/test_views.py`, agrega estos tests después de `test_analyze_submit_creates_job` (línea ~82, antes de `test_analyze_submit_without_url_with_description`):

```python
def test_analyze_submit_saves_sample_mode_when_permitted(user, free_plan):
    free_plan.allows_sample_generation = True
    free_plan.save(update_fields=['allows_sample_generation'])
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.django_rq'), \
         patch('core.brand_dna.moderation.check_business_legitimacy', return_value=(True, '')):
        c.post('/analizar/', {
            'business_name': 'Tu Web MX',
            'business_description': 'Agencia digital que hace sitios web.',
            'generation_mode': 'sample_reel',
        })
    job = AnalysisJob.objects.filter(user=user).latest('created_at')
    assert job.generation_mode == AnalysisJob.MODE_SAMPLE_REEL


def test_analyze_submit_ignores_sample_mode_without_permission(user):
    # free_plan (fixture) tiene allows_sample_generation=False por default —
    # un POST con generation_mode=sample_reel debe forzarse a 'full', nunca
    # confiar en el valor del cliente para una capacidad restringida.
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.django_rq'), \
         patch('core.brand_dna.moderation.check_business_legitimacy', return_value=(True, '')):
        c.post('/analizar/', {
            'business_name': 'Tu Web MX',
            'business_description': 'Agencia digital que hace sitios web.',
            'generation_mode': 'sample_reel',
        })
    job = AnalysisJob.objects.filter(user=user).latest('created_at')
    assert job.generation_mode == AnalysisJob.MODE_FULL


def test_analyze_submit_defaults_to_full_when_mode_missing(user, free_plan):
    free_plan.allows_sample_generation = True
    free_plan.save(update_fields=['allows_sample_generation'])
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.django_rq'), \
         patch('core.brand_dna.moderation.check_business_legitimacy', return_value=(True, '')):
        c.post('/analizar/', {
            'business_name': 'Tu Web MX',
            'business_description': 'Agencia digital que hace sitios web.',
        })
    job = AnalysisJob.objects.filter(user=user).latest('created_at')
    assert job.generation_mode == AnalysisJob.MODE_FULL
```

- [ ] **Step 8: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -k "sample_mode and not selector" -v`
Expected: FAIL — `generation_mode` no se guarda todavía (los 3 tests esperan comportamiento distinto al actual, que ignora el campo por completo).

- [ ] **Step 9: Implementar la validación en `analyze_submit`**

En `core/brand_dna/views.py`, localiza (línea ~123-130):

```python
    business_description = f"{business_name}\n{business_description}"

    # Reenvio accidental del mismo formulario (recarga de pagina, segunda
    # pestana) mientras el analisis anterior sigue en curso — el boton ya se
    # deshabilita en el primer clic (new_analysis.html), pero eso no protege
    # contra una carga de pagina nueva. Redirige al job existente en vez de
    # duplicar el consumo de API.
    duplicate_job = AnalysisJob.objects.filter(
        user=request.user,
        business_description=business_description,
        status__in=[AnalysisJob.STATUS_PENDING, AnalysisJob.STATUS_PROCESSING],
    ).first()
    if duplicate_job:
        return redirect('results', job_id=duplicate_job.id)

    job = AnalysisJob.objects.create(
        email=email,
        business_url=business_url,
        business_description=business_description,
        user=request.user,
```

Reemplázalo por (agrega la validación de `generation_mode` antes de crear el job, y lo pasa al `create`):

```python
    business_description = f"{business_name}\n{business_description}"

    # Reenvio accidental del mismo formulario (recarga de pagina, segunda
    # pestana) mientras el analisis anterior sigue en curso — el boton ya se
    # deshabilita en el primer clic (new_analysis.html), pero eso no protege
    # contra una carga de pagina nueva. Redirige al job existente en vez de
    # duplicar el consumo de API.
    duplicate_job = AnalysisJob.objects.filter(
        user=request.user,
        business_description=business_description,
        status__in=[AnalysisJob.STATUS_PENDING, AnalysisJob.STATUS_PROCESSING],
    ).first()
    if duplicate_job:
        return redirect('results', job_id=duplicate_job.id)

    # get_user_plan ya esta importado arriba en esta funcion (linea ~98,
    # junto a can_create_calendar) — no hace falta reimportarlo.
    requested_mode = request.POST.get('generation_mode', AnalysisJob.MODE_FULL)
    valid_modes = {AnalysisJob.MODE_FULL, AnalysisJob.MODE_SAMPLE_IMAGE, AnalysisJob.MODE_SAMPLE_REEL}
    if requested_mode not in valid_modes or not get_user_plan(request.user).allows_sample_generation:
        requested_mode = AnalysisJob.MODE_FULL

    job = AnalysisJob.objects.create(
        email=email,
        business_url=business_url,
        business_description=business_description,
        user=request.user,
        generation_mode=requested_mode,
```

- [ ] **Step 10: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -v`
Expected: todos en PASS (incluidos los 5 nuevos de este task más el resto de la suite ya existente, que no debe romperse).

- [ ] **Step 11: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/templates/brand_dna/new_analysis.html \
        core/brand_dna/tests/test_views.py
git commit -m "feat(prospeccion): selector de modo (calendario/muestra) en el formulario de analisis, validado server-side"
```

---

## Task 3: `generate_sample_task` (nueva) + `analyze_brand_task` bifurca según `generation_mode`

Se implementan juntas porque son interdependientes: la bifurcación de
`analyze_brand_task` no se puede probar ni commitear de forma independiente
sin que `generate_sample_task` exista de verdad (el import fallaría). Este
task las construye en el orden correcto — primero la función nueva, después
quien la invoca — y termina en un solo commit coherente.

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Modify: `core/brand_dna/tasks.py`
- Test: `core/content_pipeline/tests/test_tasks.py`
- Test: `core/brand_dna/tests/test_tasks.py`

**Interfaces:**
- Consumes: `AnalysisJob.generation_mode`/`MODE_FULL`/`MODE_SAMPLE_IMAGE`/`MODE_SAMPLE_REEL` (Task 1), `_generate_post_media` (ya existe en `core/content_pipeline/tasks.py`, sin cambios).
- Produces: `generate_sample_task(job_id: str) -> None` en `core/content_pipeline/tasks.py`. `analyze_brand_task` encola `content_generation_task` o `generate_sample_task` según `job.generation_mode`.

### Parte A — `generate_sample_task`

- [ ] **Step 1: Escribir los tests que fallan**

En `core/content_pipeline/tests/test_tasks.py`, agrega este bloque después de
`test_content_generation_falls_back_to_image_when_reel_generation_fails`
(línea ~221, antes de la sección de `calendar_with_dna`):

```python
_MOCK_POSTS_FOR_SAMPLE = [
    {'caption': 'Post reel', 'hashtags': ['#test'], 'suggested_time': '19:00', 'format': 'reel'},
    {'caption': 'Post imagen', 'hashtags': ['#test'], 'suggested_time': '19:00', 'format': 'single'},
    {'caption': 'Post carrusel', 'hashtags': ['#test'], 'suggested_time': '19:00', 'format': 'carousel'},
] + [
    {'caption': f'Post {i}', 'hashtags': ['#test'], 'suggested_time': '19:00', 'format': 'single'}
    for i in range(4, 8)
]


@pytest.fixture
def job_with_dna_sample_image():
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status=AnalysisJob.STATUS_PROCESSING, stage=AnalysisJob.STAGE_CONTENT, progress=78,
        generation_mode=AnalysisJob.MODE_SAMPLE_IMAGE,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    return job


@pytest.fixture
def job_with_dna_sample_reel():
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status=AnalysisJob.STATUS_PROCESSING, stage=AnalysisJob.STAGE_CONTENT, progress=78,
        generation_mode=AnalysisJob.MODE_SAMPLE_REEL,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    return job


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_creates_single_post_calendar_for_image(job_with_dna_sample_image):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails') as mock_schedule:
        MockText.return_value.generate.return_value = _MOCK_POSTS_FOR_SAMPLE
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/sample.jpg'

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_image.id))

    assert ContentCalendar.objects.filter(brand_dna__job=job_with_dna_sample_image).exists()
    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna_sample_image)
    assert posts.count() == 1
    post = posts.first()
    assert post.format == ContentPost.FORMAT_SINGLE
    assert post.caption == 'Post imagen'
    assert post.image_url == 'https://storage.googleapis.com/test/sample.jpg'
    MockEmail.return_value.send_initial.assert_not_called()
    mock_schedule.assert_not_called()
    job_with_dna_sample_image.refresh_from_db()
    assert job_with_dna_sample_image.status == AnalysisJob.STATUS_DONE


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_creates_single_post_calendar_for_reel(job_with_dna_sample_reel):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ReelScriptGenerator') as MockScript, \
         patch('core.content_pipeline.tasks.ReelGenerator') as MockReel, \
         patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails') as mock_schedule:
        MockText.return_value.generate.return_value = _MOCK_POSTS_FOR_SAMPLE
        MockScript.return_value.generate.return_value = {'hook_text': 'H', 'scene_prompts': ['a', 'b', 'c']}
        MockReel.return_value.generate.return_value = ('https://storage.test/reel.mp4', 'https://storage.test/poster.png')

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_reel.id))

    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna_sample_reel)
    assert posts.count() == 1
    post = posts.first()
    assert post.format == ContentPost.FORMAT_REEL
    assert post.caption == 'Post reel'
    assert post.video_url == 'https://storage.test/reel.mp4'
    MockEmail.return_value.send_initial.assert_not_called()
    mock_schedule.assert_not_called()


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_marks_failed_on_error(job_with_dna_sample_image):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText:
        MockText.return_value.generate.side_effect = Exception('Gemini error')

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_image.id))

    job_with_dna_sample_image.refresh_from_db()
    assert job_with_dna_sample_image.status == AnalysisJob.STATUS_FAILED
    assert 'Gemini error' in job_with_dna_sample_image.error_message
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py -k "generate_sample_task" -v`
Expected: FAIL — `generate_sample_task` no existe todavía.

- [ ] **Step 3: Implementar `generate_sample_task`**

En `core/content_pipeline/tasks.py`, agrega esta función nueva justo después de `content_generation_task` (después del `except` final de esa función, línea ~119-120, antes de `_generate_missing_image`):

```python
def generate_sample_task(job_id: str) -> None:
    """Genera 1 sola pieza (imagen o reel) en vez del calendario completo —
    usado para prospeccion (ver AnalysisJob.generation_mode). Reutiliza
    TextGenerator/_generate_post_media tal cual: TextGenerator ya fija el
    formato por posicion (dia 1/indice 0 = reel via REEL_DAY, el resto =
    single salvo dia 3/indice 2 = carousel via CAROUSEL_DAY), asi que solo
    se toma el primer post que coincida con el formato pedido."""
    job = AnalysisJob.objects.get(id=job_id)
    brand_dna = job.brand_dna

    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        wanted_format = (
            ContentPost.FORMAT_REEL if job.generation_mode == AnalysisJob.MODE_SAMPLE_REEL
            else ContentPost.FORMAT_SINGLE
        )
        post_data = next(p for p in posts_data if p.get('format') == wanted_format)

        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

        image_url, image_urls, video_url = _generate_post_media(
            image_gen, reel_script_gen, reel_gen,
            fmt=wanted_format,
            filename=f"{job_id}-sample",
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
            day_number=1,
            caption=post_data['caption'],
            image_url=image_url,
            image_urls=image_urls,
            video_url=video_url,
            format=wanted_format,
            suggested_time='09:00',
            hashtags=post_data.get('hashtags', []),
            scheduled_at=timezone.now(),
        )

        job.stage = AnalysisJob.STAGE_COMPLETE
        job.progress = 100
        job.status = AnalysisJob.STATUS_DONE
        job.save(update_fields=['stage', 'progress', 'status'])
        logger.info(f"Muestra generada para job {job_id} ({wanted_format})")

    except Exception as e:
        logger.error(f"generate_sample_task error para job {job_id}: {e}")
        job.mark_failed(str(e))
```

No se necesitan imports nuevos — `TextGenerator`, `ImageGenerator`, `ReelScriptGenerator`, `ReelGenerator`, `ContentCalendar`, `ContentPost`, `AnalysisJob`, `settings`, `timezone` y `logger` ya están importados al inicio de este archivo (usados por `content_generation_task`).

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: todos en PASS, incluidos los 3 tests nuevos de `generate_sample_task`.

### Parte B — `analyze_brand_task` bifurca según `generation_mode`

Con `generate_sample_task` ya implementada y probada, ahora sí se puede
probar y commitear la bifurcación que la invoca.

- [ ] **Step 5: Escribir los tests que fallan**

En `core/brand_dna/tests/test_tasks.py`, agrega estos tests después de `test_task_enqueues_content_generation` (línea ~72):

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_task_enqueues_content_generation_for_full_mode(pending_job):
    pending_job.generation_mode = AnalysisJob.MODE_FULL
    pending_job.save(update_fields=['generation_mode'])
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.fetch_context.return_value = ('texto del sitio', ['#123456'])
        MockExtractor.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    from core.content_pipeline.tasks import content_generation_task
    assert mock_rq.enqueue.call_args.args[0] is content_generation_task


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_task_enqueues_sample_generation_for_sample_mode(pending_job):
    pending_job.generation_mode = AnalysisJob.MODE_SAMPLE_REEL
    pending_job.save(update_fields=['generation_mode'])
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.fetch_context.return_value = ('texto del sitio', ['#123456'])
        MockExtractor.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    from core.content_pipeline.tasks import generate_sample_task
    assert mock_rq.enqueue.call_args.args[0] is generate_sample_task
```

Nota: `test_task_enqueues_content_generation` (ya existente, línea ~60) sigue intacto — `pending_job` no fija `generation_mode`, así que usa el default `'full'` del modelo y su aserción (`mock_rq.enqueue.assert_called_once()`) sigue siendo válida sin cambios.

- [ ] **Step 6: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_tasks.py -k "enqueues_content_generation_for_full_mode or enqueues_sample_generation_for_sample_mode" -v`
Expected: FAIL — `analyze_brand_task` siempre encola `content_generation_task` sin importar `generation_mode` todavía.

- [ ] **Step 7: Implementar la bifurcación**

En `core/brand_dna/tasks.py`, localiza (línea ~67-70):

```python
        from core.content_pipeline.tasks import content_generation_task
        # Genera 7 imagenes con reintentos de QC — el timeout global (360s) se queda
        # corto. 25 min da margen amplio incluso con reintentos en varios dias.
        django_rq.enqueue(content_generation_task, str(job_id), job_timeout=2400)
```

Reemplázalo por:

```python
        from core.content_pipeline.tasks import content_generation_task, generate_sample_task
        # Genera 7 imagenes con reintentos de QC (o 1 sola pieza en modo
        # muestra) — el timeout global (360s) se queda corto. 25 min da
        # margen amplio incluso con reintentos en varios dias.
        task = content_generation_task if job.generation_mode == AnalysisJob.MODE_FULL else generate_sample_task
        django_rq.enqueue(task, str(job_id), job_timeout=2400)
```

- [ ] **Step 8: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_tasks.py -v`
Expected: todos en PASS.

- [ ] **Step 9: Correr la suite completa del proyecto**

Run: `docker compose exec -T backend python -m pytest`
Expected: todos en PASS.

- [ ] **Step 10: Commit**

```bash
git add core/brand_dna/tasks.py core/brand_dna/tests/test_tasks.py \
        core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
git commit -m "feat(prospeccion): generate_sample_task genera 1 sola pieza sin calendario completo ni correos"
```

---

## Task 4: Verificación real end-to-end (no delegar — la ejecuta el controlador de esta sesión)

Gasta cuota real de Imagen/Veo/Gemini. Corre en el contenedor `rqworker` (el que tiene HyperFrames instalado para reels).

- [ ] **Step 1: Levantar el stack con el código nuevo**

```bash
docker compose up -d --force-recreate --no-deps --scale rqworker=3 backend rqworker
```

- [ ] **Step 2: Activar el permiso en el Plan Admin real**

```bash
docker compose exec -T backend python manage.py shell -c "
from core.tenant_management.models import Plan
plan = Plan.objects.get(name='Admin')
plan.allows_sample_generation = True
plan.save(update_fields=['allows_sample_generation'])
print('allows_sample_generation:', plan.allows_sample_generation)
"
```
Expected: `allows_sample_generation: True`. Si no existe un Plan llamado `'Admin'`, listar los planes existentes (`Plan.objects.values_list('name', flat=True)`) y usar el nombre real del plan de administrador.

- [ ] **Step 3: Generar 1 muestra real de imagen de punta a punta**

`analyze_brand_task` termina ENCOLANDO `generate_sample_task` vía RQ real
(no la ejecuta inline) — si solo se llama `analyze_brand_task` y se revisa
el resultado de inmediato, el job puede seguir en `processing` porque quien
la recoge es el proceso `rqworker` aparte. Para una verificación síncrona y
determinista, se llaman ambas funciones directo, en el mismo comando:

```bash
docker compose exec -T rqworker python manage.py shell -c "
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.brand_dna.tasks import analyze_brand_task
from core.content_pipeline.tasks import generate_sample_task
from django.contrib.auth import get_user_model

User = get_user_model()
admin_user = User.objects.filter(groups__name='admin').first()
print('admin_user:', admin_user)

job = AnalysisJob.objects.create(
    email=admin_user.email if admin_user else 'test@example.com',
    business_description='Tu Web MX\nAgencia digital que hace sitios web para pymes.',
    user=admin_user,
    generation_mode=AnalysisJob.MODE_SAMPLE_IMAGE,
)
analyze_brand_task(str(job.id))
generate_sample_task(str(job.id))
job.refresh_from_db()
print('status:', job.status)
print('error:', job.error_message)
calendar = job.brand_dna.calendar
print('posts:', calendar.posts.count())
post = calendar.posts.first()
print('format:', post.format, 'image_url:', post.image_url)
"
```
Expected: `status: done`, `posts: 1`, `format: single`, `image_url` con una URL real de `storage.googleapis.com`.

- [ ] **Step 4: Generar 1 muestra real de reel de punta a punta**

Mismo comando que el Step 3, cambiando `generation_mode=AnalysisJob.MODE_SAMPLE_REEL` y ajustando los prints finales a `post.format`/`post.video_url`.
Expected: `status: done`, `posts: 1`, `format: reel`, `video_url` con una URL real de `storage.googleapis.com` terminada en `.mp4`.

- [ ] **Step 5: Verificar visualmente en `calendar_review.html`**

Con el `job.id` de cada corrida, abrir `https://<host>/calendar/<job.id>/` autenticado como el usuario admin usado. Confirmar: se ve exactamente 1 tarjeta (no 7), descarga funcional, y en el caso del reel, el aviso de "súbelo como Reel" (HALLAZGO 75) visible.

- [ ] **Step 6: Confirmar que no se disparó ningún correo**

```bash
docker compose logs backend rqworker --since 15m | grep -i "send_initial\|schedule_daily_emails\|email enviado\|EmailSender"
```
Expected: sin coincidencias relacionadas a estas 2 corridas (o solo las de negocios reales no relacionados, si los hay en el log).

- [ ] **Step 7: Revisar logs por errores**

```bash
docker compose logs backend rqworker --since 15m | grep -i error | grep -v "INFO\|DeprecationWarning\|content_blocked"
```
Expected: sin errores relacionados a `generate_sample_task`.

- [ ] **Step 8: Documentar el resultado**

Agregar una entrada nueva a `hallazgos.txt` (mismo formato que HALLAZGO 74/75) documentando: el permiso activado, las 2 generaciones reales (imagen y reel), confirmación de que no se dispararon correos, y confirmación visual en `calendar_review.html`.

```bash
git add hallazgos.txt
git commit -m "docs: HALLAZGO 76 - generacion de muestra individual verificada en real"
git push origin main
```
