# Cleanup DIALOGIX + Sprint 5 Pendientes — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Completar los ítems pendientes del Sprint 5, limpiar todos los módulos DIALOGIX que el agente no usa, y dejar el proyecto listo para un repo nuevo en GitHub.

**Architecture:** Mover GeminiAdapter a `core/agent/`, actualizar la API de Google Gemini al SDK nuevo, eliminar 5 módulos legacy, mejorar el Admin, crear documentación del agente. En ese orden — primero migrar lo que se necesita, luego borrar lo que sobra, luego verificar que los tests pasen.

**Tech Stack:** Django 5.2, pytest-django, google-genai (nuevo SDK), docker compose, gh CLI (GitHub CLI)

---

## Mapa de archivos

| Acción | Archivo |
|---|---|
| Crear | `core/agent/infrastructure/gemini_adapter.py` |
| Modificar | `core/agent/infrastructure/embedding_service.py` |
| Modificar | `core/agent/application/agent_service.py` |
| Modificar | `core/agent/infrastructure/tools/content_tools.py` |
| Modificar | `core/agent/admin.py` |
| Modificar | `saas_chatbot/settings.py` |
| Modificar | `saas_chatbot/urls.py` |
| Modificar | `requirements.txt` |
| Modificar | `docs/index.md` |
| Eliminar | `core/routing_escalation/` |
| Eliminar | `core/channel_integration/` |
| Eliminar | `core/conversation_management/` |
| Eliminar | `core/tenant_management/` |
| Eliminar | `core/ai_processing/` |

---

## Task 1: Migrar google.generativeai → google.genai en embedding_service.py

El paquete `google-generativeai` está deprecado. El nuevo es `google-genai` con API diferente. Este es el único archivo del agente que lo usa (ai_processing/adapters.py se borra en Task 4).

**Files:**
- Modify: `core/agent/infrastructure/embedding_service.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Verificar que el test de embeddings pasa actualmente**

```bash
docker compose exec backend pytest core/agent/tests/ -k "embedding" -v
```
Anotar los tests que pasan ahora para comparar después.

- [ ] **Step 2: Actualizar requirements.txt**

Cambiar en `requirements.txt`:
```
# Antes:
google-generativeai

# Después:
google-genai>=0.8.0
```

- [ ] **Step 3: Reescribir embedding_service.py con el nuevo SDK**

Reemplazar el contenido completo de `core/agent/infrastructure/embedding_service.py`:

```python
"""
Servicio de embeddings usando Gemini text-embedding-004 (768 dimensiones).
Sin dependencias pesadas — usa la misma API key que el agente.
"""
import logging
from typing import Optional
from django.conf import settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = 'models/text-embedding-004'
EMBEDDING_DIMENSIONS = 768


def _client():
    from google import genai
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise ValueError("GEMINI_API_KEY no configurada")
    return genai.Client(api_key=api_key)


def get_embedding(text: str) -> Optional[list[float]]:
    """Genera un embedding para el texto dado. Retorna None en caso de error."""
    if not text or not text.strip():
        return None
    try:
        client = _client()
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text[:8000],
        )
        return result.embeddings[0].values
    except Exception as e:
        logger.warning(f"Error generando embedding: {e}")
        return None


def get_query_embedding(text: str) -> Optional[list[float]]:
    """Embedding para consultas (semánticamente optimizado para búsqueda)."""
    if not text or not text.strip():
        return None
    try:
        client = _client()
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text[:2000],
        )
        return result.embeddings[0].values
    except Exception as e:
        logger.warning(f"Error generando query embedding: {e}")
        return None
```

- [ ] **Step 4: Instalar el nuevo paquete en el contenedor**

```bash
docker compose exec backend pip install google-genai>=0.8.0
```

- [ ] **Step 5: Correr tests de embeddings**

```bash
docker compose exec backend pytest core/agent/tests/ -k "embedding" -v
```
Esperado: mismos tests en verde.

- [ ] **Step 6: Rebuild imagen para que el requirements.txt quede permanente**

```bash
docker compose build backend
```

---

## Task 2: Crear gemini_adapter.py en core/agent/

El agente importa `GeminiAdapter` desde `core.ai_processing` — ese módulo se va a borrar. Antes de borrarlo, hay que traer solo lo que el agente necesita: `generate_response` con retry y backoff.

**Files:**
- Create: `core/agent/infrastructure/gemini_adapter.py`
- Modify: `core/agent/application/agent_service.py` (línea 4)
- Modify: `core/agent/infrastructure/tools/content_tools.py` (línea 3)

- [ ] **Step 1: Escribir el test de regresión para generate_response**

En `core/agent/tests/test_gemini_adapter.py` (archivo nuevo):

```python
from unittest.mock import patch, MagicMock
from core.agent.infrastructure.gemini_adapter import GeminiAdapter


def test_generate_response_returns_text():
    adapter = GeminiAdapter()
    mock_response = MagicMock()
    mock_response.text = "Respuesta de prueba"

    with patch("core.agent.infrastructure.gemini_adapter.genai") as mock_genai:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value = mock_response

        result = adapter.generate_response("hola", api_key="test-key")

    assert result == "Respuesta de prueba"


def test_generate_response_retries_on_rate_limit():
    from google.api_core import exceptions as google_exceptions
    adapter = GeminiAdapter()

    with patch("core.agent.infrastructure.gemini_adapter.genai") as mock_genai:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.side_effect = [
            google_exceptions.ResourceExhausted("rate limit"),
            MagicMock(text="OK en segundo intento"),
        ]

        with patch("time.sleep"):
            result = adapter.generate_response("hola", api_key="test-key")

    assert result == "OK en segundo intento"


def test_generate_response_returns_fallback_on_error():
    adapter = GeminiAdapter()

    with patch("core.agent.infrastructure.gemini_adapter.genai") as mock_genai:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.side_effect = Exception("error fatal")

        result = adapter.generate_response("hola", api_key="test-key")

    assert "inténtalo de nuevo" in result.lower()
```

- [ ] **Step 2: Correr el test — debe fallar porque el archivo no existe aún**

```bash
docker compose exec backend pytest core/agent/tests/test_gemini_adapter.py -v
```
Esperado: `ModuleNotFoundError` o `ImportError`.

- [ ] **Step 3: Crear core/agent/infrastructure/gemini_adapter.py**

```python
import time
import random
import logging
from google import genai
from google.api_core import exceptions as google_exceptions

logger = logging.getLogger(__name__)

_FALLBACK_MSG = "Disculpa, no pudimos procesar tu solicitud en este momento. Por favor, inténtalo de nuevo más tarde."


class GeminiAdapter:
    def generate_response(
        self,
        prompt: str,
        api_key: str,
        model_name: str = 'gemini-2.5-flash',
    ) -> str:
        client = genai.Client(api_key=api_key)
        max_retries = 3
        base_delay = 2

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                return response.text
            except google_exceptions.ResourceExhausted:
                if attempt < max_retries - 1:
                    wait = base_delay * (2 ** attempt) + random.uniform(0, 0.2)
                    logger.warning(f"Rate limit Gemini, reintentando en {wait:.1f}s (intento {attempt + 1})")
                    time.sleep(wait)
                else:
                    logger.error("Rate limit Gemini: máximo de reintentos alcanzado")
                    return _FALLBACK_MSG
            except Exception as e:
                logger.error(f"Error Gemini: {e}", exc_info=True)
                return _FALLBACK_MSG

        return _FALLBACK_MSG
```

- [ ] **Step 4: Correr el test — debe pasar**

```bash
docker compose exec backend pytest core/agent/tests/test_gemini_adapter.py -v
```
Esperado: 3 tests en verde.

- [ ] **Step 5: Actualizar import en agent_service.py**

En `core/agent/application/agent_service.py`, línea 4:
```python
# Antes:
from core.ai_processing.infrastructure.adapters import GeminiAdapter

# Después:
from core.agent.infrastructure.gemini_adapter import GeminiAdapter
```

- [ ] **Step 6: Actualizar import en content_tools.py**

En `core/agent/infrastructure/tools/content_tools.py`, línea 3:
```python
# Antes:
from core.ai_processing.infrastructure.adapters import GeminiAdapter

# Después:
from core.agent.infrastructure.gemini_adapter import GeminiAdapter
```

- [ ] **Step 7: Correr todos los tests del agente para verificar que nada se rompió**

```bash
docker compose exec backend pytest core/agent/tests/ -v --tb=short
```
Esperado: todos en verde (149 + 3 nuevos = 152).

- [ ] **Step 8: Commit**

```bash
git add core/agent/infrastructure/gemini_adapter.py \
        core/agent/infrastructure/embedding_service.py \
        core/agent/application/agent_service.py \
        core/agent/infrastructure/tools/content_tools.py \
        core/agent/tests/test_gemini_adapter.py \
        requirements.txt
git commit -m "feat: migrar GeminiAdapter a core/agent y actualizar a google-genai SDK"
```

---

## Task 3: Limpiar settings.py y urls.py

Antes de borrar los módulos, hay que sacarlos de Django o crasheará al iniciar.

**Files:**
- Modify: `saas_chatbot/settings.py`
- Modify: `saas_chatbot/urls.py`

- [ ] **Step 1: Editar INSTALLED_APPS en settings.py**

En `saas_chatbot/settings.py`, en `INSTALLED_APPS`, quitar las 4 líneas:
```python
# Quitar estas 4 líneas:
'core.ai_processing.apps.AiProcessingConfig',
'core.channel_integration.apps.ChannelIntegrationConfig',
'core.conversation_management.apps.ConversationManagementConfig',
'core.routing_escalation.apps.RoutingEscalationConfig',
'core.tenant_management.apps.TenantManagementConfig',
```

Dejar solo:
```python
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'django_rq',
    'anymail',
    'core.shared.apps.SharedConfig',
    'core.agent.apps.AgentConfig',
]
```

- [ ] **Step 2: Limpiar urls.py**

En `saas_chatbot/urls.py`, quitar los 3 includes de módulos legacy:
```python
# Quitar:
path('api/v1/conversations/', include('core.conversation_management.interfaces.urls')),
path('api/v1/channels/', include('core.channel_integration.interfaces.urls')),
path('api/v1/routing/', include(('core.routing_escalation.interfaces.urls', 'routing_escalation'), namespace='routing_escalation')),
```

- [ ] **Step 3: Verificar que Django arranca sin errores**

```bash
docker compose exec backend python manage.py check
```
Esperado: `System check identified no issues (0 silenced).`

- [ ] **Step 4: Correr tests del agente**

```bash
docker compose exec backend pytest core/agent/tests/ -v --tb=short
```
Esperado: todos en verde.

---

## Task 4: Eliminar módulos DIALOGIX

Con los imports limpiados, ya es seguro borrar.

**Files:**
- Delete: `core/routing_escalation/`
- Delete: `core/channel_integration/`
- Delete: `core/conversation_management/`
- Delete: `core/tenant_management/`
- Delete: `core/ai_processing/`

- [ ] **Step 1: Borrar los módulos**

```bash
rm -rf /home/anuarbarrera/miagent/chatbot/core/routing_escalation
rm -rf /home/anuarbarrera/miagent/chatbot/core/channel_integration
rm -rf /home/anuarbarrera/miagent/chatbot/core/conversation_management
rm -rf /home/anuarbarrera/miagent/chatbot/core/tenant_management
rm -rf /home/anuarbarrera/miagent/chatbot/core/ai_processing
```

- [ ] **Step 2: Verificar que Django sigue arrancando**

```bash
docker compose exec backend python manage.py check
```
Esperado: `System check identified no issues (0 silenced).`

- [ ] **Step 3: Correr suite completa de tests**

```bash
docker compose exec backend pytest core/agent/tests/ -v
```
Esperado: todos en verde.

- [ ] **Step 4: Verificar que el bot de Telegram responde**

Enviar `/estado` al bot desde Telegram y confirmar respuesta normal.

- [ ] **Step 5: Commit**

```bash
git add saas_chatbot/settings.py saas_chatbot/urls.py
git rm -r core/routing_escalation core/channel_integration core/conversation_management core/tenant_management core/ai_processing
git commit -m "chore: eliminar módulos DIALOGIX no usados por el agente"
```

---

## Task 5: Mejorar Admin con métricas de los últimos 30 días

El admin actual tiene lista básica. Agregar `date_hierarchy`, mejor orden y resumen de métricas en el header de la lista.

**Files:**
- Modify: `core/agent/admin.py`

- [ ] **Step 1: Actualizar AgentRequestAdmin en admin.py**

Reemplazar el `AgentRequestAdmin` actual con:

```python
import datetime
from django.contrib import admin
from django.db.models import Count, Avg, Sum, Q
from django.utils import timezone
from django.utils.html import format_html
from .infrastructure.models import AgentSession, AgentMemory, AgentRequest, BrowserSession


@admin.register(AgentSession)
class AgentSessionAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'username', 'chat_id', 'is_authorized', 'last_active_at')
    list_filter = ('is_authorized',)
    search_fields = ('full_name', 'username', 'chat_id')
    readonly_fields = ('created_at', 'last_active_at')


@admin.register(AgentMemory)
class AgentMemoryAdmin(admin.ModelAdmin):
    list_display = ('session', 'role', 'short_content', 'timestamp')
    list_filter = ('role', 'session')
    search_fields = ('content',)
    readonly_fields = ('timestamp',)

    def short_content(self, obj):
        return obj.content[:80]
    short_content.short_description = 'Contenido'


@admin.register(BrowserSession)
class BrowserSessionAdmin(admin.ModelAdmin):
    list_display = ('platform', 'username', 'is_valid', 'last_used_at')
    list_filter = ('platform', 'is_valid')
    readonly_fields = ('created_at', 'last_used_at')


@admin.register(AgentRequest)
class AgentRequestAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'session', 'tool_used', 'duration_ms', 'estimated_tokens', 'success')
    list_filter = ('success', 'model_used', 'tool_used')
    search_fields = ('user_message', 'ai_response')
    readonly_fields = ('timestamp',)
    date_hierarchy = 'timestamp'
    ordering = ('-timestamp',)

    def changelist_view(self, request, extra_context=None):
        since = timezone.now() - datetime.timedelta(days=30)
        qs = AgentRequest.objects.filter(timestamp__gte=since)
        total = qs.count()
        successful = qs.filter(success=True).count()
        avg_ms = qs.aggregate(Avg('duration_ms'))['duration_ms__avg'] or 0
        tokens = qs.aggregate(Sum('estimated_tokens'))['estimated_tokens__sum'] or 0
        top_tools = list(
            qs.exclude(tool_used__isnull=True).exclude(tool_used='')
            .values('tool_used')
            .annotate(n=Count('id'))
            .order_by('-n')[:5]
        )
        extra_context = extra_context or {}
        extra_context['metrics_summary'] = {
            'total': total,
            'tasa_exito': f"{round(successful / max(total, 1) * 100, 1)}%",
            'avg_ms': round(avg_ms),
            'tokens': tokens,
            'top_tools': top_tools,
        }
        return super().changelist_view(request, extra_context=extra_context)
```

- [ ] **Step 2: Verificar en el admin que carga sin errores**

```bash
docker compose exec backend python manage.py check
```
Esperado: sin errores.

- [ ] **Step 3: Commit**

```bash
git add core/agent/admin.py
git commit -m "feat: mejorar admin con date_hierarchy y métricas 30 días en AgentRequest"
```

---

## Task 6: Documentación del agente

El `docs/index.md` actual es de DIALOGIX. Reemplazarlo con documentación del agente autónomo.

**Files:**
- Modify: `docs/index.md`

- [ ] **Step 1: Reescribir docs/index.md**

```markdown
# Agente Autónomo de Negocio — Tu Web MX

Asistente de IA personal para operaciones de negocio. Accesible por Telegram,
construido sobre Django + PostgreSQL + Gemini 2.5 Flash.

## Comandos disponibles

| Comando | Descripción |
|---|---|
| `/start` | Iniciar sesión con el agente |
| `/ayuda` | Ver todos los comandos disponibles |
| `/estado` | Estado del sistema y métricas básicas |
| `/post [red] [tono] <tema>` | Generar post para redes sociales |
| `/texto <tipo> <contexto>` | Redactar emails, descripciones, textos |
| `/short <tema>` | Guión para short/reels |
| `/reporte [mes] [año]` | Reporte mensual consolidado |
| `/prospectar <giro> <ubicación>` | Prospección en Google Maps vía n8n |
| `/estadisticas <url>` | Métricas de un post (Instagram, TikTok, etc.) |
| `/importcookies <plataforma>` | Importar cookies de sesión desde Cookie-Editor |

## Stack técnico

- **Backend:** Django 5.2 + DRF
- **Base de datos:** PostgreSQL 16 + pgvector (memoria semántica)
- **Cache/Jobs:** Redis + RQ Workers
- **IA:** Gemini 2.5 Flash (generación) + text-embedding-004 (embeddings)
- **Browser:** Playwright (scraping de estadísticas)
- **Infraestructura:** Docker Compose en servidor Ubuntu 24.04

## Módulos principales

```
core/agent/
  domain/          # Entidades y contratos (entities, tools, ports)
  infrastructure/  # DB, Gemini, Playwright, embeddings, tools
  application/     # AgentService — orquestador principal
  interfaces/      # Endpoints REST /health/ y /metrics/
```

## Correr tests

```bash
docker compose exec backend pytest core/agent/tests/ -v
```

## Variables de entorno requeridas

- `GEMINI_API_KEY` — clave de Google AI Studio
- `TELEGRAM_BOT_TOKEN` — token del bot de Telegram
- `TELEGRAM_ALLOWED_CHAT_IDS` — lista de chat_ids autorizados
- `N8N_WEBHOOK_URL` — endpoint de n8n para prospección
- `GOOGLE_SHEET_ID` — ID del Sheet donde n8n escribe resultados
```

- [ ] **Step 2: Commit**

```bash
git add docs/index.md
git commit -m "docs: reemplazar documentación DIALOGIX con documentación del agente"
```

---

## Task 7: Verificación final de tests

- [ ] **Step 1: Correr la suite completa**

```bash
docker compose exec backend pytest core/agent/tests/ -v --tb=short 2>&1 | tail -20
```
Esperado: todos los tests en verde (152+). Si alguno falla, diagnosticar antes de continuar.

- [ ] **Step 2: Verificar que los servicios siguen corriendo**

```bash
docker compose ps
```
Esperado: backend, rqworker, postgres, redis, telegram_bot — todos `Up`.

- [ ] **Step 3: Prueba manual desde Telegram**

Enviar al bot:
- `/estado` → responde con métricas
- `/post instagram casual Apertura de sucursal` → genera post
- Un mensaje libre → responde con Gemini

---

## Task 8: Preparar nuevo repo GitHub

Con el proyecto limpio, inicializar un repo nuevo con historia limpia.

**Prerequisito:** Tener `gh` (GitHub CLI) instalado y autenticado. Si no: `gh auth login`.

- [ ] **Step 1: Verificar gh instalado**

```bash
gh auth status
```
Esperado: sesión activa. Si no, ejecutar `gh auth login`.

- [ ] **Step 2: Ir al directorio del proyecto**

```bash
cd /home/anuarbarrera/miagent/chatbot
```

- [ ] **Step 3: Confirmar que el repo nuevo tendrá historia limpia**

El proyecto actual puede tener git de DIALOGIX. Verificar:
```bash
git log --oneline | head -5
```
Si los commits son de DIALOGIX y no del agente, proceder con los siguientes pasos para historia limpia.

- [ ] **Step 4: Inicializar repo limpio (solo si el historial es de DIALOGIX)**

⚠️ **Acción destructiva — confirmar con el usuario antes de ejecutar.**

```bash
# Guardar el .gitignore actual si existe
cp .gitignore /tmp/.gitignore_backup 2>/dev/null || true

# Eliminar git anterior y crear uno nuevo
rm -rf .git
git init
git branch -m main
```

- [ ] **Step 5: Crear .gitignore apropiado**

```bash
cat > .gitignore << 'EOF'
# Python
__pycache__/
*.py[cod]
*.egg-info/
.env
.venv/
venv/

# Django
*.log
local_settings.py
db.sqlite3
staticfiles/
media/

# Docker
.docker/

# IDE
.idea/
.vscode/
*.swp

# Secrets
ssl/
*.pem
*.key
*.crt
EOF
```

- [ ] **Step 6: Primer commit del agente**

```bash
git add .
git commit -m "feat: agente autónomo de negocio v1.0

Stack: Django 5.2, PostgreSQL 16 + pgvector, Redis + RQ, Gemini 2.5 Flash, Playwright, Telegram Bot.
Capacidades: posts/textos/shorts, prospección Google Maps, estadísticas de redes sociales, memoria semántica."
```

- [ ] **Step 7: Crear repo en GitHub y pushear**

```bash
gh repo create agente-negocio \
  --private \
  --description "Agente autónomo de IA para operaciones de negocio — Telegram + Gemini + Django" \
  --source=. \
  --remote=origin \
  --push
```

- [ ] **Step 8: Confirmar que el repo existe**

```bash
gh repo view agente-negocio
```

---

## Self-Review

**Cobertura de los ítems del plan:**
- [x] Sprint 5 — `google.generativeai` migrado → Task 1
- [x] Sprint 5 — Limpieza DIALOGIX → Task 3 + Task 4
- [x] Sprint 5 — Dashboard Admin métricas → Task 5
- [x] Sprint 5 — Documentación del agente → Task 6
- [x] 5 pasos de cleanup → Tasks 2, 3, 4, 7, 8
- [x] Tests de regresión → Task 7

**Items ya implementados (no requieren acción):**
- pgvector ✅, embeddings ✅, memoria semántica ✅

**Items fuera de alcance de este plan (para v1.1):**
- Login Facebook/LinkedIn (selectores no implementados)
- `read_sheets` tool
- `scrape_social_profile` tool
