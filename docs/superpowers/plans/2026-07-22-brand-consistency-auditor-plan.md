# Auditor de Consistencia de Marca — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar un auditor de consistencia de marca que revisa el texto que Gemini genera en el pipeline de contenido (captions y campos del guion de reel) y corrige, antes de que llegue al usuario, los casos donde ese texto es técnicamente válido pero perjudica el posicionamiento de la marca (HALLAZGO 78).

**Architecture:** Un módulo nuevo y compartido (`core/content_pipeline/generators/brand_consistency_qc.py`) con dos funciones puras — `audit_brand_consistency()` (una llamada a Gemini que audita varios campos a la vez) y `rewrite_for_brand_consistency()` (reescribe un campo puntual) — ambas fail-open. Se integra en dos generadores existentes (`TextGenerator`, `ReelScriptGenerator`) tras su generación normal, siguiendo el mismo patrón ya usado por `caption_safety_qc`/`_scrub_brand_leak` en esos mismos archivos.

**Tech Stack:** Django, `google-genai` SDK contra Vertex AI, pytest + `pytest.mark.django_db`, mocking con `unittest.mock.patch`. Sin dependencias nuevas.

## Global Constraints

- Spec completo y aprobado: `docs/superpowers/specs/2026-07-22-brand-consistency-auditor-design.md` — leer antes de implementar si algo en este plan no queda claro.
- El auditor corre SIEMPRE en toda generación (no condicionado a nicho sensible ni a ausencia de `business_url`, a diferencia de `caption_safety_qc`).
- Fail-open estricto: cualquier error de red, parseo o excepción en `audit_brand_consistency`/`rewrite_for_brand_consistency` nunca debe propagar — siempre degrada a "todo ok" / "texto original", nunca bloquea el pipeline.
- Una sola llamada a Gemini por auditoría (todos los campos juntos), llamadas de reescritura individuales solo para campos marcados.
- Sin reintentos ni re-auditoría tras reescribir — una sola pasada.
- `music_mood` (campo de `ReelScriptGenerator`) queda excluido del auditor.
- `scene_prompts` se audita pero NUNCA se reescribe automáticamente — si se marca, solo se loggea un `logger.warning`.
- Toda llamada nueva a Gemini debe usar `config=types.GenerateContentConfig(labels=vertex_labels())`, envuelta en `track_external_api('gemini', operation='...')`, y pasar por `record_tokens(...)` — exactamente igual que las llamadas ya existentes en `text_generator.py`/`reel_script_generator.py`.
- **Riesgo no cubierto por el spec, crítico para no romper CI/costos**: este entorno de desarrollo tiene credenciales ADC (`application_default_credentials.json`) válidas y funcionales contra el proyecto real de GCP (confirmado en esta misma sesión). Si se agrega el import de `audit_brand_consistency` a `text_generator.py`/`reel_script_generator.py` sin mockearlo por defecto, **todos los tests preexistentes de esos dos archivos harían una llamada real y pagada a Vertex AI** en cada corrida. Cada tarea de integración (Task 2 y Task 3) DEBE agregar un fixture `autouse=True` que mockee `audit_brand_consistency`/`rewrite_for_brand_consistency` por defecto en ese archivo de test, ANTES de modificar el código de producción — ver Paso 1 de cada una de esas tareas.

---

### Task 1: Módulo `brand_consistency_qc.py`

**Files:**
- Create: `core/content_pipeline/generators/brand_consistency_qc.py`
- Test: `core/content_pipeline/tests/test_brand_consistency_qc.py`

**Interfaces:**
- Produces: `audit_brand_consistency(fields: dict, brand_dna: BrandDNA) -> dict` — `fields` es `{nombre_campo: texto}`; devuelve `{nombre_campo: reason}` solo para campos con problema, `{}` si todo bien o si algo falla (fail-open).
- Produces: `rewrite_for_brand_consistency(field_name: str, text: str, reason: str, brand_dna: BrandDNA) -> str` — devuelve el texto reescrito, o `text` sin cambios si algo falla (fail-open).
- Consumes: `BrandDNA` (de `core.brand_dna.models`) — usa `.business_name`, `.description`, `.tone`, `.keywords`.

- [ ] **Step 1: Escribir el test que falla — `audit_brand_consistency` devuelve `{}` cuando todo está bien**

Crear `core/content_pipeline/tests/test_brand_consistency_qc.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db


@pytest.fixture
def brand_dna():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://elperrorebelde.com')
    return BrandDNA.objects.create(
        job=job, business_name='El Perro Rebelde', business_url='https://elperrorebelde.com',
        description='Ropa y accesorios para mascotas hechos con tecnica de upcycling',
        keywords=['upcycling', 'moda sostenible'],
        audience='Dueños de mascotas conscientes', tone='premium y consciente',
        primary_colors=['#1a1a2e'],
    )


def _mock_vertex_client(json_text):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json_text
    mock_client.models.generate_content.return_value = mock_resp
    return mock_client


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_audit_returns_empty_when_all_fields_ok(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import audit_brand_consistency
    response_json = '{"narration_script": {"ok": true, "reason": ""}}'
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = audit_brand_consistency({'narration_script': 'Hecho con upcycling.'}, brand_dna)
    assert result == {}
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_brand_consistency_qc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.content_pipeline.generators.brand_consistency_qc'`

(Si `docker compose` no está corriendo: `docker compose up -d db redis backend` primero.)

- [ ] **Step 3: Crear el módulo con la implementación mínima**

Crear `core/content_pipeline/generators/brand_consistency_qc.py`:

```python
import json
import logging
import re
import google.genai as genai
from google.genai import types
from django.conf import settings
from core.brand_dna.models import BrandDNA
from core.shared.metrics_utils import track_external_api, record_tokens, vertex_labels

logger = logging.getLogger(__name__)

_AUDIT_PROMPT = (
    "Eres un auditor de identidad de marca. Evalua si estos textos generados "
    "por IA son consistentes con la marca, o si accidentalmente cambiaron "
    "terminologia o tono de forma que perjudica su posicionamiento.\n\n"
    "MARCA: {business_name}\n"
    "DESCRIPCION (fuente de verdad de terminologia/posicionamiento): {description}\n"
    "TONO: {tone}\n"
    "KEYWORDS: {keywords}\n\n"
    "TEXTOS A EVALUAR:\n{fields_block}\n\n"
    "Marca un problema en un campo SOLO si:\n"
    "- Reemplaza un termino especifico de la marca (presente en la descripcion "
    "o keywords) por un sinonimo generico con connotacion distinta o inferior "
    "(ej: \"upcycling\" -> \"materiales reutilizados\" suena a segunda mano, "
    "cuando upcycling es un termino de moda sostenible premium).\n"
    "- El tono no coincide con {tone} (ej: mezcla registros, usa un acento o "
    "variante regional inesperada).\n"
    "NO marques problemas de gusto o estilo menores — solo casos donde el "
    "cambio daña activamente el posicionamiento de la marca.\n\n"
    "Responde UNICAMENTE con este JSON (sin markdown), una entrada por cada "
    "campo listado arriba:\n"
    '{{"nombre_campo": {{"ok": <bool>, "reason": "..."}}, ...}}'
)

_REWRITE_PROMPT = (
    "Reescribe el siguiente texto para corregir este problema de consistencia "
    "de marca: {reason}\n\n"
    "Texto original: \"{text}\"\n"
    "Terminologia/posicionamiento de referencia (descripcion de la marca): {description}\n"
    "Tono de la marca: {tone}\n\n"
    "Manten el mismo mensaje central y longitud aproximada. Responde "
    "UNICAMENTE con el texto corregido, sin comillas ni explicaciones."
)


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


def audit_brand_consistency(fields: dict, brand_dna: BrandDNA) -> dict:
    """Audita todos los campos en una sola llamada a Gemini. Devuelve
    {nombre_campo: reason} solo para los campos con problema. Fail-open:
    cualquier error devuelve {} (no bloquea el pipeline)."""
    if not fields:
        return {}
    try:
        client = _vertex_client()
        fields_block = '\n'.join(f'{name}: "{text}"' for name, text in fields.items())
        prompt = _AUDIT_PROMPT.format(
            business_name=brand_dna.business_name,
            description=brand_dna.description,
            tone=brand_dna.tone,
            keywords=', '.join(brand_dna.keywords or []),
            fields_block=fields_block,
        )
        with track_external_api('gemini', operation='brand_consistency_audit'):
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL, contents=prompt,
                config=types.GenerateContentConfig(labels=vertex_labels()),
            )
        record_tokens(resp, operation='brand_consistency_audit',
                      prompt_preview=prompt[:500],
                      response_preview=resp.text[:500] if resp.text else '')
        raw = resp.text.strip()
        raw = re.sub(r'^```(?:json)?\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            return {}
        data = json.loads(match.group())
        issues = {}
        for name in fields:
            entry = data.get(name)
            if isinstance(entry, dict) and not entry.get('ok', True):
                issues[name] = str(entry.get('reason', '')).strip() or 'Inconsistente con la identidad de marca'
        return issues
    except Exception as e:
        logger.warning(f"audit_brand_consistency fallo (fail-open, se asume ok): {e}")
        return {}


def rewrite_for_brand_consistency(field_name: str, text: str, reason: str, brand_dna: BrandDNA) -> str:
    """Reescribe un campo puntual para corregir 'reason'. Fail-open: si la
    llamada falla, devuelve el texto original sin cambios."""
    try:
        client = _vertex_client()
        prompt = _REWRITE_PROMPT.format(
            reason=reason,
            text=text,
            description=brand_dna.description,
            tone=brand_dna.tone,
        )
        with track_external_api('gemini', operation='brand_consistency_fix'):
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL, contents=prompt,
                config=types.GenerateContentConfig(labels=vertex_labels()),
            )
        record_tokens(resp, operation='brand_consistency_fix',
                      response_preview=resp.text[:300] if resp.text else '')
        new_text = resp.text.strip().strip('"').strip("'")
        raw = re.sub(r'^```.*?\n', '', new_text, flags=re.DOTALL)
        raw = re.sub(r'\n?```$', '', raw)
        return raw.strip() or text
    except Exception as e:
        logger.error(f"rewrite_for_brand_consistency fallo para campo '{field_name}': {e}")
        return text
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_brand_consistency_qc.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Agregar los 5 tests restantes**

Agregar al final de `core/content_pipeline/tests/test_brand_consistency_qc.py`:

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_audit_returns_issue_when_field_flagged(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import audit_brand_consistency
    response_json = (
        '{"narration_script": {"ok": false, "reason": '
        '"Reemplaza upcycling por materiales reutilizados, connotacion inferior"}}'
    )
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = audit_brand_consistency(
            {'narration_script': 'Hecho con materiales reutilizados.'}, brand_dna,
        )
    assert 'narration_script' in result
    assert 'upcycling' in result['narration_script']


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_audit_fails_open_on_exception(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import audit_brand_consistency
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
        result = audit_brand_consistency({'narration_script': 'texto'}, brand_dna)
    assert result == {}


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_audit_fails_open_on_unparseable_response(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import audit_brand_consistency
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client('esto no es json')
        result = audit_brand_consistency({'narration_script': 'texto'}, brand_dna)
    assert result == {}


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_rewrite_returns_new_text_on_success(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import rewrite_for_brand_consistency
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client('Hecho con upcycling, moda circular consciente.')
        result = rewrite_for_brand_consistency(
            'narration_script', 'Hecho con materiales reutilizados.',
            'Reemplaza upcycling por un termino de connotacion inferior', brand_dna,
        )
    assert result == 'Hecho con upcycling, moda circular consciente.'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_rewrite_returns_original_text_on_failure(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import rewrite_for_brand_consistency
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
        original = 'Hecho con materiales reutilizados.'
        result = rewrite_for_brand_consistency('narration_script', original, 'razon', brand_dna)
    assert result == original
```

- [ ] **Step 6: Correr toda la suite del archivo y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_brand_consistency_qc.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Commit**

```bash
git add core/content_pipeline/generators/brand_consistency_qc.py core/content_pipeline/tests/test_brand_consistency_qc.py
git commit -m "feat(content-pipeline): auditor de consistencia de marca (modulo base)

HALLAZGO 78 — nuevo modulo brand_consistency_qc.py con audit_brand_consistency()
y rewrite_for_brand_consistency(), fail-open, siguiendo el patron ya
establecido de caption_safety_qc. Aun no esta enganchado a ningun generador."
```

---

### Task 2: Integrar en `ReelScriptGenerator`

**Files:**
- Modify: `core/content_pipeline/generators/reel_script_generator.py`
- Test: `core/content_pipeline/tests/test_reel_script_generator.py`

**Interfaces:**
- Consumes: `audit_brand_consistency(fields: dict, brand_dna: BrandDNA) -> dict` y `rewrite_for_brand_consistency(field_name: str, text: str, reason: str, brand_dna: BrandDNA) -> str` (Task 1, ya implementadas y con tests propios).

- [ ] **Step 1: Agregar el fixture `autouse` que protege los tests existentes ANTES de tocar el codigo de produccion**

Este paso va primero a proposito: en cuanto el Step 3 agregue el import de `audit_brand_consistency` a `reel_script_generator.py`, cualquier test de este archivo que no lo mockee explicitamente haria una llamada real a Vertex AI (este entorno tiene credenciales validas). Este fixture lo neutraliza por defecto para TODOS los tests del archivo.

En `core/content_pipeline/tests/test_reel_script_generator.py`, agregar despues de la funcion `_mock_vertex_client` (despues de la linea `return mock_client`, antes de la primera funcion `def test_...`):

```python
@pytest.fixture(autouse=True)
def _mock_brand_consistency_qc():
    with patch('core.content_pipeline.generators.reel_script_generator.audit_brand_consistency', return_value={}) as mock_audit, \
         patch('core.content_pipeline.generators.reel_script_generator.rewrite_for_brand_consistency') as mock_rewrite:
        yield mock_audit, mock_rewrite
```

- [ ] **Step 2: Correr la suite existente y verificar que sigue pasando (el import aun no existe, este paso confirma que el fixture no rompe nada por si solo)**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_script_generator.py -v`
Expected: PASS (los 9 tests existentes, el fixture autouse no afecta nada porque `reel_script_generator.py` todavia no importa esos nombres — `patch()` sobre un atributo que no existe fallaria; si este paso falla con `AttributeError`, es la señal de que hay que hacer el Step 3 primero. En ese caso, continuar directo al Step 3 y volver a correr este mismo comando despues.)

- [ ] **Step 3: Escribir el test que falla — narration_script marcado se reescribe**

Agregar a `core/content_pipeline/tests/test_reel_script_generator.py`:

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_rewrites_field_flagged_by_brand_consistency_audit(brand_dna, _mock_brand_consistency_qc):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    mock_audit, mock_rewrite = _mock_brand_consistency_qc
    mock_audit.return_value = {'narration_script': 'connotacion inferior'}
    mock_rewrite.return_value = 'Hecho con upcycling.'
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C",'
        '"narration_script":"Hecho con materiales reutilizados.",'
        '"scene_prompts":["s1, no text, no logos.","s2, no text, no logos.",'
        '"s3, no text, no logos.","s4, no text, no logos.",'
        '"s5, no text, no logos.","s6, no text, no logos."],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    mock_audit.assert_called_once()
    mock_rewrite.assert_called_once_with(
        'narration_script', 'Hecho con materiales reutilizados.', 'connotacion inferior', brand_dna,
    )
    assert result['narration_script'] == 'Hecho con upcycling.'
```

- [ ] **Step 4: Correr el test y verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_script_generator.py::test_generate_rewrites_field_flagged_by_brand_consistency_audit -v`
Expected: FAIL — `AttributeError: <module 'core.content_pipeline.generators.reel_script_generator'> does not have the attribute 'audit_brand_consistency'` (el fixture `_mock_brand_consistency_qc` intenta mockear un nombre que todavia no existe en el modulo — se resuelve en el Step 5).

- [ ] **Step 5: Wirear la integracion en `reel_script_generator.py`**

En `core/content_pipeline/generators/reel_script_generator.py`, agregar el import despues de la linea 10 (`from core.content_pipeline.generators.text_generator import _is_sensitive_niche, _strip_accents`):

```python
from core.content_pipeline.generators.brand_consistency_qc import audit_brand_consistency, rewrite_for_brand_consistency
```

Reemplazar el bloque final del metodo `generate()` (las lineas que van desde `if _is_sensitive_niche(brand_dna):` hasta el `return result` final, actualmente:

```python
            if _is_sensitive_niche(brand_dna):
                text_to_check = _strip_accents(f"{result['hook_text']} {result['narration_script']}".lower())
                banned = ('garantizado', 'garantizamos', 'asegurar', 'aseguramos', '100%')
                if any(word in text_to_check for word in banned):
                    logger.warning("ReelScriptGenerator: guion rechazado por lenguaje prohibido en nicho sensible, usando fallback")
                    return fallback
            return result
```

) por:

```python
            if _is_sensitive_niche(brand_dna):
                text_to_check = _strip_accents(f"{result['hook_text']} {result['narration_script']}".lower())
                banned = ('garantizado', 'garantizamos', 'asegurar', 'aseguramos', '100%')
                if any(word in text_to_check for word in banned):
                    logger.warning("ReelScriptGenerator: guion rechazado por lenguaje prohibido en nicho sensible, usando fallback")
                    return fallback

            fields_to_audit = {
                'hook_text': result['hook_text'],
                'tag_cta': result['tag_cta'],
                'narration_script': result['narration_script'],
                'scene_prompts': ' | '.join(result['scene_prompts']),
            }
            issues = audit_brand_consistency(fields_to_audit, brand_dna)
            for field_name, reason in issues.items():
                if field_name == 'scene_prompts':
                    logger.warning(f"ReelScriptGenerator: scene_prompts marcado por consistencia de marca ({reason}), no se reescribe automaticamente")
                    continue
                result[field_name] = rewrite_for_brand_consistency(field_name, result[field_name], reason, brand_dna)
            return result
```

- [ ] **Step 6: Correr el test y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_script_generator.py::test_generate_rewrites_field_flagged_by_brand_consistency_audit -v`
Expected: PASS

- [ ] **Step 7: Agregar los 2 tests restantes**

Agregar a `core/content_pipeline/tests/test_reel_script_generator.py`:

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_does_not_rewrite_scene_prompts_when_flagged(brand_dna, _mock_brand_consistency_qc):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    mock_audit, mock_rewrite = _mock_brand_consistency_qc
    mock_audit.return_value = {'scene_prompts': 'inconsistente'}
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C","narration_script":"N",'
        '"scene_prompts":["s1, no text, no logos.","s2, no text, no logos.",'
        '"s3, no text, no logos.","s4, no text, no logos.",'
        '"s5, no text, no logos.","s6, no text, no logos."],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    mock_rewrite.assert_not_called()
    assert result['scene_prompts'][0] == 's1, no text, no logos.'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_skips_rewrite_when_audit_returns_no_issues(brand_dna, _mock_brand_consistency_qc):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    _, mock_rewrite = _mock_brand_consistency_qc
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C","narration_script":"N",'
        '"scene_prompts":["s1, no text, no logos.","s2, no text, no logos.",'
        '"s3, no text, no logos.","s4, no text, no logos.",'
        '"s5, no text, no logos.","s6, no text, no logos."],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        ReelScriptGenerator().generate(post_data, brand_dna)

    mock_rewrite.assert_not_called()
```

- [ ] **Step 8: Correr toda la suite del archivo y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_script_generator.py -v`
Expected: PASS (12 tests: 9 preexistentes + 3 nuevos)

- [ ] **Step 9: Commit**

```bash
git add core/content_pipeline/generators/reel_script_generator.py core/content_pipeline/tests/test_reel_script_generator.py
git commit -m "feat(reels): enganchar auditor de consistencia de marca en ReelScriptGenerator

HALLAZGO 78 — hook_text/tag_cta/narration_script/scene_prompts pasan por
audit_brand_consistency tras generarse. Campos marcados (excepto
scene_prompts, que solo se loggea) se reescriben con
rewrite_for_brand_consistency antes de devolver el resultado."
```

---

### Task 3: Integrar en `TextGenerator`

**Files:**
- Modify: `core/content_pipeline/generators/text_generator.py`
- Test: `core/content_pipeline/tests/test_text_generator.py`

**Interfaces:**
- Consumes: mismas dos funciones de Task 1.

- [ ] **Step 1: Agregar el fixture `autouse` que protege los tests existentes ANTES de tocar el codigo de produccion**

Mismo motivo que en Task 2 — el import nuevo en `text_generator.py` haria que cualquier test sin mock propio dispare una llamada real a Vertex AI.

En `core/content_pipeline/tests/test_text_generator.py`, agregar despues de la funcion `_mock_vertex_client` (despues de la linea `return mock_client`, antes de la primera funcion `def test_...`):

```python
@pytest.fixture(autouse=True)
def _mock_brand_consistency_qc():
    with patch('core.content_pipeline.generators.text_generator.audit_brand_consistency', return_value={}) as mock_audit, \
         patch('core.content_pipeline.generators.text_generator.rewrite_for_brand_consistency') as mock_rewrite:
        yield mock_audit, mock_rewrite
```

- [ ] **Step 2: Escribir el test que falla — solo la caption marcada se reescribe**

Agregar a `core/content_pipeline/tests/test_text_generator.py`:

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_rewrites_only_flagged_caption(brand_dna, _mock_brand_consistency_qc):
    from core.content_pipeline.generators.text_generator import TextGenerator
    mock_audit, mock_rewrite = _mock_brand_consistency_qc
    mock_audit.return_value = {'post_2': 'connotacion inferior'}
    mock_rewrite.return_value = 'Caption corregida'
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(brand_dna)

    mock_audit.assert_called_once()
    mock_rewrite.assert_called_once_with('post_2', 'Post 3: tu marca online', 'connotacion inferior', brand_dna)
    assert result[2]['caption'] == 'Caption corregida'
    assert result[0]['caption'] == 'Post 1: diseno que convierte'
    assert result[1]['caption'] == 'Post 2: presencia digital'
```

- [ ] **Step 3: Correr el test y verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_text_generator.py::test_generate_rewrites_only_flagged_caption -v`
Expected: FAIL — `AttributeError: <module 'core.content_pipeline.generators.text_generator'> does not have the attribute 'audit_brand_consistency'` (el fixture `_mock_brand_consistency_qc` intenta mockear un nombre que todavia no existe en el modulo — se resuelve en el Step 4).

- [ ] **Step 4: Wirear la integracion en `text_generator.py`**

En `core/content_pipeline/generators/text_generator.py`, agregar el import despues de la linea 10 (`from core.shared.rate_limiter import call_with_429_retry`):

```python
from core.content_pipeline.generators.brand_consistency_qc import audit_brand_consistency, rewrite_for_brand_consistency
```

En el metodo `TextGenerator.generate()`, insertar el bloque de auditoria justo despues del `for i, post in enumerate(posts):` que asigna `pillar`/`format` (despues de la linea que dice `post['format'] = 'single'` y su bloque, antes de `if _is_sensitive_niche(brand_dna) or not brand_dna.business_url:`). El metodo queda:

```python
        for i, post in enumerate(posts):
            pillar = CONTENT_PILLARS[i] if i < len(CONTENT_PILLARS) else None
            post['pillar'] = pillar['name'] if pillar else ''
            if pillar and pillar['day'] == REEL_DAY:
                post['format'] = 'reel'
            elif pillar and pillar['day'] == CAROUSEL_DAY:
                post['format'] = 'carousel'
            else:
                post['format'] = 'single'

        captions_to_audit = {f"post_{i}": p['caption'] for i, p in enumerate(posts)}
        issues = audit_brand_consistency(captions_to_audit, brand_dna)
        for field_name, reason in issues.items():
            idx = int(field_name.split('_')[1])
            posts[idx]['caption'] = rewrite_for_brand_consistency(
                field_name, posts[idx]['caption'], reason, brand_dna,
            )

        if _is_sensitive_niche(brand_dna) or not brand_dna.business_url:
            logger.info(f"Auditando captions para '{brand_dna.business_name}' (nicho sensible o sin business_url)")
            for post in posts:
                post['caption'] = self._ensure_safe_caption(post['caption'], brand_dna, max_qc_retries)
        return posts
```

- [ ] **Step 5: Correr el test y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_text_generator.py::test_generate_rewrites_only_flagged_caption -v`
Expected: PASS

- [ ] **Step 6: Agregar el test que confirma que la auditoria corre siempre**

Agregar a `core/content_pipeline/tests/test_text_generator.py`:

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_runs_brand_consistency_audit_for_normal_business(brand_dna, _mock_brand_consistency_qc):
    from core.content_pipeline.generators.text_generator import TextGenerator
    mock_audit, _ = _mock_brand_consistency_qc
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        gen.generate(brand_dna)

    mock_audit.assert_called_once()
    called_fields = mock_audit.call_args.args[0]
    assert len(called_fields) == 7
```

Nota: `brand_dna` (el fixture normal de este archivo, no `sensitive_brand_dna`) tiene `business_url` presente y no es nicho sensible — es exactamente el caso donde `_ensure_safe_caption`/`_validate_caption_safety` se SALTA (ver `test_generate_skips_safety_qc_for_normal_business` ya existente). Este test confirma que, a diferencia de esa QC, la nueva auditoria de consistencia de marca corre de todas formas.

- [ ] **Step 7: Correr toda la suite del archivo y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_text_generator.py -v`
Expected: PASS (todos los tests preexistentes + 2 nuevos)

- [ ] **Step 8: Correr la suite completa de content_pipeline para verificar que no hay regresiones**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/ -v`
Expected: PASS (todos, sin nuevas fallas frente al baseline — las 12 fallas preexistentes por `ImportError: cannot import name 'speech' from 'google.cloud'` y las de `test_tasks.py`/`test_usage_limits.py`/`test_domain.py` ya documentadas como preexistentes en esta sesión NO cuentan como regresion; si aparecen fallas nuevas relacionadas a `brand_consistency_qc`, investigar antes de continuar)

- [ ] **Step 9: Commit**

```bash
git add core/content_pipeline/generators/text_generator.py core/content_pipeline/tests/test_text_generator.py
git commit -m "feat(content-pipeline): enganchar auditor de consistencia de marca en TextGenerator

HALLAZGO 78 — las 7 captions de un calendario pasan por
audit_brand_consistency en una sola llamada, sin condicionar a nicho
sensible ni a business_url (a diferencia de caption_safety_qc). Solo
las marcadas se reescriben con rewrite_for_brand_consistency."
```

---

## Verificación final (después de las 3 tareas)

- [ ] Correr `docker compose exec -T backend python -m pytest core/content_pipeline/ -v` completo una vez más y confirmar 0 fallas nuevas.
- [ ] Actualizar `hallazgos.txt`: agregar una entrada de seguimiento a HALLAZGO 78 indicando "🟢 RESUELTO — implementado auditor de consistencia de marca (`brand_consistency_qc.py`), ver commits [hashes]" — seguir el mismo formato de actualización ya usado en HALLAZGO 7/20/21 dentro del mismo archivo (bloque `ACTUALIZACIÓN <fecha> — RESUELTO...` debajo del hallazgo original, sin borrar el texto original).
