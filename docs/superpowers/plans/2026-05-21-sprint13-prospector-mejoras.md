# Sprint 13 — Prospector: Dedup, Scoring y Follow-up

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mejorar el prospector de mapas con tres capacidades: deduplicación de negocios por `place_id` (no repetir prospectos ya vistos), scoring automático con Gemini (ordenar por potencial), y seguimiento de contacto (el bot recuerda y pregunta si se contactaron los prospectos anteriores).

**Architecture:** Nuevo modelo `ProspectLead` almacena leads con `place_id` por usuario. El n8n workflow existente se modifica para devolver el array de leads (no solo `total`). `prospect_n8n_job` filtra duplicados, guarda nuevos, pide scoring a Gemini y formatea resultado. `cmd_prospectar` muestra aviso de leads sin contactar. Nuevo comando `/contactado` marca el batch reciente como contactado.

**Tech Stack:** Django 5.2, PostgreSQL, n8n (workflow update via REST API), Gemini 2.5 Flash (scoring), python-telegram-bot.

---

### Task 1: Modelo ProspectLead + migración

**Files:**
- Modify: `core/agent/infrastructure/models.py`
- Create: `core/agent/migrations/0006_prospectlead.py`
- Create: `core/agent/tests/test_sprint13_prospector.py`

- [ ] **Step 1: Escribir tests del modelo**

```python
# core/agent/tests/test_sprint13_prospector.py
import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


class TestProspectLead:
    def test_unique_per_user_and_place(self):
        """Mismo place_id para el mismo chat_id lanza IntegrityError."""
        from core.agent.infrastructure.models import ProspectLead
        from django.db import IntegrityError
        ProspectLead.objects.create(
            place_id='ChIJXXX', chat_id='123', name='Plomería ABC',
            giro='plomeros', lat=25.67, lng=-100.31,
        )
        with pytest.raises(IntegrityError):
            ProspectLead.objects.create(
                place_id='ChIJXXX', chat_id='123', name='Plomería ABC duplicada',
                giro='plomeros', lat=25.67, lng=-100.31,
            )

    def test_different_users_can_have_same_place(self):
        """Mismo place_id para distinto chat_id es válido."""
        from core.agent.infrastructure.models import ProspectLead
        ProspectLead.objects.create(
            place_id='ChIJYYY', chat_id='111', name='Salon A', giro='salones',
        )
        ProspectLead.objects.create(
            place_id='ChIJYYY', chat_id='222', name='Salon A', giro='salones',
        )
        assert ProspectLead.objects.filter(place_id='ChIJYYY').count() == 2

    def test_contacted_defaults_to_false(self):
        from core.agent.infrastructure.models import ProspectLead
        lead = ProspectLead.objects.create(
            place_id='ChIJZZZ', chat_id='123', name='Test', giro='test',
        )
        assert lead.contacted is False
        assert lead.contacted_at is None

    def test_score_is_nullable(self):
        from core.agent.infrastructure.models import ProspectLead
        lead = ProspectLead.objects.create(
            place_id='ChIJAAA', chat_id='123', name='Test', giro='test',
        )
        assert lead.score is None
```

- [ ] **Step 2: Correr tests para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_prospector.py -q
```

Expected: FAIL — `ProspectLead` no existe en models.

- [ ] **Step 3: Añadir `ProspectLead` a `models.py`**

Añadir al final del archivo `core/agent/infrastructure/models.py`:

```python
class ProspectLead(models.Model):
    place_id = models.CharField(max_length=255)
    chat_id = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=500, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    website = models.CharField(max_length=500, blank=True)
    rating = models.FloatField(null=True, blank=True)
    reviews_total = models.IntegerField(default=0)
    giro = models.CharField(max_length=255, blank=True)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    score = models.IntegerField(null=True, blank=True)
    score_reason = models.CharField(max_length=255, blank=True)
    contacted = models.BooleanField(default=False)
    contacted_at = models.DateTimeField(null=True, blank=True)
    searched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('place_id', 'chat_id')]
        ordering = ['-searched_at', '-score']
```

- [ ] **Step 4: Generar la migración**

```bash
docker exec chatbot-backend-1 python manage.py makemigrations core_agent --name=prospectlead
docker exec chatbot-backend-1 python manage.py migrate
```

Expected: `Migrations for 'core_agent': core/agent/migrations/0006_prospectlead.py` y `Running migrations: Applying core_agent.0006_prospectlead... OK`

- [ ] **Step 5: Correr tests del modelo**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_prospector.py::TestProspectLead -q
```

Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add core/agent/infrastructure/models.py core/agent/migrations/0006_prospectlead.py core/agent/tests/test_sprint13_prospector.py
GIT_EDITOR=true git commit -m "feat: add ProspectLead model for dedup and follow-up tracking"
```

---

### Task 2: Modificar el workflow n8n para devolver leads

El workflow de prospección en n8n actualmente responde `{"total": N}` al webhook. Necesita devolver también el array de leads con `place_id`.

**Files:** ninguno en Django — este task es en la UI de n8n.

- [ ] **Step 1: Abrir n8n y localizar el workflow**

```
http://localhost:5678
```

Buscar el workflow con nombre similar a "Prospector" o "Google Maps" o el que responde al webhook configurado en `N8N_WEBHOOK_URL` (`/webhook/prospector`).

- [ ] **Step 2: Identificar el nodo de respuesta del webhook**

El workflow tiene un nodo tipo **"Respond to Webhook"** o un nodo HTTP Request que sirve como respuesta final. Localizar el nodo de code/formateo de datos previo.

- [ ] **Step 3: Actualizar el Code node de formateo**

En el nodo Code (o "Formatear Datos") que construye la respuesta, reemplazar el código para incluir `leads`:

```javascript
// Asume que el nodo anterior entrega un array de places de Google Places API
// Cada item tiene: place_id, name, formatted_address, formatted_phone_number,
//                  website, rating, user_ratings_total, geometry.location.lat/lng
const places = $input.all();

const leads = places.map(p => ({
  place_id: p.json.place_id || '',
  name: p.json.name || '',
  address: p.json.formatted_address || p.json.vicinity || '',
  phone: p.json.formatted_phone_number || p.json.national_phone_number || '',
  website: p.json.website || '',
  rating: p.json.rating || null,
  reviews_total: p.json.user_ratings_total || 0,
  lat: p.json.geometry?.location?.lat || null,
  lng: p.json.geometry?.location?.lng || null,
}));

return [{
  json: {
    total: leads.length,
    leads: leads,
  }
}];
```

- [ ] **Step 4: Activar el workflow y probar desde Telegram**

Enviar al bot: `/prospectar plomeros "Monterrey" 3`

Verificar en logs del rqworker que el job recibe `leads` en la respuesta:

```bash
docker logs chatbot-rqworker-1 --tail 30 | grep -E "prospect|leads|total"
```

Expected: log con `total: N, leads: [...]` (ajustar el log en el siguiente task).

---

### Task 3: Dedup + almacenamiento en prospect_n8n_job

**Files:**
- Modify: `core/agent/infrastructure/jobs.py`

- [ ] **Step 1: Escribir tests de dedup**

En `test_sprint13_prospector.py`, añadir:

```python
class TestProspectN8nJobDedup:
    def _make_lead(self, place_id='ChIJAAA', name='Test'):
        return {
            'place_id': place_id,
            'name': name,
            'address': 'Calle 1',
            'phone': '8181234567',
            'website': '',
            'rating': 4.2,
            'reviews_total': 50,
            'lat': 25.67,
            'lng': -100.31,
        }

    def test_stores_new_leads(self):
        from core.agent.infrastructure.models import ProspectLead
        from unittest.mock import patch
        leads = [self._make_lead('PlaceA'), self._make_lead('PlaceB')]
        with patch('core.agent.infrastructure.jobs.requests.post') as mock_n8n, \
             patch('core.agent.infrastructure.jobs._send_telegram'), \
             patch('core.agent.infrastructure.jobs._score_leads_with_gemini',
                   side_effect=lambda leads, giro: leads):
            mock_n8n.return_value.json.return_value = {'total': 2, 'leads': leads}
            mock_n8n.return_value.raise_for_status = lambda: None
            from core.agent.infrastructure.jobs import prospect_n8n_job
            prospect_n8n_job(giro='plomeros', lat=25.67, lng=-100.31,
                             rango_km=5.0, chat_id=123)
        assert ProspectLead.objects.filter(chat_id='123').count() == 2

    def test_skips_duplicate_place_ids(self):
        from core.agent.infrastructure.models import ProspectLead
        from unittest.mock import patch
        ProspectLead.objects.create(
            place_id='PlaceA', chat_id='123', name='Ya existe', giro='plomeros',
        )
        leads = [self._make_lead('PlaceA'), self._make_lead('PlaceB')]
        with patch('core.agent.infrastructure.jobs.requests.post') as mock_n8n, \
             patch('core.agent.infrastructure.jobs._send_telegram'), \
             patch('core.agent.infrastructure.jobs._score_leads_with_gemini',
                   side_effect=lambda leads, giro: leads):
            mock_n8n.return_value.json.return_value = {'total': 2, 'leads': leads}
            mock_n8n.return_value.raise_for_status = lambda: None
            from core.agent.infrastructure.jobs import prospect_n8n_job
            prospect_n8n_job(giro='plomeros', lat=25.67, lng=-100.31,
                             rango_km=5.0, chat_id=123)
        # Solo PlaceB es nuevo
        assert ProspectLead.objects.filter(chat_id='123').count() == 2
        new_lead = ProspectLead.objects.get(place_id='PlaceB', chat_id='123')
        assert new_lead.name == 'Test'

    def test_sends_telegram_with_new_count(self):
        from unittest.mock import patch, call
        leads = [self._make_lead('PlaceX')]
        with patch('core.agent.infrastructure.jobs.requests.post') as mock_n8n, \
             patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             patch('core.agent.infrastructure.jobs._score_leads_with_gemini',
                   side_effect=lambda leads, giro: leads):
            mock_n8n.return_value.json.return_value = {'total': 1, 'leads': leads}
            mock_n8n.return_value.raise_for_status = lambda: None
            from core.agent.infrastructure.jobs import prospect_n8n_job
            prospect_n8n_job(giro='plomeros', lat=25.67, lng=-100.31,
                             rango_km=5.0, chat_id=456)
        mock_tg.assert_called_once()
        msg = mock_tg.call_args[0][1]
        assert '1' in msg  # total de nuevos
```

- [ ] **Step 2: Correr tests para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_prospector.py::TestProspectN8nJobDedup -q
```

Expected: FAIL — la función `prospect_n8n_job` no tiene dedup ni `_score_leads_with_gemini`.

- [ ] **Step 3: Reescribir `prospect_n8n_job` en `jobs.py`**

Localizar la función `prospect_n8n_job` (actualmente ~línea 75 en jobs.py) y reemplazarla completamente:

```python
def prospect_n8n_job(giro: str, lat: float, lng: float, rango_km: float, chat_id: int) -> None:
    """Llama al webhook de n8n, deduplica por place_id, aplica scoring con Gemini y notifica."""
    n8n_url = getattr(settings, 'N8N_WEBHOOK_URL', '')
    if not n8n_url:
        _send_telegram(chat_id, '❌ Error: URL de n8n no configurada.')
        return

    logger.info(f'Prospección: giro={giro}, lat={lat}, lng={lng}, rango={rango_km}km')
    try:
        resp = requests.post(
            n8n_url,
            json={'giro': giro, 'lat': lat, 'lng': lng, 'rango_km': rango_km},
            timeout=360,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.Timeout:
        logger.error('Timeout en prospección n8n')
        _send_telegram(
            chat_id,
            '⚠️ La prospección está tardando más de lo esperado. '
            'Revisa tu Google Sheet en unos minutos.',
        )
        return
    except Exception as e:
        logger.error(f'Error HTTP n8n prospección: {e}')
        _send_telegram(chat_id, f'❌ Error al conectar con n8n: {e}')
        return

    leads_raw = data.get('leads', [])
    total_found = data.get('total', len(leads_raw))

    if not leads_raw:
        _send_telegram(chat_id, f'🔍 Prospección completada — 0 resultados encontrados para *{giro}*.')
        return

    # Dedup: filtrar place_ids ya guardados para este usuario
    from core.agent.infrastructure.models import ProspectLead
    existing_ids = set(
        ProspectLead.objects.filter(
            chat_id=str(chat_id),
            place_id__in=[l['place_id'] for l in leads_raw if l.get('place_id')],
        ).values_list('place_id', flat=True)
    )
    new_leads = [l for l in leads_raw if l.get('place_id') and l['place_id'] not in existing_ids]
    duplicates = total_found - len(new_leads)

    if not new_leads:
        _send_telegram(
            chat_id,
            f'🔍 Prospección completada — todos los {total_found} resultados ya estaban en tu historial.',
        )
        return

    # Scoring con Gemini
    new_leads = _score_leads_with_gemini(new_leads, giro)

    # Guardar en BD (ordenados por score desc)
    new_leads.sort(key=lambda l: l.get('score', 0), reverse=True)
    for lead in new_leads:
        try:
            ProspectLead.objects.create(
                place_id=lead['place_id'],
                chat_id=str(chat_id),
                name=lead.get('name', ''),
                address=lead.get('address', ''),
                phone=lead.get('phone', ''),
                website=lead.get('website', ''),
                rating=lead.get('rating'),
                reviews_total=lead.get('reviews_total', 0),
                giro=giro,
                lat=lead.get('lat'),
                lng=lead.get('lng'),
                score=lead.get('score'),
                score_reason=lead.get('score_reason', ''),
            )
        except Exception as e:
            logger.warning(f'Error guardando lead {lead.get("place_id")}: {e}')

    # Formatear mensaje para Telegram (top 10)
    top = new_leads[:10]
    lines = [f'✅ *{len(new_leads)} nuevos prospectos* encontrados para *{giro}*']
    if duplicates > 0:
        lines.append(f'_(+{duplicates} ya vistos anteriormente, omitidos)_')
    lines.append('')
    for i, lead in enumerate(top, 1):
        score_emoji = '🔥' if (lead.get('score') or 0) >= 8 else '⭐' if (lead.get('score') or 0) >= 5 else '📍'
        phone_line = f' · 📞 {lead["phone"]}' if lead.get('phone') else ''
        web_line = f' · 🌐 sin web' if not lead.get('website') else ''
        score_line = f' · Score: {lead.get("score", "?")}/10' if lead.get('score') else ''
        lines.append(
            f'{score_emoji} *{i}. {lead.get("name", "Sin nombre")}*{score_line}\n'
            f'_{lead.get("address", "")}{phone_line}{web_line}_'
        )
    if len(new_leads) > 10:
        lines.append(f'\n_...y {len(new_leads) - 10} más guardados en tu historial._')
    lines.append('\nUsa `/contactado` cuando hayas contactado este batch.')
    _send_telegram(chat_id, '\n'.join(lines))
```

- [ ] **Step 4: Correr los tests de dedup**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_prospector.py::TestProspectN8nJobDedup -q
```

Expected: FAIL en `_score_leads_with_gemini` — función no existe aún.

- [ ] **Step 5: Commit parcial del job**

```bash
git add core/agent/infrastructure/jobs.py
GIT_EDITOR=true git commit -m "feat: prospect_n8n_job with dedup by place_id and ProspectLead storage"
```

---

### Task 4: Scoring de leads con Gemini

**Files:**
- Modify: `core/agent/infrastructure/jobs.py`

- [ ] **Step 1: Escribir test de scoring**

En `test_sprint13_prospector.py`, añadir:

```python
class TestScoreLeadsWithGemini:
    def _leads(self):
        return [
            {'place_id': 'A', 'name': 'Sin web', 'phone': '8181234567',
             'website': '', 'rating': 4.5, 'reviews_total': 100},
            {'place_id': 'B', 'name': 'Con web', 'phone': '',
             'website': 'https://example.com', 'rating': 3.0, 'reviews_total': 5},
        ]

    def test_adds_score_field_to_each_lead(self):
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        from unittest.mock import patch
        import json
        fake_scores = json.dumps([
            {'score': 9, 'reason': 'sin web, teléfono disponible'},
            {'score': 4, 'reason': 'tiene web, sin teléfono'},
        ])
        with patch.object(GeminiAdapter, 'generate_response', return_value=fake_scores), \
             override_settings(GEMINI_API_KEY='key'):
            from core.agent.infrastructure.jobs import _score_leads_with_gemini
            result = _score_leads_with_gemini(self._leads(), 'plomeros')
        assert result[0]['score'] == 9
        assert result[0]['score_reason'] == 'sin web, teléfono disponible'
        assert result[1]['score'] == 4

    def test_uses_default_score_5_on_gemini_error(self):
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        from unittest.mock import patch
        with patch.object(GeminiAdapter, 'generate_response', return_value='invalid json'), \
             override_settings(GEMINI_API_KEY='key'):
            from core.agent.infrastructure.jobs import _score_leads_with_gemini
            result = _score_leads_with_gemini(self._leads(), 'plomeros')
        assert all(l.get('score') == 5 for l in result)
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_prospector.py::TestScoreLeadsWithGemini -q
```

Expected: FAIL — `_score_leads_with_gemini` no existe.

- [ ] **Step 3: Añadir `_score_leads_with_gemini` en `jobs.py`**

Añadir después de los imports en `jobs.py` (al inicio del módulo, antes de `_SOCIAL_DOMAINS`):

```python
import json
import re
```

Y añadir la función antes de `prospect_n8n_job`:

```python
def _score_leads_with_gemini(leads: list, giro: str) -> list:
    """Llama a Gemini para asignar score 1-10 a cada lead. Modifica la lista in-place y la retorna."""
    from core.agent.infrastructure.gemini_adapter import GeminiAdapter
    gemini = GeminiAdapter()
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        for lead in leads:
            lead.setdefault('score', 5)
            lead.setdefault('score_reason', '')
        return leads

    model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')
    leads_summary = json.dumps([{
        'name': l.get('name', ''),
        'phone': l.get('phone', ''),
        'website': l.get('website', ''),
        'rating': l.get('rating'),
        'reviews': l.get('reviews_total', 0),
    } for l in leads], ensure_ascii=False)

    prompt = (
        f'Eres un asesor de ventas digitales. Analiza estos negocios del giro "{giro}" '
        f'y asigna a cada uno un score del 1 al 10 como posible cliente para una agencia web.\n\n'
        f'Criterios de puntuación:\n'
        f'- Sin website: +4 puntos (necesitan uno urgente)\n'
        f'- Teléfono disponible: +3 puntos (podemos contactarlos)\n'
        f'- Rating >= 3.5: +2 puntos (negocio activo y valorado)\n'
        f'- Muchas reseñas (>50): +1 punto (negocio establecido)\n\n'
        f'Negocios (en este orden exacto):\n{leads_summary}\n\n'
        f'Responde SOLO con un JSON array con un objeto por negocio, '
        f'en el mismo orden, con campos "score" (int 1-10) y "reason" (frase corta en español). '
        f'Ejemplo: [{{"score": 9, "reason": "sin web, teléfono disponible"}}]'
    )

    try:
        raw = gemini.generate_response(
            prompt=prompt, api_key=api_key, model_name=model, thinking_budget=0
        )
        json_str = re.sub(r'^```json\n?|^```\n?|```$', '', raw.strip(), flags=re.MULTILINE).strip()
        scores = json.loads(json_str)
        for i, lead in enumerate(leads):
            if i < len(scores):
                lead['score'] = int(scores[i].get('score', 5))
                lead['score_reason'] = str(scores[i].get('reason', ''))
            else:
                lead.setdefault('score', 5)
                lead.setdefault('score_reason', '')
    except Exception as e:
        logger.warning(f'Error scoring con Gemini: {e} — usando score 5 por defecto')
        for lead in leads:
            lead.setdefault('score', 5)
            lead.setdefault('score_reason', '')

    return leads
```

- [ ] **Step 4: Correr todos los tests de prospector**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_prospector.py -q
```

Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add core/agent/infrastructure/jobs.py
GIT_EDITOR=true git commit -m "feat: add Gemini lead scoring to prospect_n8n_job"
```

---

### Task 5: Follow-up en cmd_prospectar y comando /contactado

**Files:**
- Modify: `core/agent/management/commands/run_telegram_bot.py`

- [ ] **Step 1: Escribir tests de follow-up**

En `test_sprint13_prospector.py`, añadir:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

class TestFollowUp:
    @pytest.fixture
    def fake_update(self):
        update = MagicMock()
        update.effective_user.id = 1
        update.effective_user.username = 'user'
        update.effective_user.full_name = 'User'
        update.effective_chat.id = 999
        update.message.reply_text = AsyncMock()
        return update

    def test_prospectar_shows_uncontacted_warning(self, fake_update):
        """Si hay leads sin contactar, cmd_prospectar muestra aviso antes de buscar."""
        from core.agent.infrastructure.models import ProspectLead
        from django.utils import timezone
        ProspectLead.objects.create(
            place_id='OldLead1', chat_id='999', name='Negocio anterior',
            giro='plomeros', contacted=False,
        )
        context = MagicMock()
        context.args = ['plomeros', 'Monterrey']
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session') as mock_sess, \
             patch('core.agent.management.commands.run_telegram_bot.run_tool') as mock_tool:
            mock_sess.return_value = MagicMock(is_authorized=True, id=1)
            mock_tool.return_value = MagicMock(
                success=True, content='⏳ Prospección iniciada...'
            )
            from core.agent.management.commands.run_telegram_bot import cmd_prospectar
            asyncio.get_event_loop().run_until_complete(cmd_prospectar(fake_update, context))
        # Debe haber enviado al menos 2 mensajes: el aviso + el resultado
        assert fake_update.message.reply_text.call_count >= 2
        calls_text = [str(c) for c in fake_update.message.reply_text.call_args_list]
        assert any('contactar' in t.lower() or 'contactado' in t.lower() for t in calls_text)

    def test_cmd_contactado_marks_recent_leads(self, fake_update):
        """cmd_contactado marca todos los leads no-contactados del usuario como contactados."""
        from core.agent.infrastructure.models import ProspectLead
        ProspectLead.objects.create(
            place_id='Lead1', chat_id='999', name='N1', giro='plomeros', contacted=False,
        )
        ProspectLead.objects.create(
            place_id='Lead2', chat_id='999', name='N2', giro='plomeros', contacted=False,
        )
        context = MagicMock()
        context.args = []
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session') as mock_sess:
            mock_sess.return_value = MagicMock(is_authorized=True, id=1)
            from core.agent.management.commands.run_telegram_bot import cmd_contactado
            asyncio.get_event_loop().run_until_complete(cmd_contactado(fake_update, context))
        assert ProspectLead.objects.filter(chat_id='999', contacted=False).count() == 0
        assert ProspectLead.objects.filter(chat_id='999', contacted=True).count() == 2
```

- [ ] **Step 2: Correr tests para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_prospector.py::TestFollowUp -q
```

Expected: FAIL — `cmd_contactado` no existe y `cmd_prospectar` no tiene el chequeo.

- [ ] **Step 3: Añadir chequeo de follow-up en `cmd_prospectar`**

Localizar `cmd_prospectar` en `run_telegram_bot.py` (línea 579). Añadir el bloque de follow-up después del chequeo de autorización (después de la línea `if not session.is_authorized`), antes de `send_chat_action`:

```python
    # Verificar leads sin contactar de los últimos 30 días
    from asgiref.sync import sync_to_async
    from core.agent.infrastructure.models import ProspectLead
    from django.utils import timezone
    from datetime import timedelta

    chat_id_str = str(update.effective_chat.id)

    @sync_to_async
    def _count_uncontacted():
        cutoff = timezone.now() - timedelta(days=30)
        return ProspectLead.objects.filter(
            chat_id=chat_id_str,
            contacted=False,
            searched_at__gte=cutoff,
        ).count()

    uncontacted = await _count_uncontacted()
    if uncontacted > 0:
        await update.message.reply_text(
            f'📋 Tienes *{uncontacted} prospectos* sin contactar de búsquedas anteriores.\n'
            f'Usa `/contactado` para marcarlos como contactados cuando los hayas llamado.',
            parse_mode='Markdown',
        )
```

- [ ] **Step 4: Añadir `cmd_contactado` en `run_telegram_bot.py`**

Añadir después de `cmd_prospectar`:

```python
async def cmd_contactado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/contactado — marca todos los prospectos recientes como contactados"""
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return

    from asgiref.sync import sync_to_async
    from core.agent.infrastructure.models import ProspectLead
    from django.utils import timezone
    from datetime import timedelta

    chat_id_str = str(update.effective_chat.id)

    @sync_to_async
    def _mark_contacted():
        cutoff = timezone.now() - timedelta(days=30)
        qs = ProspectLead.objects.filter(
            chat_id=chat_id_str,
            contacted=False,
            searched_at__gte=cutoff,
        )
        count = qs.count()
        qs.update(contacted=True, contacted_at=timezone.now())
        return count

    count = await _mark_contacted()
    if count == 0:
        await update.message.reply_text('No hay prospectos pendientes de los últimos 30 días.')
    else:
        await update.message.reply_text(
            f'✅ *{count} prospectos* marcados como contactados.',
            parse_mode='Markdown',
        )
```

- [ ] **Step 5: Registrar `/contactado`**

Localizar el bloque de `app.add_handler(CommandHandler('prospectar', cmd_prospectar))` (línea ~922) y añadir:

```python
        app.add_handler(CommandHandler('contactado', cmd_contactado))
```

- [ ] **Step 6: Actualizar `/ayuda`**

Añadir después de la línea de ayuda de `/prospectar`:

```python
"✅ */contactado*\n"
"Marca como contactados los prospectos del batch más reciente.\n\n"
```

- [ ] **Step 7: Correr todos los tests de prospector**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_prospector.py -q
```

Expected: todos PASS.

- [ ] **Step 8: Commit**

```bash
git add core/agent/management/commands/run_telegram_bot.py
GIT_EDITOR=true git commit -m "feat: follow-up tracking in /prospectar and new /contactado command"
```

---

### Task 6: Suite completa y prueba end-to-end

- [ ] **Step 1: Correr suite completa**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q
```

Expected: todos los tests pasan.

- [ ] **Step 2: Restart para cargar cambios**

```bash
docker compose restart backend rqworker telegram_bot
```

- [ ] **Step 3: Probar en Telegram — primera prospección**

Enviar: `/prospectar plomeros "Guadalajara" 5`

Expected:
1. Bot responde "⏳ Prospección iniciada..."
2. En 2-5 minutos llega mensaje con lista de prospectos con scores (🔥 para 8+, ⭐ para 5+)
3. Mensaje incluye "Usa `/contactado` cuando hayas contactado este batch"

- [ ] **Step 4: Probar dedup — segunda prospección igual zona**

Enviar de nuevo: `/prospectar plomeros "Guadalajara" 5`

Expected:
1. Aviso: "📋 Tienes X prospectos sin contactar..."
2. Resultado de segunda búsqueda indica cuántos son nuevos y cuántos duplicados

- [ ] **Step 5: Probar /contactado**

Enviar: `/contactado`

Expected: "✅ X prospectos marcados como contactados"

- [ ] **Step 6: Verificar en BD que se guardaron**

```bash
docker exec chatbot-db-1 psql -U $DB_USER -d $DB_NAME -c "SELECT place_id, name, score, contacted FROM core_agent_prospectlead LIMIT 5;"
```

Expected: filas con `score` (1-10) y `contacted = true` tras el paso anterior.

- [ ] **Step 7: Commit final**

```bash
GIT_EDITOR=true git commit -m "sprint 13b completo: dedup+scoring+follow-up en prospector de mapas"
```

---

## Self-Review

**Cobertura del spec:**
- ✅ Deduplicación por `place_id` — Task 1 (modelo), Task 3 (filtrado en job)
- ✅ Scoring de leads con Gemini (criterios: sin web, teléfono, rating) — Task 4
- ✅ Follow-up tracking: aviso de prospectos sin contactar en `/prospectar` — Task 5
- ✅ `/contactado` para marcar batch como contactado — Task 5

**Placeholder scan:** ninguno. Todos los code blocks son código completo ejecutable.

**Type consistency:**
- `ProspectLead.chat_id` es `CharField` → siempre convertir `int` a `str` con `str(chat_id)` al guardar/filtrar.
- `_score_leads_with_gemini(leads, giro)` recibe `list[dict]` y devuelve la misma lista modificada in-place (también la retorna para encadenamiento).
- `prospect_n8n_job` usa `data.get('leads', [])` — si n8n no devuelve leads, el job termina gracefully sin crash.
- Los tests mockean `_score_leads_with_gemini` con `side_effect=lambda leads, giro: leads` para aislar los tests de dedup del scoring.
