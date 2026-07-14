# Eliminación de la carga de fotos de producto — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar por completo la carga de fotos de producto (formulario, campos de BD, flujo BGSWAP de Imagen 3) manteniendo el upload de logo intacto.

**Architecture:** Eliminación en 4 capas, cada una desplegable de forma independiente: (1) UI/intake de subida, (2) lógica de generación que consume las fotos, (3) núcleo de `image_generator.py` (BGSWAP), (4) modelo/BD + limpieza + copy legal. Es eliminación pura — no hay ciclo TDD rojo-verde; cada tarea verifica quitando los tests que cubrían el código eliminado y confirmando que la suite sigue en verde.

**Tech Stack:** Django 5.2, pytest-django, migraciones Django, Vertex AI (Imagen 3 BGSWAP — se elimina), GCS.

## Global Constraints

- El logo de marca (`logo_file_path`) NO se toca — feature separada, se queda igual en las 4 tareas.
- El branding "Art Director" `mode: product/lifestyle` en `image_generator.py` (`_analyze_brand_scene`, `_generate_background`, `_SCENE_FALLBACKS`, `_PRODUCT_FALLBACKS`) NO se toca — genera el fondo desde cero, no depende de fotos subidas por el usuario.
- Las etiquetas de métricas Prometheus (`img_type='bgswap'`) NO se tocan.
- Los archivos ya subidos por usuarios en GCS/almacenamiento NO se borran como parte de este trabajo.
- Cada tarea deja la suite de tests en verde antes de pasar a la siguiente — el orden importa (UI → lógica → núcleo del generador → modelo/BD) porque cada capa depende de que la BD siga teniendo los campos hasta la Tarea 4.
- Comando de test para verificar cada tarea: `docker compose exec -T backend python -m pytest <paths> -v`.
- Commits en español, prefijo `remove(product-photos):`, sin GIT_EDITOR heredoc (usar `git commit -m "..."` directo).

---

## Task 1: UI + intake de subida

**Files:**
- Modify: `core/brand_dna/templates/brand_dna/new_analysis.html`
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html`
- Modify: `core/brand_dna/views.py`
- Test: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: nada de tareas anteriores (primera tarea).
- Produces: `analyze_submit`, `calendar_review_view`, `calendar_feedback_api` ya NO aceptan ni procesan `product_images`/`image_choice`. La función `_update_active_product_images` deja de existir — las Tareas 2-4 no deben referenciarla.

- [ ] **Step 1: Quitar el campo de subida en `new_analysis.html`**

En `core/brand_dna/templates/brand_dna/new_analysis.html`, eliminar el bloque `<details class="extra-info">...</details>` completo (líneas 105-120 del archivo actual — todo el collapsible "¿Quieres mejorar el resultado?" que solo contiene el campo de fotos de producto):

```html
      <details class="extra-info">
        <summary>
          <span class="arrow">&#9654;</span>
          ¿Quieres mejorar el resultado? Agrega más info
          <span class="optional-badge">opcional</span>
        </summary>
        <div class="extra-body">
          <div class="section-title" style="margin-top:16px;">Fotos de tu producto</div>
          <div class="form-group">
            <label>Sube hasta 7 fotos de tu producto <span style="color:#666;font-weight:400;">(cada dia usa una diferente)</span></label>
            <input type="file" name="product_images" accept="image/*" multiple>
            <small style="color:#666;font-size:0.8rem;margin-top:4px;display:block;">Ej: collar de plata, platillo del menu, prenda de ropa. Con 1 imagen se usa 2 dias; con 3-7 imagenes, una distinta por dia.</small>
            <small style="color:#555;font-size:0.78rem;margin-top:2px;display:block;">Sube imágenes sin texto ni marcas de agua para mejores resultados.</small>
          </div>
        </div>
      </details>

```

Debe quedar el `<div class="form-group">` del Logo (líneas 100-103) seguido directamente del `<p style="text-align:center;...">Vamos a crear tu plan de contenido...</p>`.

- [ ] **Step 2: Quitar la compresión/envío de fotos de producto del JS en `new_analysis.html`**

Reemplazar:

```javascript
      var logoInput = form.querySelector('[name="logo"]');
      var prodInput = form.querySelector('[name="product_images"]');

      Promise.all([
        logoInput.files.length ? compressAll(logoInput.files) : Promise.resolve([]),
        prodInput.files.length ? compressAll(prodInput.files) : Promise.resolve([]),
      ]).then(function(results) {
        var fd = new FormData();
        fd.append('csrfmiddlewaretoken', form.querySelector('[name="csrfmiddlewaretoken"]').value);

        var fields = ['business_url', 'business_name', 'business_description'];
        fields.forEach(function(name) {
          var el = form.querySelector('[name="' + name + '"]');
          if (el) fd.append(name, el.value);
        });

        results[0].forEach(function(f) { fd.append('logo', f); });
        results[1].forEach(function(f) { fd.append('product_images', f); });
```

Por:

```javascript
      var logoInput = form.querySelector('[name="logo"]');

      Promise.all([
        logoInput.files.length ? compressAll(logoInput.files) : Promise.resolve([]),
      ]).then(function(results) {
        var fd = new FormData();
        fd.append('csrfmiddlewaretoken', form.querySelector('[name="csrfmiddlewaretoken"]').value);

        var fields = ['business_url', 'business_name', 'business_description'];
        fields.forEach(function(name) {
          var el = form.querySelector('[name="' + name + '"]');
          if (el) fd.append(name, el.value);
        });

        results[0].forEach(function(f) { fd.append('logo', f); });
```

- [ ] **Step 3: Quitar la sección de imágenes de producto en `calendar_review.html`**

Dentro del bloque `{% if pending_feedback %}...{% endif %}`, eliminar este `<div>` completo (queda solo el `<h2>`, el `<p>` de descripción, y el botón "Generar nueva semana →"):

```html
    <div style="margin-bottom:16px;">
      <div style="font-size:0.85rem;color:#ccc;margin-bottom:10px;">Imágenes de producto para tu próxima semana <span style="color:#666;font-weight:400;">(opcional)</span></div>

      <label style="display:flex;align-items:center;gap:8px;font-size:0.85rem;margin-bottom:8px;cursor:pointer;">
        <input type="radio" name="image_choice" value="reuse" checked onchange="toggleImageMode()"> Reutilizar mis imágenes
      </label>
      <label style="display:flex;align-items:center;gap:8px;font-size:0.85rem;margin-bottom:12px;cursor:pointer;">
        <input type="radio" name="image_choice" value="new" onchange="toggleImageMode()"> Subir nuevas imágenes
      </label>

      {% if product_pool|length > 7 %}
      <div id="image-gallery" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(70px,1fr));gap:8px;margin-bottom:12px;">
        {% for img in product_pool %}
        <label style="position:relative;cursor:pointer;">
          <input type="checkbox" class="gallery-checkbox" value="{{ img }}" style="position:absolute;top:4px;left:4px;z-index:1;">
          <img src="/media/{{ img }}" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px;border:2px solid transparent;" loading="lazy">
        </label>
        {% endfor %}
      </div>
      {% endif %}

      <div id="image-upload-section" style="display:none;">
        <input type="file" id="product-images-input" multiple accept="image/jpeg,image/png" style="font-size:0.82rem;color:#ccc;">
        <p style="font-size:0.75rem;color:#666;margin-top:6px;">Hasta 7 imágenes.</p>
      </div>
    </div>

```

- [ ] **Step 4: Simplificar el JS de `calendar_review.html`**

Reemplazar (elimina `toggleImageMode`, el listener de `.gallery-checkbox`, y la parte de `submitFeedback` que arma `image_choice`/`selected_images`/`product_images`):

```javascript
  function toggleImageMode() {
    const mode = document.querySelector('input[name="image_choice"]:checked').value;
    const gallery = document.getElementById('image-gallery');
    const upload = document.getElementById('image-upload-section');
    if (mode === 'new') {
      if (gallery) gallery.style.display = 'none';
      upload.style.display = 'block';
    } else {
      if (gallery) gallery.style.display = 'grid';
      upload.style.display = 'none';
    }
  }

  document.querySelectorAll('.gallery-checkbox').forEach(cb => {
    cb.addEventListener('change', () => {
      const checked = document.querySelectorAll('.gallery-checkbox:checked');
      if (checked.length > 7) {
        cb.checked = false;
        showToast('Máximo 7 imágenes', '#f0c040');
      }
    });
  });

  async function submitFeedback(decision, btn) {
    const formData = new FormData();
    formData.append('continue_decision', decision);

    const mode = document.querySelector('input[name="image_choice"]:checked').value;
    formData.append('image_choice', mode);
    if (mode === 'reuse') {
      document.querySelectorAll('.gallery-checkbox:checked').forEach(cb => {
        formData.append('selected_images', cb.value);
      });
    } else if (mode === 'new') {
      const files = document.getElementById('product-images-input').files;
      for (let i = 0; i < Math.min(files.length, 7); i++) {
        formData.append('product_images', files[i]);
      }
    }

    const banner = document.getElementById('feedback-banner');
```

Por:

```javascript
  async function submitFeedback(decision, btn) {
    const formData = new FormData();
    formData.append('continue_decision', decision);

    const banner = document.getElementById('feedback-banner');
```

- [ ] **Step 5: Quitar el manejo de `product_images` en `analyze_submit` (`views.py`)**

Eliminar este bloque (justo después de la validación de nombre/descripción, antes de la llamada a `check_business_legitimacy`):

```python
    prod_files = request.FILES.getlist('product_images')
    if len(prod_files) > 7:
        return render(request, 'brand_dna/new_analysis.html', {
            'error': f'Subiste {len(prod_files)} fotos de producto — el máximo es 7. Quita algunas e intenta de nuevo.',
        })

```

Eliminar este otro bloque (después de guardar el logo, antes de `from core.brand_dna.tasks import analyze_brand_task`):

```python
    if prod_files:
        prod_paths = []
        for idx, prod_file in enumerate(prod_files):
            prod_bytes = prod_file.read()
            if not _validate_image_bytes(prod_bytes):
                continue
            ext = _safe_extension(prod_file.name)
            prod_path = f'uploads/product_{job.id}_{idx}.{ext}'
            save_upload(prod_bytes, prod_path)
            prod_paths.append(prod_path)
        if prod_paths:
            job.product_image_paths = prod_paths
            job.product_image_path = prod_paths[0]
            job.save(update_fields=['product_image_path', 'product_image_paths'])

```

- [ ] **Step 6: Quitar `product_pool` del contexto de `calendar_review_view`**

En `views.py`, dentro de `calendar_review_view`, eliminar esta línea del `return render(...)`:

```python
        'product_pool': job.product_image_paths,
```

- [ ] **Step 7: Quitar la validación y la llamada a `_update_active_product_images` en `calendar_feedback_api`, y borrar la función**

Eliminar este bloque de `calendar_feedback_api` (antes de `feedback.rating = rating`):

```python
    if continue_decision == WeeklyFeedback.CONTINUE_YES and request.POST.get('image_choice') == 'new':
        prod_files = request.FILES.getlist('product_images')
        if len(prod_files) > 7:
            return JsonResponse({
                'error': f'Subiste {len(prod_files)} fotos de producto — el máximo es 7. Quita algunas e intenta de nuevo.',
            }, status=400)

```

Eliminar esta línea (dentro del `if feedback.continue_decision == WeeklyFeedback.CONTINUE_YES:`):

```python
        _update_active_product_images(calendar, job, request, next_week)
```

Eliminar la función completa `_update_active_product_images` (entre `calendar_feedback_api` y `_regenerate_caption`):

```python
def _update_active_product_images(calendar, job, request, next_week):
    choice = request.POST.get('image_choice', 'reuse')
    if choice == 'new':
        files = request.FILES.getlist('product_images')[:7]
        new_paths = []
        for idx, f in enumerate(files):
            file_bytes = f.read()
            if not _validate_image_bytes(file_bytes):
                continue
            ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else 'jpg'
            path = f'uploads/product_{job.id}_w{next_week}_{idx}.{ext}'
            save_upload(file_bytes, path)
            new_paths.append(path)
        if new_paths:
            job.product_image_paths = job.product_image_paths + new_paths
            job.save(update_fields=['product_image_paths'])
            calendar.active_product_images = new_paths
            calendar.save(update_fields=['active_product_images'])
    elif choice == 'reuse':
        pool = job.product_image_paths
        if len(pool) > 7:
            selected = request.POST.getlist('selected_images')[:7]
            valid = [p for p in selected if p in pool]
            if valid:
                calendar.active_product_images = valid
                calendar.save(update_fields=['active_product_images'])


```

- [ ] **Step 8: Quitar tests que cubrían la subida en `test_views.py`**

Eliminar la función completa (líneas 131-146 del archivo actual):

```python
def test_analyze_submit_rejects_more_than_7_product_images(user):
    from django.core.files.uploadedfile import SimpleUploadedFile
    c = Client()
    c.force_login(user)
    images = [SimpleUploadedFile(f'p{i}.jpg', b'fake-bytes', content_type='image/jpeg') for i in range(8)]
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        response = c.post('/analizar/', {
            'business_name': 'Tu Web MX',
            'business_description': 'Agencia digital que hace sitios web.',
            'product_images': images,
        })
    assert response.status_code == 200
    assert b'm\xc3\xa1ximo es 7' in response.content
    mock_rq.enqueue.assert_not_called()
    assert not AnalysisJob.objects.filter(user=user).exists()


```

Eliminar la función completa `test_calendar_feedback_api_rejects_more_than_7_new_images`:

```python
def test_calendar_feedback_api_rejects_more_than_7_new_images(client, user, job_with_calendar):
    from django.core.files.uploadedfile import SimpleUploadedFile
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    images = [SimpleUploadedFile(f'p{i}.jpg', b'fake-bytes', content_type='image/jpeg') for i in range(8)]
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '5',
            'comment': '',
            'continue_decision': 'yes',
            'image_choice': 'new',
            'product_images': images,
        })
    assert response.status_code == 400
    data = response.json()
    assert 'máximo es 7' in data['error']
    mock_rq.enqueue.assert_not_called()

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_PENDING


```

Eliminar las 3 funciones completas de test de `_update_active_product_images` (quedan consecutivas en el archivo, justo antes de `test_privacy_policy_accessible_without_login`):

```python
def test_update_active_product_images_reuse_pool_le_7(job_with_calendar):
    from core.brand_dna.views import _update_active_product_images
    from django.test import RequestFactory

    job = job_with_calendar
    job.product_image_paths = ['uploads/p1.jpg', 'uploads/p2.jpg']
    job.save(update_fields=['product_image_paths'])
    calendar = job.brand_dna.calendar
    calendar.active_product_images = ['uploads/p1.jpg', 'uploads/p2.jpg']
    calendar.save(update_fields=['active_product_images'])

    request = RequestFactory().post('/', {'image_choice': 'reuse'})
    _update_active_product_images(calendar, job, request, next_week=2)

    calendar.refresh_from_db()
    assert calendar.active_product_images == ['uploads/p1.jpg', 'uploads/p2.jpg']


def test_update_active_product_images_reuse_pool_gt_7_with_selection(job_with_calendar):
    from core.brand_dna.views import _update_active_product_images
    from django.test import RequestFactory

    job = job_with_calendar
    pool = [f'uploads/p{i}.jpg' for i in range(1, 9)]  # 8 imágenes
    job.product_image_paths = pool
    job.save(update_fields=['product_image_paths'])
    calendar = job.brand_dna.calendar

    selected = pool[:5]
    request = RequestFactory().post('/', {
        'image_choice': 'reuse',
        'selected_images': selected,
    })
    _update_active_product_images(calendar, job, request, next_week=2)

    calendar.refresh_from_db()
    assert calendar.active_product_images == selected


def test_update_active_product_images_new_uploads(job_with_calendar, tmp_path, settings):
    from core.brand_dna.views import _update_active_product_images
    from django.test import RequestFactory
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.MEDIA_ROOT = str(tmp_path)
    job = job_with_calendar
    calendar = job.brand_dna.calendar

    image1 = SimpleUploadedFile('product1.jpg', b'fake-bytes-1', content_type='image/jpeg')
    image2 = SimpleUploadedFile('product2.png', b'fake-bytes-2', content_type='image/png')

    request = RequestFactory().post('/', {
        'image_choice': 'new',
        'product_images': [image1, image2],
    })
    def mock_save_upload(file_bytes, path):
        full_path = os.path.join(settings.MEDIA_ROOT, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'wb') as f:
            f.write(file_bytes)

    with patch('core.brand_dna.views._validate_image_bytes', return_value=True), \
         patch('core.brand_dna.views.save_upload', side_effect=mock_save_upload):
        _update_active_product_images(calendar, job, request, next_week=2)

    job.refresh_from_db()
    calendar.refresh_from_db()

    assert len(job.product_image_paths) == 2
    assert calendar.active_product_images == job.product_image_paths
    for path in calendar.active_product_images:
        full = os.path.join(settings.MEDIA_ROOT, path)
        assert os.path.exists(full)


```

- [ ] **Step 9: Simplificar `test_calendar_feedback_api_yes_triggers_generate_next_week`**

Reemplazar:

```python
def test_calendar_feedback_api_yes_triggers_generate_next_week(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    with patch('core.brand_dna.views.django_rq') as mock_rq, \
         patch('core.brand_dna.views._update_active_product_images') as mock_update:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '5',
            'comment': '',
            'continue_decision': 'yes',
            'image_choice': 'reuse',
        })
    assert response.status_code == 200
    data = response.json()
    assert data['continue_decision'] == 'yes'

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_YES
    mock_update.assert_called_once()
    mock_rq.enqueue.assert_called_once()
    enqueue_args = mock_rq.enqueue.call_args[0]
    assert enqueue_args[1] == str(calendar.id)
    assert enqueue_args[2] == 2

    calendar.refresh_from_db()
    assert calendar.next_week_generating is True
```

Por:

```python
def test_calendar_feedback_api_yes_triggers_generate_next_week(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '5',
            'comment': '',
            'continue_decision': 'yes',
        })
    assert response.status_code == 200
    data = response.json()
    assert data['continue_decision'] == 'yes'

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_YES
    mock_rq.enqueue.assert_called_once()
    enqueue_args = mock_rq.enqueue.call_args[0]
    assert enqueue_args[1] == str(calendar.id)
    assert enqueue_args[2] == 2

    calendar.refresh_from_db()
    assert calendar.next_week_generating is True
```

- [ ] **Step 10: Correr la suite y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -v`
Expected: todos los tests pasan, ninguno menciona `product_images`, `product_pool` ni `_update_active_product_images`.

- [ ] **Step 11: Commit**

```bash
git add core/brand_dna/templates/brand_dna/new_analysis.html core/brand_dna/templates/brand_dna/calendar_review.html core/brand_dna/views.py core/brand_dna/tests/test_views.py
git commit -m "remove(product-photos): quitar formulario, JS y vistas de subida de fotos de producto"
```

---

## Task 2: Lógica de generación de contenido

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Modify: `core/brand_dna/views.py`
- Test: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: nada de UI (Tarea 1 ya no pasa `product_images`/`image_choice` a ninguna vista).
- Produces: `_generate_post_media`, `content_generation_task`, `_generate_missing_image`, `generate_next_week` ya NO tienen ningún parámetro ni variable relacionada a fotos de producto. `_load_product_images`, `_product_image_for_day`, `_disable_carousel_if_full_product_week` dejan de existir — la Tarea 3 no debe referenciarlas.

- [ ] **Step 1: Borrar las 3 funciones helper de `tasks.py`**

Eliminar completas (entre los imports y `_generate_post_media`):

```python
def _load_product_images(paths: list[str]) -> list[bytes]:
    """Carga hasta 7 imágenes de producto desde GCS, normalizadas a WebP."""
    result = []
    for path in (paths or [])[:7]:
        try:
            if upload_exists(path):
                result.append(normalize_image(read_upload(path)))
            else:
                logger.warning(f"Producto no encontrado en GCS: {path}")
        except Exception as e:
            logger.warning(f"Error cargando imagen de producto {path}: {e}")
    return result


```

Eliminar completa (entre `_generate_post_media` y `_disable_carousel_if_full_product_week`):

```python
def _product_image_for_day(day_in_week: int, images: list[bytes]) -> bytes | None:
    """Asigna imagen de producto por día dentro de la semana (1-7).
    - Si hay imagen para ese día exacto: úsala.
    - Si solo hay 1 imagen: se repite el día 2 (máx 2 usos).
    - Después del día 3 sin imagen directa: sin producto.
    """
    n = len(images)
    if n == 0:
        return None
    if day_in_week <= n:
        return images[day_in_week - 1]
    if n == 1 and day_in_week == 2:
        return images[0]
    return None


```

Eliminar completa (entre `_product_image_for_day` y `content_generation_task`):

```python
def _disable_carousel_if_full_product_week(posts_data: list[dict], product_images_bytes: list[bytes]) -> None:
    """Si el usuario subio una foto de producto por cada dia de la semana (7),
    el carrusel (que usa un fondo generado por IA) le restaria protagonismo a
    esas fotos — el usuario quiere mostrar SUS productos ese dia, no
    contenido generico. En ese caso, todos los posts se generan como 'single'."""
    if len(product_images_bytes) == 7:
        for post in posts_data:
            post['format'] = ContentPost.FORMAT_SINGLE


```

- [ ] **Step 2: Quitar los imports que quedan sin uso**

Eliminar estas 2 líneas del bloque de imports al inicio de `tasks.py` (`normalize_image` y `read_upload`/`upload_exists` solo se usaban dentro de `_load_product_images`):

```python
from core.content_pipeline.image_utils import normalize_image
```
```python
from core.shared.gcs_uploads import read_upload, upload_exists
```

- [ ] **Step 3: Simplificar `content_generation_task`**

Reemplazar:

```python
        calendar = ContentCalendar.objects.create(
            brand_dna=brand_dna,
            active_product_images=job.product_image_paths[:7],
        )
        CALENDARS_CREATED.inc()
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()

        scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

        # Cargar imágenes de producto (hasta 7, una por día)
        product_images_bytes = _load_product_images(calendar.active_product_images)
        _disable_carousel_if_full_product_week(posts_data, product_images_bytes)
        if _product_image_for_day(1, product_images_bytes) is not None:
            posts_data[0]['format'] = ContentPost.FORMAT_SINGLE

        # Generamos las 7 imágenes por adelantado — el usuario no espera en vivo
        # (flujo async: se le avisa por correo/dashboard cuando todo está listo),
        # así que el calendario completo queda disponible desde el primer momento.
        total = len(posts_data)
        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

        for i, post_data in enumerate(posts_data, start=1):
            scheduled = scheduled_dates[i - 1]
            day_product = _product_image_for_day(i, product_images_bytes)
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{i}",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                business_url=brand_dna.business_url,
                product_image_bytes=day_product,
                brand_dna=brand_dna,
                post_data=post_data,
            )
```

Por:

```python
        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        CALENDARS_CREATED.inc()
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()

        scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

        # Generamos las 7 imágenes por adelantado — el usuario no espera en vivo
        # (flujo async: se le avisa por correo/dashboard cuando todo está listo),
        # así que el calendario completo queda disponible desde el primer momento.
        total = len(posts_data)
        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

        for i, post_data in enumerate(posts_data, start=1):
            scheduled = scheduled_dates[i - 1]
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{i}",
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
```

- [ ] **Step 4: Simplificar `_generate_missing_image`**

Reemplazar:

```python
def _generate_missing_image(post: ContentPost) -> None:
    """Genera y guarda la imagen de un post que quedo sin image_url. No lanza — loggea y sigue."""
    brand_dna = post.calendar.brand_dna
    job_id = str(brand_dna.job.id)
    day_in_week = ((post.day_number - 1) % 7) + 1
    try:
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        product_images = _load_product_images(post.calendar.active_product_images)
        product_image_bytes = _product_image_for_day(day_in_week, product_images)
        post.image_url, post.image_urls, post.video_url = _generate_post_media(
            image_gen, ReelScriptGenerator(), ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET),
            fmt=post.format,
            filename=f"{job_id}-day{post.day_number}",
            caption=post.caption,
            colors=brand_dna.primary_colors,
            tone=brand_dna.tone,
            brand_name=brand_dna.business_name,
            keywords=brand_dna.keywords,
            description=brand_dna.description,
            audience=brand_dna.audience,
            business_url=brand_dna.business_url,
            product_image_bytes=product_image_bytes,
            brand_dna=brand_dna,
            post_data={'caption': post.caption},
        )
        post.save(update_fields=['image_url', 'image_urls', 'video_url'])
    except Exception as img_err:
        logger.warning(f"Imagen día {post.day_number} falló (no fatal): {img_err}")
```

Por:

```python
def _generate_missing_image(post: ContentPost) -> None:
    """Genera y guarda la imagen de un post que quedo sin image_url. No lanza — loggea y sigue."""
    brand_dna = post.calendar.brand_dna
    job_id = str(brand_dna.job.id)
    try:
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        post.image_url, post.image_urls, post.video_url = _generate_post_media(
            image_gen, ReelScriptGenerator(), ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET),
            fmt=post.format,
            filename=f"{job_id}-day{post.day_number}",
            caption=post.caption,
            colors=brand_dna.primary_colors,
            tone=brand_dna.tone,
            brand_name=brand_dna.business_name,
            keywords=brand_dna.keywords,
            description=brand_dna.description,
            audience=brand_dna.audience,
            business_url=brand_dna.business_url,
            brand_dna=brand_dna,
            post_data={'caption': post.caption},
        )
        post.save(update_fields=['image_url', 'image_urls', 'video_url'])
    except Exception as img_err:
        logger.warning(f"Imagen día {post.day_number} falló (no fatal): {img_err}")
```

- [ ] **Step 5: Simplificar `generate_next_week`**

Reemplazar:

```python
        base_day = (week_number - 1) * 7
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        product_images_bytes = _load_product_images(calendar.active_product_images)
        _disable_carousel_if_full_product_week(posts_data, product_images_bytes)
        if _product_image_for_day(1, product_images_bytes) is not None:
            posts_data[0]['format'] = ContentPost.FORMAT_SINGLE

        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

        for i, post_data in enumerate(posts_data, start=1):
            scheduled = scheduled_dates[i - 1]
            day_product = _product_image_for_day(i, product_images_bytes)
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{base_day + i}",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                business_url=brand_dna.business_url,
                product_image_bytes=day_product,
                brand_dna=brand_dna,
                post_data=post_data,
            )
```

Por:

```python
        base_day = (week_number - 1) * 7
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

        for i, post_data in enumerate(posts_data, start=1):
            scheduled = scheduled_dates[i - 1]
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{base_day + i}",
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
```

- [ ] **Step 6: Quitar la lectura de `product_image_path` en `post_action_api` (`views.py`)**

Reemplazar:

```python
        try:
            from core.content_pipeline.generators.image_generator import ImageGenerator
            from core.content_pipeline.tasks import _generate_post_media
            brand_dna = post.calendar.brand_dna
            job_id = str(brand_dna.job.id)
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            product_image_bytes = None
            if brand_dna.job.product_image_path:
                gcs_path = brand_dna.job.product_image_path
                if upload_exists(gcs_path):
                    product_image_bytes = read_upload(gcs_path)
            generated_url, generated_urls, _ = _generate_post_media(
                image_gen,
                None,  # reel_script_gen
                None,  # reel_gen
                fmt=post.format,
                filename=f"{job_id}-day{post.day_number}-regen-{int(_time.time())}",
                caption=new_caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                product_image_bytes=product_image_bytes,
                max_qc_retries=0,  # regen es síncrono — sin reintentos QC para evitar timeout
            )
```

Por:

```python
        try:
            from core.content_pipeline.generators.image_generator import ImageGenerator
            from core.content_pipeline.tasks import _generate_post_media
            brand_dna = post.calendar.brand_dna
            job_id = str(brand_dna.job.id)
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            generated_url, generated_urls, _ = _generate_post_media(
                image_gen,
                None,  # reel_script_gen
                None,  # reel_gen
                fmt=post.format,
                filename=f"{job_id}-day{post.day_number}-regen-{int(_time.time())}",
                caption=new_caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                max_qc_retries=0,  # regen es síncrono — sin reintentos QC para evitar timeout
            )
```

- [ ] **Step 7: Quitar la lectura de `product_image_path` en `regenerate_calendar_api` (`views.py`)**

Reemplazar:

```python
    day1 = posts_by_day.get(1)
    if day1 and day1.image_url:
        try:
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            product_image_bytes = None
            if job.product_image_path and upload_exists(job.product_image_path):
                product_image_bytes = read_upload(job.product_image_path)
            new_image_url = image_gen.generate(
                caption=day1.caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day1-regen-{int(_time.time())}",
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                product_image_bytes=product_image_bytes,
                max_qc_retries=0,
            )
```

Por:

```python
    day1 = posts_by_day.get(1)
    if day1 and day1.image_url:
        try:
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            new_image_url = image_gen.generate(
                caption=day1.caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day1-regen-{int(_time.time())}",
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                max_qc_retries=0,
            )
```

- [ ] **Step 8: Actualizar el import de `views.py`**

`read_upload`/`upload_exists` ya no se usan en ningún lado de `views.py` tras los Steps 6-7 (`save_upload` sigue usándose para el logo). Reemplazar:

```python
from core.shared.gcs_uploads import save_upload, read_upload, upload_exists
```

Por:

```python
from core.shared.gcs_uploads import save_upload
```

- [ ] **Step 9: Quitar tests de las funciones helper eliminadas en `test_tasks.py`**

Eliminar la función completa `test_content_generation_disables_carousel_when_7_product_images`:

```python
def test_content_generation_disables_carousel_when_7_product_images(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks._load_product_images', return_value=[b'img'] * 7):
        MockText.return_value.generate.return_value = [dict(p) for p in _MOCK_POSTS_WITH_CAROUSEL]
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        MockImage.return_value.generate_carousel.return_value = ['https://storage.googleapis.com/test/slide1.jpg']

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    MockImage.return_value.generate_carousel.assert_not_called()
    assert MockImage.return_value.generate.call_count == 7
    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna)
    assert all(p.format == 'single' for p in posts)


```

Eliminar las 2 funciones completas de `_disable_carousel_if_full_product_week`:

```python
def test_disable_carousel_if_full_product_week_forces_single():
    from core.content_pipeline.tasks import _disable_carousel_if_full_product_week
    posts_data = [{'format': 'single'}] * 6 + [{'format': 'carousel'}]
    _disable_carousel_if_full_product_week(posts_data, [b'img'] * 7)
    assert all(p['format'] == 'single' for p in posts_data)


def test_disable_carousel_if_full_product_week_noop_with_fewer_than_7():
    from core.content_pipeline.tasks import _disable_carousel_if_full_product_week
    posts_data = [{'format': 'single'}] * 6 + [{'format': 'carousel'}]
    _disable_carousel_if_full_product_week(posts_data, [b'img'] * 3)
    assert posts_data[-1]['format'] == 'carousel'


```

Eliminar la función completa `test_content_generation_skips_reel_when_day1_has_product_photo`:

```python
def test_content_generation_skips_reel_when_day1_has_product_photo(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.ReelGenerator') as MockReel, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks._load_product_images', return_value=[b'foto-dia-1']):
        MockText.return_value.generate.return_value = [dict(p) for p in _MOCK_POSTS_WITH_REEL]
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    MockReel.return_value.generate.assert_not_called()
    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna)
    assert posts.get(day_number=1).format == 'single'


```

Eliminar la función completa `test_load_product_images_takes_paths_list` (junto con sus 3 decoradores `@patch`):

```python
@patch('core.content_pipeline.tasks.upload_exists', return_value=True)
@patch('core.content_pipeline.tasks.read_upload', return_value=b'fake-image-bytes')
@patch('core.content_pipeline.tasks.normalize_image', side_effect=lambda x: x)
def test_load_product_images_takes_paths_list(mock_normalize, mock_read, mock_exists, tmp_path, settings):
    from core.content_pipeline.tasks import _load_product_images
    result = _load_product_images(['uploads/product.webp'])
    assert result == [b'fake-image-bytes']


```

Eliminar la función completa `test_product_image_for_day_maps_day_in_week`:

```python
def test_product_image_for_day_maps_day_in_week():
    from core.content_pipeline.tasks import _product_image_for_day
    images = [b'img1', b'img2', b'img3']

    # Semana 1: day_in_week == day_number
    assert _product_image_for_day(1, images) == b'img1'
    assert _product_image_for_day(3, images) == b'img3'
    assert _product_image_for_day(4, images) is None

    # Semana 2, día 8 -> day_in_week 1 (mismo resultado que día 1 de semana 1)
    day_in_week = ((8 - 1) % 7) + 1
    assert day_in_week == 1
    assert _product_image_for_day(day_in_week, images) == _product_image_for_day(1, images)


```

Eliminar la función completa `test_content_generation_sets_active_product_images` (junto con su decorador `@override_settings`):

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
def test_content_generation_sets_active_product_images(job_with_dna):
    job_with_dna.product_image_paths = ['uploads/p1.jpg', 'uploads/p2.jpg']
    job_with_dna.save(update_fields=['product_image_paths'])

    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
    assert calendar.active_product_images == ['uploads/p1.jpg', 'uploads/p2.jpg']


```

- [ ] **Step 10: Correr la suite y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: todos los tests pasan. Ninguno menciona `_load_product_images`, `_product_image_for_day`, ni `_disable_carousel_if_full_product_week`. `test_content_generation_uses_reel_for_day_1_without_product_photo` sigue pasando sin cambios (no dependía de esas funciones).

- [ ] **Step 11: Commit**

```bash
git add core/content_pipeline/tasks.py core/brand_dna/views.py core/content_pipeline/tests/test_tasks.py
git commit -m "remove(product-photos): quitar carga y mapeo de fotos de producto en la generacion de contenido"
```

---

## Task 3: Núcleo de `image_generator.py`

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py`
- Test: `core/content_pipeline/tests/test_image_generator.py`

**Interfaces:**
- Consumes: nada de fotos de producto desde tasks.py/views.py (Tareas 1-2 ya no las pasan).
- Produces: `generate(caption, colors, tone, filename, brand_name='', keywords=None, description='', audience='', max_qc_retries=2, business_url='')`, `generate_carousel(caption, colors, tone, filename_prefix, brand_name='', keywords=None, description='', audience='', max_qc_retries=2, num_slides=4, business_url='')` — ninguno de los dos acepta ya `product_image_bytes`. `_generate_product_scene`, `_analyze_product_style`, `_bgswap_product`, `_generate_svg_overlay` dejan de existir.

- [ ] **Step 1: Simplificar `generate()`**

Reemplazar:

```python
    def generate(self, caption: str, colors: list[str], tone: str, filename: str, brand_name: str = '', keywords: list[str] = None, description: str = '', audience: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2, business_url: str = '') -> str:
        try:
            # job_id (sin el sufijo "-dayN") como seed de fuente — asi las 7 imagenes
            # de una semana comparten tipografia, incluso si se regenera un solo post.
            font_seed = filename.rsplit('-day', 1)[0] if '-day' in filename else filename
            image_bytes = self._layered_pipeline(caption, colors, tone, keywords or [], description, audience=audience, product_image_bytes=product_image_bytes, max_qc_retries=max_qc_retries, font_seed=font_seed, business_url=business_url)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''
```

Por:

```python
    def generate(self, caption: str, colors: list[str], tone: str, filename: str, brand_name: str = '', keywords: list[str] = None, description: str = '', audience: str = '', max_qc_retries: int = 2, business_url: str = '') -> str:
        try:
            # job_id (sin el sufijo "-dayN") como seed de fuente — asi las 7 imagenes
            # de una semana comparten tipografia, incluso si se regenera un solo post.
            font_seed = filename.rsplit('-day', 1)[0] if '-day' in filename else filename
            image_bytes = self._layered_pipeline(caption, colors, tone, keywords or [], description, audience=audience, max_qc_retries=max_qc_retries, font_seed=font_seed, business_url=business_url)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''
```

- [ ] **Step 2: Simplificar `generate_carousel()`**

Reemplazar:

```python
    def generate_carousel(self, caption: str, colors: list[str], tone: str, filename_prefix: str, brand_name: str = '', keywords: list[str] = None, description: str = '', audience: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2, num_slides: int = 4, business_url: str = '') -> list[str]:
        """Genera un carrusel de `num_slides` (H20 + roadmap #5). Reutiliza UN solo
        fondo (misma llamada a Imagen 3/BGSWAP que un post normal) y superpone
        contenido de texto DISTINTO por slide — evita multiplicar el costo de
        generacion de imagen por N mientras mantiene coherencia visual entre slides."""
        try:
            font_seed = filename_prefix.rsplit('-day', 1)[0] if '-day' in filename_prefix else filename_prefix
            kw_str = ', '.join((keywords or [])[:4])
            brand_ctx = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."

            if product_image_bytes:
                background_bytes, svg_overlay = self._generate_product_scene(
                    product_image_bytes, caption, colors, tone, max_qc_retries=max_qc_retries
                )
            else:
                background_bytes = self._generate_background(caption, colors, tone, keywords or [], description, audience=audience, max_qc_retries=max_qc_retries)
                svg_overlay = ''

            slides_content = self._generate_carousel_slides_content(caption, brand_ctx, num_slides=num_slides, business_url=business_url)

            urls = []
            for i, slide_content in enumerate(slides_content, start=1):
                image_bytes = self._render_html_template(background_bytes, slide_content, colors, svg_overlay=svg_overlay, font_seed=font_seed)
                urls.append(self._upload_to_storage(image_bytes, f"{filename_prefix}-slide{i}"))
            return urls
        except Exception as e:
            logger.error(f"ImageGenerator.generate_carousel error: {e}")
            return []
```

Por:

```python
    def generate_carousel(self, caption: str, colors: list[str], tone: str, filename_prefix: str, brand_name: str = '', keywords: list[str] = None, description: str = '', audience: str = '', max_qc_retries: int = 2, num_slides: int = 4, business_url: str = '') -> list[str]:
        """Genera un carrusel de `num_slides` (H20 + roadmap #5). Reutiliza UN solo
        fondo (misma llamada a Imagen 3) y superpone contenido de texto DISTINTO por
        slide — evita multiplicar el costo de generacion de imagen por N mientras
        mantiene coherencia visual entre slides."""
        try:
            font_seed = filename_prefix.rsplit('-day', 1)[0] if '-day' in filename_prefix else filename_prefix
            kw_str = ', '.join((keywords or [])[:4])
            brand_ctx = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."

            background_bytes = self._generate_background(caption, colors, tone, keywords or [], description, audience=audience, max_qc_retries=max_qc_retries)

            slides_content = self._generate_carousel_slides_content(caption, brand_ctx, num_slides=num_slides, business_url=business_url)

            urls = []
            for i, slide_content in enumerate(slides_content, start=1):
                image_bytes = self._render_html_template(background_bytes, slide_content, colors, svg_overlay='', font_seed=font_seed)
                urls.append(self._upload_to_storage(image_bytes, f"{filename_prefix}-slide{i}"))
            return urls
        except Exception as e:
            logger.error(f"ImageGenerator.generate_carousel error: {e}")
            return []
```

- [ ] **Step 3: Simplificar `_layered_pipeline()`**

Reemplazar:

```python
    def _layered_pipeline(self, caption: str, colors: list[str], tone: str, keywords: list[str] = None, description: str = '', audience: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2, font_seed: str = '', business_url: str = '') -> bytes:
        if product_image_bytes:
            kw_str = ', '.join((keywords or [])[:3])
            brand_context = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
            background_bytes, svg_overlay = self._generate_product_scene(
                product_image_bytes, caption, colors, tone, max_qc_retries=max_qc_retries
            )
            content = self._generate_post_content(caption, product_image_bytes=product_image_bytes, brand_context=brand_context, business_url=business_url)
            result = self._render_html_template(background_bytes, content, colors, svg_overlay=svg_overlay, font_seed=font_seed)
            if max_qc_retries > 0 and svg_overlay and not self._validate_final_image(result):
                logger.warning("Final QC falló — reintentando sin SVG overlay")
                result = self._render_html_template(background_bytes, content, colors, svg_overlay='', font_seed=font_seed)
            return result
        else:
            background_bytes = self._generate_background(caption, colors, tone, keywords or [], description, audience=audience, max_qc_retries=max_qc_retries)
            kw_str = ', '.join((keywords or [])[:4])
            brand_ctx = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
            content = self._generate_post_content(caption, product_image_bytes=None, brand_context=brand_ctx, business_url=business_url)
            svg_overlay = ''
        return self._render_html_template(background_bytes, content, colors, svg_overlay=svg_overlay, font_seed=font_seed)
```

Por:

```python
    def _layered_pipeline(self, caption: str, colors: list[str], tone: str, keywords: list[str] = None, description: str = '', audience: str = '', max_qc_retries: int = 2, font_seed: str = '', business_url: str = '') -> bytes:
        background_bytes = self._generate_background(caption, colors, tone, keywords or [], description, audience=audience, max_qc_retries=max_qc_retries)
        kw_str = ', '.join((keywords or [])[:4])
        brand_ctx = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
        content = self._generate_post_content(caption, brand_context=brand_ctx, business_url=business_url)
        return self._render_html_template(background_bytes, content, colors, svg_overlay='', font_seed=font_seed)
```

- [ ] **Step 4: Simplificar `_generate_post_content()`**

Reemplazar el método completo:

```python
    def _generate_post_content(self, caption: str, product_image_bytes: bytes = None, brand_context: str = '', business_url: str = '') -> dict:
        """Gemini generates {headline, subtitle, cta, tag}. Multimodal if product_image_bytes provided."""
        _FALLBACK = {
            'headline': self._extract_headline(caption),
            'subtitle': _truncate_at_word_boundary(caption.strip()) if caption else '',
            'cta': 'Contáctanos hoy',
            'tag': 'DESTACADO',
        }
        try:
            client = _vertex_client()
            if product_image_bytes:
                mime = _detect_mime(product_image_bytes)
                image_part = types.Part.from_bytes(data=product_image_bytes, mime_type=mime)
                prompt = (
                    f"ADN de marca: {brand_context}\n"
                    f"Caption del post (refleja la propuesta de la marca): \"{caption[:200]}\"\n\n"
                    "Hay una imagen adjunta que se usará como FONDO VISUAL del post.\n"
                    "Tu tarea: genera copy que comunique la propuesta de valor DE LA MARCA,\n"
                    "usando la imagen como contexto o punto de conexión — NO como tema principal.\n"
                    "Si la imagen conecta naturalmente con la marca, úsala. Si no, el copy habla de la marca\n"
                    "y el visual simplemente acompaña.\n\n"
                    "Genera 4 elementos:\n"
                    "1. headline: 3-5 palabras. Frase gancho que represente la marca. Sin nombres de marca.\n"
                    "2. subtitle: 8-15 palabras. Beneficio clave o propuesta de valor de la marca.\n"
                    "3. cta: 2-4 palabras. Llamada a la acción acorde a la marca.\n"
                    "4. tag: 1-3 palabras EN MAYÚSCULAS. Sector o categoría de la marca.\n\n"
                    "REGLAS: Español impecable. Sin inventar palabras.\n"
                    "Responde ÚNICAMENTE este JSON (sin markdown):\n"
                    "{\"headline\":\"...\",\"subtitle\":\"...\",\"cta\":\"...\",\"tag\":\"...\"}"
                )
                contents = [image_part, prompt]
            else:
                ctx_line = f"ADN de marca: {brand_context}\n" if brand_context else ""
                prompt = (
                    f"{ctx_line}"
                    f"Caption del post: \"{caption[:300]}\"\n\n"
                    "Genera el contenido para un post de Instagram con estos 4 elementos:\n"
                    "1. headline: 3-5 palabras. Frase gancho, memorable. Sin nombres de marca, URLs, hashtags.\n"
                    "2. subtitle: 8-15 palabras. Amplía el headline con el beneficio clave. Español correcto.\n"
                    "3. cta: 2-4 palabras. Llamada a la acción directa. (Ej: 'Empieza hoy', 'Solicita tu demo')\n"
                    "4. tag: 1-3 palabras EN MAYÚSCULAS. Categoría del sector. (Ej: 'DISEÑO WEB', 'NUTRICIÓN')\n\n"
                    "REGLAS: Español impecable. Sin inventar palabras. Sin duplicar letras.\n"
                    "Responde ÚNICAMENTE este JSON (sin markdown):\n"
                    "{\"headline\":\"...\",\"subtitle\":\"...\",\"cta\":\"...\",\"tag\":\"...\"}"
                )
                contents = prompt
            def _call():
                with track_external_api('gemini', operation='post_content'):
                    return client.models.generate_content(
                        model=settings.VERTEX_TEXT_MODEL,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=(
                                "Eres 'Cosmic', Director Creativo de Agente Cosmic. "
                                "Generas contenido de marketing para redes sociales. "
                                "Español impecable. Cero errores ortográficos. Nunca inventes palabras. "
                                "Frases para imagen: cortas, impactantes, máximo 5 palabras. "
                                "Regla de seguridad (siempre aplica): si la marca pertenece a un nicho "
                                "sensible (niños, salud, medicina, finanzas, crédito, temas legales), usa "
                                "tono neutro-positivo, evita promesas absolutas y evita lenguaje retador "
                                "o de urgencia con audiencias vulnerables. PROHIBIDO usar las palabras/frases: "
                                "'garantizado', 'garantizamos', 'asegurar', 'aseguramos', 'asegurando', "
                                "'resultados 100% seguros', 'nunca falla', 'sin riesgo'."
                            ),
                        ),
                    )
            resp = call_with_429_retry(_call, settings.VERTEX_TEXT_MODEL)
            record_tokens(resp, operation='post_content',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return {
                    'headline': _sanitize_web_visit_mention(
                        str(data.get('headline', '')).strip() or _FALLBACK['headline'],
                        business_url, self._extract_headline(caption),
                    ),
                    'subtitle': _sanitize_web_visit_mention(
                        str(data.get('subtitle', '')).strip() or _FALLBACK['subtitle'],
                        business_url, _truncate_at_word_boundary(caption.strip()) if caption else '',
                    ),
                    'cta': _sanitize_web_visit_mention(
                        str(data.get('cta', '')).strip() or _FALLBACK['cta'],
                        business_url, 'Contáctanos hoy',
                    ),
                    'tag': str(data.get('tag', '')).strip().upper() or _FALLBACK['tag'],
                }
        except Exception as e:
            logger.warning(f"Post content generation failed, using fallback: {e}")
        return _FALLBACK
```

Por:

```python
    def _generate_post_content(self, caption: str, brand_context: str = '', business_url: str = '') -> dict:
        """Gemini generates {headline, subtitle, cta, tag}."""
        _FALLBACK = {
            'headline': self._extract_headline(caption),
            'subtitle': _truncate_at_word_boundary(caption.strip()) if caption else '',
            'cta': 'Contáctanos hoy',
            'tag': 'DESTACADO',
        }
        try:
            client = _vertex_client()
            ctx_line = f"ADN de marca: {brand_context}\n" if brand_context else ""
            prompt = (
                f"{ctx_line}"
                f"Caption del post: \"{caption[:300]}\"\n\n"
                "Genera el contenido para un post de Instagram con estos 4 elementos:\n"
                "1. headline: 3-5 palabras. Frase gancho, memorable. Sin nombres de marca, URLs, hashtags.\n"
                "2. subtitle: 8-15 palabras. Amplía el headline con el beneficio clave. Español correcto.\n"
                "3. cta: 2-4 palabras. Llamada a la acción directa. (Ej: 'Empieza hoy', 'Solicita tu demo')\n"
                "4. tag: 1-3 palabras EN MAYÚSCULAS. Categoría del sector. (Ej: 'DISEÑO WEB', 'NUTRICIÓN')\n\n"
                "REGLAS: Español impecable. Sin inventar palabras. Sin duplicar letras.\n"
                "Responde ÚNICAMENTE este JSON (sin markdown):\n"
                "{\"headline\":\"...\",\"subtitle\":\"...\",\"cta\":\"...\",\"tag\":\"...\"}"
            )
            def _call():
                with track_external_api('gemini', operation='post_content'):
                    return client.models.generate_content(
                        model=settings.VERTEX_TEXT_MODEL,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=(
                                "Eres 'Cosmic', Director Creativo de Agente Cosmic. "
                                "Generas contenido de marketing para redes sociales. "
                                "Español impecable. Cero errores ortográficos. Nunca inventes palabras. "
                                "Frases para imagen: cortas, impactantes, máximo 5 palabras. "
                                "Regla de seguridad (siempre aplica): si la marca pertenece a un nicho "
                                "sensible (niños, salud, medicina, finanzas, crédito, temas legales), usa "
                                "tono neutro-positivo, evita promesas absolutas y evita lenguaje retador "
                                "o de urgencia con audiencias vulnerables. PROHIBIDO usar las palabras/frases: "
                                "'garantizado', 'garantizamos', 'asegurar', 'aseguramos', 'asegurando', "
                                "'resultados 100% seguros', 'nunca falla', 'sin riesgo'."
                            ),
                        ),
                    )
            resp = call_with_429_retry(_call, settings.VERTEX_TEXT_MODEL)
            record_tokens(resp, operation='post_content',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return {
                    'headline': _sanitize_web_visit_mention(
                        str(data.get('headline', '')).strip() or _FALLBACK['headline'],
                        business_url, self._extract_headline(caption),
                    ),
                    'subtitle': _sanitize_web_visit_mention(
                        str(data.get('subtitle', '')).strip() or _FALLBACK['subtitle'],
                        business_url, _truncate_at_word_boundary(caption.strip()) if caption else '',
                    ),
                    'cta': _sanitize_web_visit_mention(
                        str(data.get('cta', '')).strip() or _FALLBACK['cta'],
                        business_url, 'Contáctanos hoy',
                    ),
                    'tag': str(data.get('tag', '')).strip().upper() or _FALLBACK['tag'],
                }
        except Exception as e:
            logger.warning(f"Post content generation failed, using fallback: {e}")
        return _FALLBACK
```

- [ ] **Step 5: Borrar `_generate_product_scene`, `_analyze_product_style`, `_bgswap_product` y `_generate_svg_overlay`**

Estos 4 métodos son el pipeline BGSWAP completo — `_generate_svg_overlay` ("Gemini Iluminador") existía únicamente para armonizar fotos de producto compuestas por BGSWAP; tras borrar `_generate_product_scene` (su único llamador) queda inalcanzable. Eliminar el bloque completo, desde `def _generate_product_scene` hasta el final de `_bgswap_product`, y por separado el bloque de `_generate_svg_overlay` (quedan entre `_generate_background`... revisa: en el archivo actual el orden es `_generate_product_scene` → `_analyze_product_style` → `_bgswap_product` → `_generate_svg_overlay` → `_SCENE_FALLBACKS`. Los 4 métodos son consecutivos):

```python
    def _generate_product_scene(self, product_image_bytes: bytes, caption: str, colors: list[str], tone: str, max_qc_retries: int = 2) -> tuple[bytes, str]:
        """Pipeline agéntico de 3 pasos con QC en la escena generada:
        1. Gemini Director de Arte → prompt de entorno premium específico para este producto
        2. Imagen 3 BGSWAP → producto pixel-perfect sobre ese entorno (con reintento si QC falla)
        3. Gemini Iluminador → SVG overlay de sombra/luz para armonizar (solo si BGSWAP tuvo éxito)
        """
        env_prompt = self._analyze_product_style(product_image_bytes, caption, colors, tone)
        total_attempts = max_qc_retries + 1
        scene_bytes, bgswap_ok = product_image_bytes, False
        for attempt in range(total_attempts):
            candidate_bytes, candidate_ok = self._bgswap_product(product_image_bytes, env_prompt)
            if not candidate_ok:
                scene_bytes, bgswap_ok = product_image_bytes, False
                break
            if max_qc_retries == 0 or self._validate_background(candidate_bytes):
                scene_bytes, bgswap_ok = candidate_bytes, True
                break
            if attempt < max_qc_retries:
                logger.warning(f"Scene QC falló (intento {attempt + 1}/{total_attempts}), reintentando BGSWAP...")
            else:
                logger.warning("Scene QC: reintentos agotados, usando última escena generada")
                scene_bytes, bgswap_ok = candidate_bytes, True
        svg_overlay = self._generate_svg_overlay(scene_bytes, colors) if bgswap_ok else ''
        return scene_bytes, svg_overlay

    def _analyze_product_style(self, product_image_bytes: bytes, caption: str, colors: list[str], tone: str) -> str:
        """Gemini Director de Arte: analiza el producto y genera prompt de entorno premium para Imagen 3."""
        color_str = ', '.join(colors[:3]) if colors else 'warm neutrals'
        _FALLBACK = (
            f"Professional editorial product photography background. "
            f"Elegant real-world environment: wooden surface, marble, or lifestyle context. "
            f"Natural lighting, shallow depth of field, warm bokeh. Mood: {tone}. "
            f"NOT white background. NOT abstract. NOT 3D render. Absolutely NO text, NO logos."
        )
        try:
            client = _vertex_client()
            mime = _detect_mime(product_image_bytes)
            image_part = types.Part.from_bytes(data=product_image_bytes, mime_type=mime)
            prompt = (
                f"You are an Art Director for premium brand advertising.\n"
                f"Analyze this product image and generate a specific Imagen 3 prompt (max 100 words) "
                f"for the BACKGROUND ENVIRONMENT ONLY — where this product would look spectacular.\n"
                f"Brand context: {caption[:80]}. Color palette: {color_str}. Mood: {tone}.\n\n"
                f"Describe: surface/pedestal/setting, lighting style, atmosphere, complementary textures.\n"
                f"Do NOT mention the product itself — only the environment that showcases it.\n"
                f"End with: 'NOT abstract. NOT 3D render. Absolutely NO text, NO logos.'\n"
                f"Return ONLY the prompt text, no explanations."
            )
            with track_external_api('gemini', operation='image_product'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                )
            record_tokens(resp, operation='image_product',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            result = resp.text.strip().strip('"').strip("'")
            if result:
                logger.info(f"Art Director env prompt: {result[:100]}...")
                return result
        except Exception as e:
            logger.warning(f"Product style analysis failed (using fallback): {e}")
        return _FALLBACK

    def _bgswap_product(self, product_image_bytes: bytes, environment_prompt: str) -> tuple[bytes, bool]:
        """Imagen 3 BGSWAP: mantiene el producto exacto y reemplaza el fondo con el entorno del Director de Arte.
        Retorna (image_bytes, success). MASK_MODE_BACKGROUND para que Imagen 3 detecte el fondo automáticamente.
        """
        mime = _detect_mime(product_image_bytes)

        def _call_edit_image():
            client = _vertex_client()
            with track_external_api('imagen3', operation='bgswap'):
                return client.models.edit_image(
                    model=settings.VERTEX_IMAGE_EDIT_MODEL,
                    prompt=environment_prompt,
                    reference_images=[
                        types.RawReferenceImage(
                            reference_image=types.Image(image_bytes=product_image_bytes, mime_type=mime),
                            reference_id=1,
                        ),
                        types.MaskReferenceImage(
                            reference_id=2,
                            config=types.MaskReferenceConfig(
                                mask_mode=types.MaskReferenceMode.MASK_MODE_BACKGROUND,
                            ),
                        ),
                    ],
                    config=types.EditImageConfig(
                        edit_mode=types.EditMode.EDIT_MODE_BGSWAP,
                        number_of_images=1,
                        aspect_ratio='1:1',
                    ),
                )

        try:
            resp = call_with_429_retry(_call_edit_image, settings.VERTEX_IMAGE_EDIT_MODEL)
            if resp.generated_images:
                record_imagen_generation('bgswap')
                logger.info("BGSWAP exitoso — producto sobre entorno premium")
                return resp.generated_images[0].image.image_bytes, True
            logger.warning("BGSWAP sin imágenes, usando foto original")
        except Exception as e:
            logger.warning(f"BGSWAP fallido (usando foto original): {e}")
        return product_image_bytes, False

    def _generate_svg_overlay(self, image_bytes: bytes, colors: list[str]) -> str:
        """Gemini Iluminador: genera SVG de sombra/luz para armonizar el producto con el nuevo fondo."""
        try:
            client = _vertex_client()
            mime = _detect_mime(image_bytes)
            image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime)
            primary = colors[0] if colors else '#ffffff'
            prompt = (
                f"Analyze this product advertising image. Generate an SVG transparent overlay (1080x1080) that:\n"
                f"1. Adds ONLY a gentle ambient light gradient matching the scene's dominant light direction\n"
                f"2. Applies a very soft color wash using {primary} at opacity 0.04-0.06 to harmonize\n\n"
                f"Rules:\n"
                f"- Use ONLY: <defs>, <rect>, <radialGradient>, <linearGradient> elements\n"
                f"- NO shadow ellipses, NO dark blobs, NO ellipse elements\n"
                f"- All fills must use opacity 0.10 or lower — barely visible, purely atmospheric\n"
                f"- No solid opaque fills. SVG root has no background-color.\n"
                f"- Return ONLY valid SVG starting with <svg and ending with </svg>. No markdown."
            )
            with track_external_api('gemini', operation='svg_overlay'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                )
            record_tokens(resp, operation='svg_overlay',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            raw = resp.text.strip()
            svg_match = re.search(r'<svg[\s\S]*?</svg>', raw, re.DOTALL)
            if svg_match:
                logger.info("SVG lighting overlay generado")
                return svg_match.group()
        except Exception as e:
            logger.warning(f"SVG overlay fallido (omitiendo): {e}")
        return ''

```

No dejar nada en su lugar — tras borrar el bloque, `_SCENE_FALLBACKS = [...]` queda como la siguiente definición en el archivo, inmediatamente después de `_layered_pipeline` (que ya quedó simplificado en el Step 3).

- [ ] **Step 6: Borrar las clases de test que cubrían el pipeline BGSWAP en `test_image_generator.py`**

Eliminar la clase completa `TestGeneratePostContentWithProduct`:

```python
class TestGeneratePostContentWithProduct:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_multimodal_call_when_product_image_provided(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_image = _png_bytes()
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"Brilla distinto","subtitle":"Plata 925 hecha a mano para ti","cta":"Cómpralo ahora","tag":"JOYERÍA"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Collar artesanal de plata', product_image_bytes=fake_image)
        call_args = mock_vc.return_value.models.generate_content.call_args
        contents = call_args.kwargs.get('contents') or (call_args.args[1] if len(call_args.args) > 1 else None)
        assert isinstance(contents, list), "Multimodal call must pass contents as list [image_part, prompt]"
        assert result['headline'] == 'Brilla distinto'
        assert result['tag'] == 'JOYERÍA'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_text_only_call_when_no_product_image(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"Impulsa tu negocio","subtitle":"Tecnología que funciona para ti","cta":"Empieza hoy","tag":"TECNOLOGÍA"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Soluciones tecnológicas', product_image_bytes=None)
        call_args = mock_vc.return_value.models.generate_content.call_args
        contents = call_args.kwargs.get('contents') or (call_args.args[1] if len(call_args.args) > 1 else None)
        assert isinstance(contents, str), "Text-only call must pass contents as string"
        assert result['headline'] == 'Impulsa tu negocio'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_brand_context_included_in_multimodal_prompt(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_image = _png_bytes()
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"Tu marca brilla","subtitle":"Identidad que vende","cta":"Hablemos","tag":"BRANDING"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._generate_post_content(
                'Post sobre branding',
                product_image_bytes=fake_image,
                brand_context='Agencia de branding. Tono: profesional.',
            )
        call_args = mock_vc.return_value.models.generate_content.call_args
        contents = call_args.kwargs.get('contents') or (call_args.args[1] if len(call_args.args) > 1 else None)
        assert isinstance(contents, list)
        prompt_text = contents[1]  # second element is the text prompt
        assert 'Agencia de branding' in prompt_text, "brand_context must appear in the multimodal prompt"


```

En `TestLayeredPipelineWithProduct`, mover `test_no_product_uses_imagen3_flow` a la clase `TestLayeredPipeline` (ver Step 7) y eliminar el resto de la clase (`test_product_path_calls_generate_product_scene`, `test_final_qc_fail_rerenders_without_svg`, `test_final_qc_skipped_when_max_qc_retries_zero`) junto con la declaración `class TestLayeredPipelineWithProduct:`:

```python
class TestLayeredPipelineWithProduct:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_product_path_calls_generate_product_scene(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes((200, 150, 100))
        scene_img = _png_bytes((100, 180, 140))
        fake_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080"></svg>'
        fake_content = {'headline': 'Brilla distinto', 'subtitle': 'Plata artesanal', 'cta': 'Cómpralo', 'tag': 'JOYERÍA'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_product_scene', return_value=(scene_img, fake_svg)) as mock_scene, \
             patch.object(gen, '_generate_background') as mock_bg, \
             patch.object(gen, '_generate_post_content', return_value=fake_content) as mock_content, \
             patch.object(gen, '_render_html_template', return_value=fake_shot) as mock_render, \
             patch.object(gen, '_validate_final_image', return_value=True):
            result = gen._layered_pipeline('Collar artesanal', ['#c0c0c0'], 'elegante', product_image_bytes=product_img)

        mock_scene.assert_called_once_with(product_img, 'Collar artesanal', ['#c0c0c0'], 'elegante', max_qc_retries=2)
        mock_bg.assert_not_called()
        mock_content.assert_called_once()
        call_kwargs = mock_content.call_args.kwargs
        assert call_kwargs['product_image_bytes'] == product_img
        assert 'brand_context' in call_kwargs and len(call_kwargs['brand_context']) > 0
        mock_render.assert_called_once_with(scene_img, fake_content, ['#c0c0c0'], svg_overlay=fake_svg, font_seed='')
        assert result == fake_shot

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_final_qc_fail_rerenders_without_svg(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes((200, 150, 100))
        scene_img = _png_bytes((100, 180, 140))
        fake_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080"></svg>'
        fake_content = {'headline': 'Test', 'subtitle': 'Sub', 'cta': 'CTA', 'tag': 'TAG'}
        render_with_svg = _png_bytes((200, 50, 50), size=(1080, 1080))
        render_no_svg = _png_bytes((50, 200, 50), size=(1080, 1080))

        with patch.object(gen, '_generate_product_scene', return_value=(scene_img, fake_svg)), \
             patch.object(gen, '_generate_post_content', return_value=fake_content), \
             patch.object(gen, '_render_html_template', side_effect=[render_with_svg, render_no_svg]) as mock_render, \
             patch.object(gen, '_validate_final_image', return_value=False):
            result = gen._layered_pipeline('Caption', ['#c0c0c0'], 'elegante', product_image_bytes=product_img)

        assert mock_render.call_count == 2
        second_call = mock_render.call_args_list[1]
        assert second_call.kwargs.get('svg_overlay') == '' or second_call.args[-1] == ''
        assert result == render_no_svg

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_final_qc_skipped_when_max_qc_retries_zero(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes()
        fake_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080"></svg>'
        fake_content = {'headline': 'Test', 'subtitle': 'Sub', 'cta': 'CTA', 'tag': 'TAG'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_product_scene', return_value=(_png_bytes(), fake_svg)), \
             patch.object(gen, '_generate_post_content', return_value=fake_content), \
             patch.object(gen, '_render_html_template', return_value=fake_shot), \
             patch.object(gen, '_validate_final_image') as mock_qc:
            gen._layered_pipeline('Caption', ['#c0c0c0'], 'elegante', product_image_bytes=product_img, max_qc_retries=0)

        mock_qc.assert_not_called()  # QC disabled para UI (max_qc_retries=0)

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_no_product_uses_imagen3_flow(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes((30, 30, 60))
        fake_content = {'headline': 'Hola mundo', 'subtitle': 'Mundo', 'cta': 'Ver', 'tag': 'TEST'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_background', return_value=fake_bg) as mock_bg, \
             patch.object(gen, '_generate_post_content', return_value=fake_content) as mock_content, \
             patch.object(gen, '_render_html_template', return_value=fake_shot):
            gen._layered_pipeline('Caption', ['#1a1a2e'], 'profesional', product_image_bytes=None)

        mock_bg.assert_called_once()
        mock_content.assert_called_once_with('Caption', product_image_bytes=None, brand_context='Tono: profesional.', business_url='')


```

Eliminar las clases completas `TestGenerateProductScene` y `TestBgswapProduct`:

```python
class TestGenerateProductScene:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_scene_and_svg_tuple(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes((200, 150, 100))
        scene_img = _png_bytes((100, 180, 140))
        fake_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080"></svg>'

        with patch.object(gen, '_analyze_product_style', return_value='premium env prompt'), \
             patch.object(gen, '_bgswap_product', return_value=(scene_img, True)), \
             patch.object(gen, '_validate_background', return_value=True), \
             patch.object(gen, '_generate_svg_overlay', return_value=fake_svg):
            result = gen._generate_product_scene(product_img, 'Collar artesanal', ['#c0c0c0'], 'elegante')

        assert isinstance(result, tuple) and len(result) == 2
        assert result[0] == scene_img
        assert result[1] == fake_svg

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_bgswap_fallback_skips_svg_overlay(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes()

        with patch.object(gen, '_analyze_product_style', return_value='env prompt'), \
             patch.object(gen, '_bgswap_product', return_value=(product_img, False)), \
             patch.object(gen, '_generate_svg_overlay') as mock_svg:
            scene_bytes, svg = gen._generate_product_scene(product_img, 'Caption', [], 'pro')

        mock_svg.assert_not_called()  # SVG no se genera si BGSWAP falló
        assert scene_bytes == product_img
        assert svg == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_scene_qc_retries_bgswap_on_fail(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes((200, 150, 100))
        bad_scene = _png_bytes((200, 50, 50))
        good_scene = _png_bytes((50, 200, 50))

        with patch.object(gen, '_analyze_product_style', return_value='env prompt'), \
             patch.object(gen, '_bgswap_product', side_effect=[(bad_scene, True), (good_scene, True)]) as mock_bgswap, \
             patch.object(gen, '_validate_background', side_effect=[False, True]), \
             patch.object(gen, '_generate_svg_overlay', return_value=''):
            scene_bytes, _ = gen._generate_product_scene(product_img, 'Caption', [], 'pro', max_qc_retries=2)

        assert mock_bgswap.call_count == 2  # reintentó BGSWAP al fallar QC
        assert scene_bytes == good_scene

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_scene_qc_skipped_when_max_qc_retries_zero(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes()
        scene_img = _png_bytes((100, 180, 140))

        with patch.object(gen, '_analyze_product_style', return_value='env prompt'), \
             patch.object(gen, '_bgswap_product', return_value=(scene_img, True)), \
             patch.object(gen, '_validate_background') as mock_validate, \
             patch.object(gen, '_generate_svg_overlay', return_value=''):
            gen._generate_product_scene(product_img, 'Caption', [], 'pro', max_qc_retries=0)

        mock_validate.assert_not_called()  # QC desactivado para UI


class TestBgswapProduct:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
    )
    def test_returns_scene_bytes_on_success(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes((200, 150, 100))
        scene_img = _png_bytes((100, 180, 140))
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_generated = MagicMock()
            mock_generated.image.image_bytes = scene_img
            mock_vc.return_value.models.edit_image.return_value.generated_images = [mock_generated]
            result_bytes, ok = gen._bgswap_product(product_img, 'luxury marble pedestal, warm lighting')
        assert result_bytes == scene_img
        assert ok is True
        call_kwargs = mock_vc.return_value.models.edit_image.call_args.kwargs
        from google.genai.types import EditMode, MaskReferenceMode
        assert call_kwargs['config'].edit_mode == EditMode.EDIT_MODE_BGSWAP
        assert len(call_kwargs['reference_images']) == 2  # RawReferenceImage + MaskReferenceImage

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_EDIT_MODEL='imagen-3.0-capability-001',
    )
    def test_falls_back_to_product_on_api_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes()
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.edit_image.side_effect = Exception('API error')
            result_bytes, ok = gen._bgswap_product(product_img, 'some prompt')
        assert result_bytes == product_img
        assert ok is False


```

Eliminar la clase completa `TestGenerateSvgOverlay`:

```python
class TestGenerateSvgOverlay:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_svg_string_on_success(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080"><ellipse cx="540" cy="900" rx="200" ry="30" fill="black" opacity="0.18"/></svg>'
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = fake_svg
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_svg_overlay(_png_bytes(), ['#c0c0c0'])
        assert result.startswith('<svg')
        assert result.endswith('</svg>')

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_empty_string_on_api_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._generate_svg_overlay(_png_bytes(), [])
        assert result == ''


```

- [ ] **Step 7: Agregar la versión adaptada de `test_no_product_uses_imagen3_flow` al final de `TestLayeredPipeline`**

En la clase `TestLayeredPipeline` (la que ya existe, con `test_pipeline_calls_render_html_template` y `test_pipeline_propagates_render_error`), agregar este método al final de la clase:

```python

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_generates_brand_context_and_calls_post_content(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes((30, 30, 60))
        fake_content = {'headline': 'Hola mundo', 'subtitle': 'Mundo', 'cta': 'Ver', 'tag': 'TEST'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_background', return_value=fake_bg) as mock_bg, \
             patch.object(gen, '_generate_post_content', return_value=fake_content) as mock_content, \
             patch.object(gen, '_render_html_template', return_value=fake_shot):
            gen._layered_pipeline('Caption', ['#1a1a2e'], 'profesional')

        mock_bg.assert_called_once()
        mock_content.assert_called_once_with('Caption', brand_context='Tono: profesional.', business_url='')
```

- [ ] **Step 8: Borrar el test de carrusel con foto de producto**

Eliminar el método completo `test_uses_product_scene_when_product_image_provided` de la clase `TestGenerateCarousel` (los otros métodos de esa clase se quedan):

```python
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_uses_product_scene_when_product_image_provided(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        fake_slides = [{'headline': 'H', 'subtitle': 'S', 'cta': 'CTA', 'tag': 'TAG'}] * 3
        product_img = _png_bytes((200, 50, 50))
        with patch.object(gen, '_generate_product_scene', return_value=(fake_bg, '<svg></svg>')) as mock_scene, \
             patch.object(gen, '_generate_background') as mock_no_product_bg, \
             patch.object(gen, '_generate_carousel_slides_content', return_value=fake_slides), \
             patch.object(gen, '_render_html_template', return_value=fake_shot), \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/slide.png'):
            gen.generate_carousel('Caption', ['#1a1a2e'], 'profesional', 'job1-day3', product_image_bytes=product_img, num_slides=3)
        mock_scene.assert_called_once()
        mock_no_product_bg.assert_not_called()

```

- [ ] **Step 9: Correr la suite y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py -v`
Expected: todos los tests pasan. Ninguno menciona `_generate_product_scene`, `_analyze_product_style`, `_bgswap_product`, `_generate_svg_overlay` ni `product_image_bytes`.

- [ ] **Step 10: Commit**

```bash
git add core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_image_generator.py
git commit -m "remove(product-photos): eliminar pipeline BGSWAP de image_generator.py"
```

---

## Task 4: Modelo, migraciones, limpieza y copy legal

**Files:**
- Modify: `core/brand_dna/models.py`
- Create: `core/brand_dna/migrations/0007_remove_analysisjob_product_image_fields.py`
- Modify: `core/content_pipeline/models.py`
- Create: `core/content_pipeline/migrations/0011_remove_contentcalendar_active_product_images.py`
- Modify: `core/tenant_management/management/commands/cleanup_deactivated_images.py`
- Modify: `core/brand_dna/templates/brand_dna/legal/privacy.html`
- Test: `core/content_pipeline/tests/test_models.py`
- Test: `core/content_pipeline/tests/test_tasks.py`
- Test: `core/tenant_management/tests/test_cleanup_command.py`

**Interfaces:**
- Consumes: nada del código de negocio referencia ya `product_image_path`, `product_image_paths` ni `active_product_images` (Tareas 1-3 ya los dejaron de usar).
- Produces: N/A — última tarea del plan.

- [ ] **Step 1: Quitar los campos de `AnalysisJob`**

En `core/brand_dna/models.py`, eliminar estas 2 líneas de la clase `AnalysisJob`:

```python
    product_image_path = models.CharField(max_length=500, blank=True, default='')
    product_image_paths = models.JSONField(default=list, blank=True)
```

- [ ] **Step 2: Crear la migración de `brand_dna`**

Crear `core/brand_dna/migrations/0007_remove_analysisjob_product_image_fields.py`:

```python
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('brand_dna', '0006_add_business_description'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='analysisjob',
            name='product_image_path',
        ),
        migrations.RemoveField(
            model_name='analysisjob',
            name='product_image_paths',
        ),
    ]
```

- [ ] **Step 3: Quitar el campo de `ContentCalendar`**

En `core/content_pipeline/models.py`, eliminar esta línea de la clase `ContentCalendar`:

```python
    active_product_images = models.JSONField(default=list, blank=True)
```

- [ ] **Step 4: Crear la migración de `content_pipeline`**

Crear `core/content_pipeline/migrations/0011_remove_contentcalendar_active_product_images.py`:

```python
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('content_pipeline', '0010_contentpost_video_url_alter_contentpost_format'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='contentcalendar',
            name='active_product_images',
        ),
    ]
```

- [ ] **Step 5: Aplicar las migraciones**

```
docker compose exec -T backend python manage.py migrate brand_dna
docker compose exec -T backend python manage.py migrate content_pipeline
```
Esperado: `Applying brand_dna.0007_remove_analysisjob_product_image_fields... OK` y `Applying content_pipeline.0011_remove_contentcalendar_active_product_images... OK`

```
docker compose exec -T backend python manage.py makemigrations --check --dry-run
```
Esperado: exit code 0, sin migraciones faltantes.

- [ ] **Step 6: Simplificar `cleanup_deactivated_images.py`**

Reemplazar el archivo completo:

```python
import logging
import os
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from google.cloud import storage
from core.tenant_management.models import User
from core.brand_dna.models import AnalysisJob

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Delete GCS images and local files for users deactivated more than 30 days ago'

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=30)
        users = User.objects.filter(
            is_active=False,
            deactivated_at__isnull=False,
            deactivated_at__lt=cutoff,
        )

        if not users.exists():
            self.stdout.write('No users to clean up.')
            return

        bucket_name = settings.GOOGLE_CLOUD_STORAGE_BUCKET
        gcs_client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        bucket = gcs_client.bucket(bucket_name)

        total_blobs = 0
        total_jobs = 0

        for user in users:
            jobs = AnalysisJob.objects.filter(user=user)
            for job in jobs:
                blobs = list(bucket.list_blobs(prefix=f'posts/{job.id}-'))
                for blob in blobs:
                    blob.delete()
                    total_blobs += 1

                if job.logo_file_path:
                    full = os.path.join(settings.MEDIA_ROOT, job.logo_file_path)
                    if os.path.exists(full):
                        os.remove(full)

                for path in (job.post_images_paths or []):
                    full = os.path.join(settings.MEDIA_ROOT, path)
                    if os.path.exists(full):
                        os.remove(full)

                job.logo_file_path = ''
                job.post_images_paths = []
                job.save(update_fields=['logo_file_path', 'post_images_paths'])
                total_jobs += 1

            logger.info(f'Cleaned images for user {user.email}')

        self.stdout.write(
            f'Cleanup complete: {users.count()} users, {total_jobs} jobs, {total_blobs} GCS blobs deleted.'
        )
```

- [ ] **Step 7: Actualizar `privacy.html`**

Reemplazar:

```html
      <li><strong>Datos de tu negocio:</strong> nombre, descripción, audiencia, tono de
        comunicación, palabras clave, colores de marca, URL de tu sitio web (opcional),
        y cualquier logo o foto de producto que subas.</li>
```

Por:

```html
      <li><strong>Datos de tu negocio:</strong> nombre, descripción, audiencia, tono de
        comunicación, palabras clave, colores de marca, URL de tu sitio web (opcional),
        y cualquier logo que subas.</li>
```

- [ ] **Step 8: Quitar el test del campo eliminado en `test_models.py`**

Eliminar la función completa:

```python
def test_content_calendar_active_product_images_default(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    assert calendar.active_product_images == []


```

- [ ] **Step 9: Quitar `active_product_images=[]` de los fixtures en `test_tasks.py`**

Este bloque aparece idéntico 3 veces (en `test_generate_next_week_creates_posts_for_week_2`, `test_generate_next_week_does_not_collide_with_last_post_date`, `test_generate_next_week_resets_flag_even_on_failure`). Reemplazar cada una de las 3 ocurrencias de:

```python
    calendar = ContentCalendar.objects.create(
        brand_dna=job_with_dna.brand_dna, active_product_images=[], next_week_generating=True,
    )
```

Por:

```python
    calendar = ContentCalendar.objects.create(
        brand_dna=job_with_dna.brand_dna, next_week_generating=True,
    )
```

- [ ] **Step 10: Quitar `product_image_paths` de `test_cleanup_command.py`**

Reemplazar:

```python
def test_cleanup_deletes_old_user_images(deactivated_user_old):
    job = AnalysisJob.objects.create(
        email=deactivated_user_old.email, business_url='https://test.com',
        user=deactivated_user_old, status='done',
        logo_file_path='uploads/logo_test.jpg',
        product_image_paths=['uploads/p1.jpg', 'uploads/p2.jpg'],
    )

    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_bucket.list_blobs.return_value = [mock_blob]

    with patch('core.tenant_management.management.commands.cleanup_deactivated_images.storage.Client') as mock_client:
        mock_client.return_value.bucket.return_value = mock_bucket
        call_command('cleanup_deactivated_images')

    job.refresh_from_db()
    assert job.logo_file_path == ''
    assert job.product_image_paths == []


def test_cleanup_skips_recent_deactivation(deactivated_user_recent):
    job = AnalysisJob.objects.create(
        email=deactivated_user_recent.email, business_url='https://test.com',
        user=deactivated_user_recent, status='done',
        logo_file_path='uploads/logo_test.jpg',
        product_image_paths=['uploads/p1.jpg'],
    )

    with patch('core.tenant_management.management.commands.cleanup_deactivated_images.storage.Client'):
        call_command('cleanup_deactivated_images')

    job.refresh_from_db()
    assert job.logo_file_path == 'uploads/logo_test.jpg'
    assert job.product_image_paths == ['uploads/p1.jpg']
```

Por:

```python
def test_cleanup_deletes_old_user_images(deactivated_user_old):
    job = AnalysisJob.objects.create(
        email=deactivated_user_old.email, business_url='https://test.com',
        user=deactivated_user_old, status='done',
        logo_file_path='uploads/logo_test.jpg',
    )

    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_bucket.list_blobs.return_value = [mock_blob]

    with patch('core.tenant_management.management.commands.cleanup_deactivated_images.storage.Client') as mock_client:
        mock_client.return_value.bucket.return_value = mock_bucket
        call_command('cleanup_deactivated_images')

    job.refresh_from_db()
    assert job.logo_file_path == ''


def test_cleanup_skips_recent_deactivation(deactivated_user_recent):
    job = AnalysisJob.objects.create(
        email=deactivated_user_recent.email, business_url='https://test.com',
        user=deactivated_user_recent, status='done',
        logo_file_path='uploads/logo_test.jpg',
    )

    with patch('core.tenant_management.management.commands.cleanup_deactivated_images.storage.Client'):
        call_command('cleanup_deactivated_images')

    job.refresh_from_db()
    assert job.logo_file_path == 'uploads/logo_test.jpg'
```

- [ ] **Step 11: Correr las 3 suites y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_models.py core/content_pipeline/tests/test_tasks.py core/tenant_management/tests/test_cleanup_command.py -v`
Expected: todos los tests pasan.

También correr la suite completa para confirmar que no quedó ninguna referencia rota en ningún otro archivo:

Run: `docker compose exec -T backend python -m pytest core/ -v`
Expected: todos los tests pasan (grep de `product_image` sobre `core/` antes de este paso confirmó que solo estos archivos referenciaban los campos eliminados: `cleanup_deactivated_images.py`, `test_cleanup_command.py`, `tasks.py`, `content_pipeline/models.py`, `test_models.py`, `test_views.py`, `brand_dna/models.py`, `calendar_review.html`, `test_tasks.py`, `views.py`, `new_analysis.html` — todos cubiertos por las 4 tareas de este plan).

- [ ] **Step 12: Commit**

```bash
git add core/brand_dna/models.py core/brand_dna/migrations/0007_remove_analysisjob_product_image_fields.py core/content_pipeline/models.py core/content_pipeline/migrations/0011_remove_contentcalendar_active_product_images.py core/tenant_management/management/commands/cleanup_deactivated_images.py core/brand_dna/templates/brand_dna/legal/privacy.html core/content_pipeline/tests/test_models.py core/content_pipeline/tests/test_tasks.py core/tenant_management/tests/test_cleanup_command.py
git commit -m "remove(product-photos): quitar campos de BD, comando de limpieza y copy legal de fotos de producto"
```
