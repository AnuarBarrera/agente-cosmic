# Precheck de copyright/marca antes de subir foto de producto — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar un chequeo previo (precheck) que detecta riesgo de marca/copyright de terceros en la foto de producto que sube el usuario, ANTES de gastar la llamada real de generación (nano banana) — corre en cuanto el usuario selecciona el archivo, con bloqueo duro del submit si detecta riesgo, y fail-open ante cualquier fallo del chequeo mismo (red, cuota agotada, excepción).

**Architecture:** Nuevo endpoint `POST /api/brand-dna/product-photo-precheck/` (login-required) que recibe los bytes de la foto ya comprimida, valida que sea una imagen decodificable, aplica un límite diario por usuario (nuevo modelo `ProductPhotoPrecheckAttempt` + campo `Plan.max_photo_prechecks_per_day`), y si hay cupo llama a una clase nueva `ProductPhotoCopyrightPrecheck` (Vertex AI, mismo patrón de cliente que `ProductPhotoAnalyzer`, veredicto derivado en Python de 3 flags booleanos igual que `ProductPhotoQCSchema`). El frontend (`new_analysis.html`) dispara este chequeo en el evento `change` del input de foto, muestra un indicador inline, y bloquea el botón de submit si el resultado es `ok: false`.

**Tech Stack:** Django views + `django.contrib.auth.decorators.login_required`, `google.genai` (Vertex AI), Pydantic (`response_schema`), JS vanilla (mismo patrón AJAX que el submit existente), pytest + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-08-16-product-photo-copyright-precheck-design.md`

## Global Constraints

- El precheck detecta **solo** riesgo de marca/copyright de terceros — no otras categorías de contenido prohibido (violencia, contenido sexual, etc.).
- Fail-open ante cualquier excepción de la llamada a Gemini, y ante cupo diario agotado: la vista responde `{ok: true, skipped: true}`, nunca bloquea al usuario en esos casos.
- La única respuesta de bloqueo real (`ok: false`) es: imagen inválida (no decodifica) o riesgo de copyright detectado con éxito por el LLM.
- No se sube nada a GCS desde este endpoint — los bytes se validan en memoria y se descartan al terminar el request.
- Se registra una fila en `ProductPhotoPrecheckAttempt` únicamente cuando la llamada a Gemini se completó (éxito o rechazo) — nunca en fail-open (ni por excepción ni por cupo agotado).
- Mismo prefijo de ruta que otros endpoints JSON del app: `api/brand-dna/`.
- El endpoint requiere `@login_required`, igual que `analyze_submit`.
- Migraciones: generadas por `makemigrations`, nunca escritas a mano.
- Commits: `GIT_EDITOR=true git commit -m "msg"` (nunca heredoc), `git add` de archivos exactos (nunca `-A`/`-a`). Van directo a `main`, local — sin push a origin salvo pedido explícito.

---

### Task 1: Modelo `ProductPhotoPrecheckAttempt` + campo `Plan.max_photo_prechecks_per_day`

**Files:**
- Modify: `core/brand_dna/models.py` (agregar clase `ProductPhotoPrecheckAttempt` al final del archivo)
- Modify: `core/tenant_management/models.py:37` (agregar campo a `Plan`, junto a los demás `max_*`)
- Modify: `core/brand_dna/tests/test_models.py` (test nuevo)
- Create: migración de `brand_dna` (generada por `makemigrations`, no escribir a mano)
- Create: migración de `tenant_management` (generada por `makemigrations`, no escribir a mano)

**Interfaces:**
- Produces: `ProductPhotoPrecheckAttempt` — modelo Django con campos `user` (FK a `settings.AUTH_USER_MODEL`, `on_delete=models.CASCADE`) y `created_at` (`DateTimeField(auto_now_add=True)`). Usado por Task 2.
- Produces: `Plan.max_photo_prechecks_per_day` — `PositiveIntegerField(default=10)`. Usado por Task 2.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `core/brand_dna/tests/test_models.py`:

```python
def test_product_photo_precheck_attempt_creation():
    from django.contrib.auth import get_user_model
    from core.brand_dna.models import ProductPhotoPrecheckAttempt
    User = get_user_model()
    user = User.objects.create_user(
        username='precheck@test.com', email='precheck@test.com', password='pass1234',
    )
    attempt = ProductPhotoPrecheckAttempt.objects.create(user=user)
    assert attempt.user == user
    assert attempt.created_at is not None
```

- [ ] **Step 2: Confirmar que falla**

Run: `docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest core/brand_dna/tests/test_models.py::test_product_photo_precheck_attempt_creation -v"`
Expected: FAIL con `ImportError: cannot import name 'ProductPhotoPrecheckAttempt'`.

- [ ] **Step 3: Agregar el modelo `ProductPhotoPrecheckAttempt`**

Al final de `core/brand_dna/models.py` (después de la clase `BrandDNA`):

```python
class ProductPhotoPrecheckAttempt(models.Model):
    """Registra cada llamada real al precheck de copyright/marca de foto de
    producto (core/brand_dna/extractors/product_photo_copyright_precheck.py) —
    solo cuando la llamada a Gemini se completó (éxito o rechazo), nunca en
    fail-open. Usado por can_precheck_photo (rate_limits.py) para limitar
    abuso de costo: un usuario autenticado probando muchas fotos seguidas."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'brand_dna_product_photo_precheck_attempt'
```

- [ ] **Step 4: Agregar el campo `Plan.max_photo_prechecks_per_day`**

En `core/tenant_management/models.py`, dentro de la clase `Plan`, justo después de la línea `max_post_edits = models.PositiveIntegerField(default=2)` (línea 37) y antes del comentario de `allows_sample_generation`:

```python
    max_post_edits = models.PositiveIntegerField(default=2)
    # Límite de llamadas reales al precheck de copyright/marca de foto de
    # producto por usuario cada 24h (ver ProductPhotoPrecheckAttempt) —
    # mitiga abuso de costo de un usuario autenticado probando muchas fotos.
    max_photo_prechecks_per_day = models.PositiveIntegerField(default=10)
    # Permite generar 1 sola pieza de muestra (imagen o reel) desde el
```

(el resto de la clase queda igual — esto solo inserta la línea nueva entre `max_post_edits` y el comentario existente de `allows_sample_generation`).

- [ ] **Step 5: Generar las migraciones**

Run: `docker compose run --rm --entrypoint "" backend python manage.py makemigrations brand_dna tenant_management`
Expected: crea `core/brand_dna/migrations/0013_productphotoprecheckattempt.py` (o nombre similar autogenerado) y `core/tenant_management/migrations/0025_plan_max_photo_prechecks_per_day.py` (o nombre similar). Verificar que cada migración solo contiene el `CreateModel`/`AddField` esperado — sin `RunPython` ni cambios inesperados.

- [ ] **Step 6: Correr el test y confirmar que pasa**

Run: `docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest core/brand_dna/tests/test_models.py -v"`
Expected: todos los tests de `test_models.py` PASS, incluyendo el nuevo.

- [ ] **Step 7: Commit**

```bash
git add core/brand_dna/models.py core/tenant_management/models.py core/brand_dna/tests/test_models.py core/brand_dna/migrations/ core/tenant_management/migrations/
GIT_EDITOR=true git commit -m "feat(brand_dna): agrega ProductPhotoPrecheckAttempt y Plan.max_photo_prechecks_per_day"
```

---

### Task 2: `can_precheck_photo` en `rate_limits.py`

**Files:**
- Modify: `core/brand_dna/rate_limits.py`
- Modify: `core/brand_dna/tests/test_rate_limits.py`

**Interfaces:**
- Consumes: `ProductPhotoPrecheckAttempt` (Task 1), `Plan.max_photo_prechecks_per_day` (Task 1), `get_user_plan(user)` (ya existe en este archivo).
- Produces: `can_precheck_photo(user) -> tuple[bool, int]` — usado por Task 4 (`product_photo_precheck_api`).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/brand_dna/tests/test_rate_limits.py`:

```python
class TestCanPrecheckPhoto(TestCase):
    def test_allowed_when_under_limit(self):
        from core.brand_dna.rate_limits import can_precheck_photo
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'max_photo_prechecks_per_day': 10, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        with patch('core.brand_dna.rate_limits.ProductPhotoPrecheckAttempt') as MockAttempt:
            MockAttempt.objects.filter.return_value.count.return_value = 3
            allowed, remaining = can_precheck_photo(user)
        assert allowed is True
        assert remaining == 7

    def test_blocked_when_at_limit(self):
        from core.brand_dna.rate_limits import can_precheck_photo
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'max_photo_prechecks_per_day': 10, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        with patch('core.brand_dna.rate_limits.ProductPhotoPrecheckAttempt') as MockAttempt:
            MockAttempt.objects.filter.return_value.count.return_value = 10
            allowed, remaining = can_precheck_photo(user)
        assert allowed is False
        assert remaining == 0

    def test_only_counts_attempts_within_last_24h(self):
        """Un intento de hace 25h no debe contar contra el límite del día."""
        from core.brand_dna.rate_limits import can_precheck_photo
        from core.brand_dna.models import ProductPhotoPrecheckAttempt
        from core.tenant_management.models import Plan, TenantModel, Subscription
        from django.contrib.auth import get_user_model
        User = get_user_model()
        plan, _ = Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'max_photo_prechecks_per_day': 10, 'price': '0.00'},
        )
        user = User.objects.create_user(
            username='precheck-window@test.com', email='precheck-window@test.com', password='pass1234',
        )
        tenant = TenantModel.objects.create(name=user.email, status='active')
        Subscription.objects.create(tenant=tenant, plan=plan)
        user.tenant = tenant
        user.save(update_fields=['tenant'])

        old_attempt = ProductPhotoPrecheckAttempt.objects.create(user=user)
        old_attempt.created_at = timezone.now() - timedelta(hours=25)
        old_attempt.save(update_fields=['created_at'])

        allowed, remaining = can_precheck_photo(user)
        assert allowed is True
        assert remaining == 10
```

- [ ] **Step 2: Confirmar que fallan**

Run: `docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest core/brand_dna/tests/test_rate_limits.py::TestCanPrecheckPhoto -v"`
Expected: FAIL con `ImportError: cannot import name 'can_precheck_photo'`.

- [ ] **Step 3: Implementar `can_precheck_photo`**

En `core/brand_dna/rate_limits.py`, agregar el import de `ProductPhotoPrecheckAttempt` junto al de `AnalysisJob` (línea 3) y la función al final del archivo:

```python
from core.brand_dna.models import AnalysisJob, ProductPhotoPrecheckAttempt
```

```python
def can_precheck_photo(user) -> tuple[bool, int]:
    """Límite diario de llamadas reales al precheck de copyright/marca de
    foto de producto (ver ProductPhotoPrecheckAttempt). Ventana móvil de 24h,
    no día calendario."""
    plan = get_user_plan(user)
    since = timezone.now() - timedelta(hours=24)
    used = ProductPhotoPrecheckAttempt.objects.filter(user=user, created_at__gte=since).count()
    remaining = max(0, plan.max_photo_prechecks_per_day - used)
    return remaining > 0, remaining
```

- [ ] **Step 4: Correr los tests y confirmar que pasan**

Run: `docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest core/brand_dna/tests/test_rate_limits.py -v"`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/rate_limits.py core/brand_dna/tests/test_rate_limits.py
GIT_EDITOR=true git commit -m "feat(brand_dna): agrega can_precheck_photo, límite diario de precheck por usuario"
```

---

### Task 3: `ProductPhotoCopyrightPrecheck` (clase de chequeo)

**Files:**
- Create: `core/brand_dna/extractors/product_photo_copyright_precheck.py`
- Create: `core/brand_dna/tests/test_product_photo_copyright_precheck.py`

**Interfaces:**
- Produces: `ProductPhotoCopyrightPrecheck().check(image_bytes: bytes, mime_type: str) -> dict` — retorna `{'ok': bool, 'reason': str}` cuando la llamada se completó, o `{'ok': True, 'skipped': True}` si falló. Usado por Task 4.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `core/brand_dna/tests/test_product_photo_copyright_precheck.py`:

```python
from unittest.mock import patch, MagicMock
from django.test import override_settings


def _mock_vertex_client(response_json):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = response_json
    mock_client.models.generate_content.return_value = mock_resp
    return mock_client


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_ok_when_no_flags_active():
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"has_recognizable_brand_logo": false, "has_licensed_character_or_ip": false, '
            '"has_third_party_packaging_design": false, "ok": true}'
        )
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result == {'ok': True}


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_rejects_when_brand_logo_detected():
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"has_recognizable_brand_logo": true, "has_licensed_character_or_ip": false, '
            '"has_third_party_packaging_design": false, "ok": false}'
        )
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result['ok'] is False
    assert result['reason']


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_rejects_when_licensed_character_detected():
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"has_recognizable_brand_logo": false, "has_licensed_character_or_ip": true, '
            '"has_third_party_packaging_design": false, "ok": false}'
        )
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result['ok'] is False


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_rejects_when_third_party_packaging_detected():
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"has_recognizable_brand_logo": false, "has_licensed_character_or_ip": false, '
            '"has_third_party_packaging_design": true, "ok": false}'
        )
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result['ok'] is False


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_ignores_inconsistent_ok_from_llm():
    """Si el LLM manda ok=true pero algun flag individual es true, el
    veredicto se re-deriva en Python (mismo patron que ProductPhotoQCSchema
    en image_generator.py) -- no se confia en el campo ok crudo."""
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"has_recognizable_brand_logo": true, "has_licensed_character_or_ip": false, '
            '"has_third_party_packaging_design": false, "ok": true}'
        )
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result['ok'] is False


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_fails_open_on_exception():
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client', side_effect=Exception('boom')):
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result == {'ok': True, 'skipped': True}
```

- [ ] **Step 2: Confirmar que fallan**

Run: `docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest core/brand_dna/tests/test_product_photo_copyright_precheck.py -v"`
Expected: FAIL con `ModuleNotFoundError: No module named 'core.brand_dna.extractors.product_photo_copyright_precheck'`.

- [ ] **Step 3: Implementar `ProductPhotoCopyrightPrecheck`**

Crear `core/brand_dna/extractors/product_photo_copyright_precheck.py`:

```python
import json
import logging
import google.genai as genai
from google.genai import types
from django.conf import settings
from pydantic import BaseModel
from core.shared.metrics_utils import track_external_api, record_tokens, vertex_labels

logger = logging.getLogger(__name__)

_REJECTED_REASON = (
    "Detectamos una marca, logo o personaje con derechos de terceros en esta foto. "
    "Prueba con otra foto de tu producto."
)

_PROMPT = (
    "Analiza esta foto de un producto real subida por un negocio. Solo te interesa "
    "detectar contenido de MARCA/COPYRIGHT DE TERCEROS -- no evalues ningun otro "
    "aspecto de la imagen.\n\n"
    "El producto real del negocio es aceptable aunque tenga texto o diseño propio "
    "impreso en su empaque/etiqueta (esto NO cuenta como riesgo).\n\n"
    "has_recognizable_brand_logo: true si aparece un logo o marca reconocible de UN "
    "TERCERO (no el producto propio del negocio) -- ej. una marca de refresco, ropa, "
    "o tecnologia conocida en el fondo o en otro objeto de la foto.\n"
    "has_licensed_character_or_ip: true si aparece un personaje con licencia "
    "(caricatura, superheroe, marca de entretenimiento) impreso en cualquier "
    "superficie de la foto.\n"
    "has_third_party_packaging_design: true si el empaque/etiqueta visible en la foto "
    "pertenece claramente a una marca comercial reconocible DISTINTA del producto "
    "propio del negocio (ej. una bolsa de una cadena de comida rapida usada como "
    "fondo, no como el producto que se vende).\n"
    "ok: true solo si los 3 flags anteriores son false."
)


class CopyrightPrecheckSchema(BaseModel):
    has_recognizable_brand_logo: bool
    has_licensed_character_or_ip: bool
    has_third_party_packaging_design: bool
    ok: bool


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION_TEXT,
    )


class ProductPhotoCopyrightPrecheck:
    def check(self, image_bytes: bytes, mime_type: str) -> dict:
        """Retorna {'ok': bool, 'reason': str} si la llamada se completo, o
        {'ok': True, 'skipped': True} si fallo (fail-open)."""
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            with track_external_api('gemini', operation='product_photo_copyright_precheck'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[_PROMPT, image_part],
                    config=types.GenerateContentConfig(
                        labels=vertex_labels(),
                        response_mime_type="application/json",
                        response_schema=CopyrightPrecheckSchema,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
            record_tokens(resp, operation='product_photo_copyright_precheck',
                          prompt_preview=_PROMPT[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            data = json.loads(resp.text)
            # Veredicto re-derivado en Python, no se confia en el `ok` del LLM --
            # mismo patron que ProductPhotoQCSchema en image_generator.py.
            parsed = CopyrightPrecheckSchema(**data)
            ok = not (
                parsed.has_recognizable_brand_logo
                or parsed.has_licensed_character_or_ip
                or parsed.has_third_party_packaging_design
            )
            if ok:
                return {'ok': True}
            logger.info(f"Copyright precheck REJECTED: {data}")
            return {'ok': False, 'reason': _REJECTED_REASON}
        except Exception as e:
            logger.error(f"ProductPhotoCopyrightPrecheck error (fail-open): {e}")
            return {'ok': True, 'skipped': True}
```

- [ ] **Step 4: Correr los tests y confirmar que pasan**

Run: `docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest core/brand_dna/tests/test_product_photo_copyright_precheck.py -v"`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/extractors/product_photo_copyright_precheck.py core/brand_dna/tests/test_product_photo_copyright_precheck.py
GIT_EDITOR=true git commit -m "feat(brand_dna): agrega ProductPhotoCopyrightPrecheck (chequeo de marca/copyright)"
```

---

### Task 4: Vista `product_photo_precheck_api` + ruta

**Files:**
- Modify: `core/brand_dna/views.py`
- Modify: `core/brand_dna/urls.py`
- Modify: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: `_validate_image_bytes(data: bytes) -> bool` (ya existe, `views.py:49`), `can_precheck_photo(user) -> tuple[bool, int]` (Task 2), `ProductPhotoCopyrightPrecheck().check(image_bytes, mime_type) -> dict` (Task 3), `ProductPhotoPrecheckAttempt` (Task 1).
- Produces: vista `product_photo_precheck_api(request)`, ruta `path('api/brand-dna/product-photo-precheck/', views.product_photo_precheck_api, name='product_photo_precheck_api')`. Usado por Task 5 (frontend).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/brand_dna/tests/test_views.py` (reusa el fixture `_fake_product_photo()` ya definido en el archivo, línea 153):

```python
def test_precheck_api_requires_login():
    c = Client()
    response = c.post('/api/brand-dna/product-photo-precheck/', {
        'product_reference_photo': _fake_product_photo(),
    })
    assert response.status_code == 302


def test_precheck_api_rejects_invalid_image(user):
    c = Client()
    c.force_login(user)
    from django.core.files.uploadedfile import SimpleUploadedFile
    bad_file = SimpleUploadedFile('producto.png', b'no es una imagen real', content_type='image/png')
    with patch('core.brand_dna.views.ProductPhotoCopyrightPrecheck') as MockPrecheck:
        response = c.post('/api/brand-dna/product-photo-precheck/', {
            'product_reference_photo': bad_file,
        })
    data = json.loads(response.content)
    assert data['ok'] is False
    MockPrecheck.assert_not_called()
    assert not ProductPhotoPrecheckAttempt.objects.filter(user=user).exists()


def test_precheck_api_success_registers_attempt(user, free_plan):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.can_precheck_photo', return_value=(True, 9)), \
         patch('core.brand_dna.views.ProductPhotoCopyrightPrecheck') as MockPrecheck:
        MockPrecheck.return_value.check.return_value = {'ok': True}
        response = c.post('/api/brand-dna/product-photo-precheck/', {
            'product_reference_photo': _fake_product_photo(),
        })
    data = json.loads(response.content)
    assert data == {'ok': True}
    assert ProductPhotoPrecheckAttempt.objects.filter(user=user).count() == 1


def test_precheck_api_rejection_registers_attempt(user, free_plan):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.can_precheck_photo', return_value=(True, 9)), \
         patch('core.brand_dna.views.ProductPhotoCopyrightPrecheck') as MockPrecheck:
        MockPrecheck.return_value.check.return_value = {'ok': False, 'reason': 'marca detectada'}
        response = c.post('/api/brand-dna/product-photo-precheck/', {
            'product_reference_photo': _fake_product_photo(),
        })
    data = json.loads(response.content)
    assert data == {'ok': False, 'reason': 'marca detectada'}
    assert ProductPhotoPrecheckAttempt.objects.filter(user=user).count() == 1


def test_precheck_api_exception_does_not_register_attempt(user, free_plan):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.can_precheck_photo', return_value=(True, 9)), \
         patch('core.brand_dna.views.ProductPhotoCopyrightPrecheck') as MockPrecheck:
        MockPrecheck.return_value.check.return_value = {'ok': True, 'skipped': True}
        response = c.post('/api/brand-dna/product-photo-precheck/', {
            'product_reference_photo': _fake_product_photo(),
        })
    data = json.loads(response.content)
    assert data == {'ok': True, 'skipped': True}
    assert not ProductPhotoPrecheckAttempt.objects.filter(user=user).exists()


def test_precheck_api_quota_exceeded_skips_without_calling_gemini(user, free_plan):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.can_precheck_photo', return_value=(False, 0)), \
         patch('core.brand_dna.views.ProductPhotoCopyrightPrecheck') as MockPrecheck:
        response = c.post('/api/brand-dna/product-photo-precheck/', {
            'product_reference_photo': _fake_product_photo(),
        })
    data = json.loads(response.content)
    assert data == {'ok': True, 'skipped': True}
    MockPrecheck.return_value.check.assert_not_called()
    assert not ProductPhotoPrecheckAttempt.objects.filter(user=user).exists()
```

Agregar los imports nuevos que estos tests necesitan al principio de `core/brand_dna/tests/test_views.py` (junto a los imports existentes de `AnalysisJob, BrandDNA`):

```python
from core.brand_dna.models import AnalysisJob, BrandDNA, ProductPhotoPrecheckAttempt
```

- [ ] **Step 2: Confirmar que fallan**

Run: `docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest core/brand_dna/tests/test_views.py -k precheck -v"`
Expected: FAIL — la URL `/api/brand-dna/product-photo-precheck/` no existe (404) y `core.brand_dna.views.ProductPhotoCopyrightPrecheck`/`can_precheck_photo` no están importados en `views.py`.

- [ ] **Step 3: Agregar la vista**

En `core/brand_dna/views.py`, agregar los imports nuevos junto a los existentes (línea 21-23):

```python
from core.brand_dna.models import AnalysisJob, BrandDNA, ProductPhotoPrecheckAttempt
from core.brand_dna.rate_limits import can_precheck_photo
from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
```

Agregar la vista nueva después de `analyze_submit` (después de la línea 211, antes de `@login_required` de `results`):

```python
@login_required
@require_POST
def product_photo_precheck_api(request):
    """Precheck de riesgo de marca/copyright ANTES de que el usuario envie
    el formulario completo -- se llama desde new_analysis.html en el evento
    change del input de foto. No sube nada a GCS; solo valida en memoria.
    Fail-open (ok:true, skipped:true) ante excepcion o cupo diario agotado --
    nano banana sigue siendo el filtro real al final del pipeline."""
    if 'product_reference_photo' not in request.FILES:
        return JsonResponse({'ok': False, 'reason': 'No se recibió ninguna foto.'})

    photo_bytes = request.FILES['product_reference_photo'].read()
    if not _validate_image_bytes(photo_bytes):
        return JsonResponse({'ok': False, 'reason': 'Esa imagen no se pudo abrir, intenta con otra.'})

    allowed, _remaining = can_precheck_photo(request.user)
    if not allowed:
        return JsonResponse({'ok': True, 'skipped': True})

    from core.content_pipeline.generators.image_generator import _detect_mime
    mime_type = _detect_mime(photo_bytes)
    result = ProductPhotoCopyrightPrecheck().check(photo_bytes, mime_type)
    if not result.get('skipped'):
        ProductPhotoPrecheckAttempt.objects.create(user=request.user)
    return JsonResponse(result)
```

`_detect_mime` vive en `core/content_pipeline/generators/image_generator.py:153` (reusado también por `core/content_pipeline/tasks.py:16` para el reel/imagen con foto real). Se importa dentro de la función, no a nivel de módulo, para evitar un import circular — mismo patrón que otros imports diferidos ya presentes en este archivo (ej. `from core.brand_dna.tasks import analyze_brand_task` dentro de `analyze_submit`).

- [ ] **Step 4: Agregar la ruta**

En `core/brand_dna/urls.py`, agregar después de la línea 12 (`path('api/brand-dna/status/<uuid:job_id>/', ...)`):

```python
    path('api/brand-dna/product-photo-precheck/', views.product_photo_precheck_api, name='product_photo_precheck_api'),
```

- [ ] **Step 5: Correr los tests y confirmar que pasan**

Run: `docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest core/brand_dna/tests/test_views.py -v"`
Expected: todos PASS, incluidos los 6 tests nuevos de precheck y los existentes de `analyze_submit` (sin regresión).

- [ ] **Step 6: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/urls.py core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(brand_dna): agrega endpoint product_photo_precheck_api"
```

---

### Task 5: Frontend — integrar el precheck en `new_analysis.html`

**Files:**
- Modify: `core/brand_dna/templates/brand_dna/new_analysis.html`

**Interfaces:**
- Consumes: `POST /api/brand-dna/product-photo-precheck/` (Task 4), respuestas `{ok: true}` / `{ok: false, reason: string}` / `{ok: true, skipped: true}`.

Este repo no tiene suite de tests JS (confirmado en la exploración de este plan) — la verificación de este task es manual en navegador, mismo patrón usado para validar el módulo hermano de reel con foto real.

- [ ] **Step 1: Agregar el indicador inline en el HTML**

En `core/brand_dna/templates/brand_dna/new_analysis.html`, dentro de `<div class="form-group" id="productPhotoGroup">` (línea 121-124), agregar el indicador después del `<input>`:

```html
      <div class="form-group" id="productPhotoGroup">
        <label>Foto real del producto <span class="optional-badge">opcional</span></label>
        <input type="file" name="product_reference_photo" accept="image/*" id="productPhotoInput">
        <small id="photoPrecheckStatus" style="display:none;margin-top:6px;font-size:0.85rem;"></small>
      </div>
```

- [ ] **Step 2: Agregar el CSS del indicador**

En el bloque `<style>` (después de la regla `input[type="file"] { ... }`, línea 32), agregar:

```css
    #photoPrecheckStatus.checking { color: #aaa; }
    #photoPrecheckStatus.ok { color: #2ecc71; }
    #photoPrecheckStatus.warning { color: #e74c3c; }
```

- [ ] **Step 3: Agregar el JS del precheck**

En el `<script>` existente, después de la función `compressAll` (línea 168) y antes del listener de `submit` (línea 170), agregar:

```javascript
    var photoPrecheckOk = true;

    function setPrecheckStatus(cls, text) {
      var el = document.getElementById('photoPrecheckStatus');
      if (!el) return;
      el.className = cls;
      el.textContent = text;
      el.style.display = text ? 'block' : 'none';
    }

    function updateSubmitButtonForPrecheck() {
      var btn = document.getElementById('submitBtn');
      btn.disabled = !photoPrecheckOk;
    }

    var productPhotoInputEl = document.getElementById('productPhotoInput');
    if (productPhotoInputEl) {
      productPhotoInputEl.addEventListener('change', function() {
        photoPrecheckOk = true;
        updateSubmitButtonForPrecheck();
        if (!this.files.length) {
          setPrecheckStatus('', '');
          return;
        }
        setPrecheckStatus('checking', 'Analizando tu foto...');
        var self = this;
        compressImage(this.files[0]).then(function(compressed) {
          var fd = new FormData();
          fd.append('csrfmiddlewaretoken', document.querySelector('#analyzeForm [name="csrfmiddlewaretoken"]').value);
          fd.append('product_reference_photo', compressed);
          var xhr = new XMLHttpRequest();
          xhr.open('POST', '{% url "product_photo_precheck_api" %}');
          xhr.onload = function() {
            if (xhr.status !== 200) {
              setPrecheckStatus('', '');
              photoPrecheckOk = true;
              updateSubmitButtonForPrecheck();
              return;
            }
            var data;
            try { data = JSON.parse(xhr.responseText); } catch (e) { data = {ok: true, skipped: true}; }
            if (data.ok === false) {
              photoPrecheckOk = false;
              setPrecheckStatus('warning', '⚠ ' + (data.reason || 'Esta foto no se puede usar.'));
            } else if (data.skipped) {
              photoPrecheckOk = true;
              setPrecheckStatus('', '');
            } else {
              photoPrecheckOk = true;
              setPrecheckStatus('ok', '✓ Foto lista para usar');
            }
            updateSubmitButtonForPrecheck();
          };
          xhr.onerror = function() {
            photoPrecheckOk = true;
            setPrecheckStatus('', '');
            updateSubmitButtonForPrecheck();
          };
          xhr.send(fd);
        });
      });
    }
```

- [ ] **Step 4: Verificación manual en navegador**

1. Levantar el stack: `docker compose up -d --force-recreate --no-deps backend nginx` (recuerda: `DEBUG=False` cachea templates, hace falta `--force-recreate`, no solo `restart`).
2. Iniciar sesión con un usuario del plan Admin (o cualquiera con `allows_sample_generation=True`).
3. Ir a `/nuevo-analisis/`, seleccionar "Solo 1 imagen de muestra" o "Solo 1 reel de muestra" para que aparezca el campo de foto.
4. Subir una foto sin marcas de terceros → confirmar que aparece "Analizando tu foto..." y luego "✓ Foto lista para usar", y que el botón de submit sigue habilitado.
5. Subir una foto con un logo de marca reconocible visible (ej. una lata de refresco de marca conocida) → confirmar que aparece la advertencia y que el botón de submit se deshabilita.
6. Cambiar a otra foto sin marca → confirmar que el botón se re-habilita y el indicador vuelve a "✓ lista".
7. Revisar en los logs del backend (`docker compose logs backend --tail 50`) que se ve la llamada a `product_photo_copyright_precheck` en `track_external_api`, y que no hay ninguna subida a GCS asociada a este endpoint (confirmar en el bucket o en logs que no aparece un `uploads/product_ref_...` nuevo hasta que se envíe el formulario real).

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/templates/brand_dna/new_analysis.html
GIT_EDITOR=true git commit -m "feat(brand_dna): integra precheck de copyright en el formulario de subida de foto"
```

---

## Self-Review

**1. Cobertura del spec:** sección 1 (flujo UX) → Task 5. Sección 2 (endpoint + chequeo) → Tasks 3 y 4. Sección 3 (frontend) → Task 5. Sección 4 (límite de uso) → Tasks 1 y 2. Sección 5 (tabla de errores) → cubierta por los tests de Task 4 (imagen inválida, excepción, cupo agotado, éxito, rechazo) y el fail-open del frontend en Task 5. Sección 6 (testing) → un test por caso en cada task. Todo cubierto.

**2. Placeholders:** ninguno — todos los steps tienen código literal completo, sin "TBD" ni "agregar validación" genérico.

**3. Consistencia de tipos:** `can_precheck_photo(user) -> tuple[bool, int]` (Task 2) usado igual en Task 4. `ProductPhotoCopyrightPrecheck().check(image_bytes: bytes, mime_type: str) -> dict` (Task 3) usado igual en Task 4. `ProductPhotoPrecheckAttempt(user=..., created_at=...)` (Task 1) consistente entre Task 2 y Task 4. Nombres de endpoint/ruta (`product_photo_precheck_api`) consistentes entre Task 4 y Task 5.
