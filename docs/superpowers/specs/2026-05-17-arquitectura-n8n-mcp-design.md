# Spec: Arquitectura n8n + MCP — Roadmap Unificado v2.0

**Fecha:** 2026-05-17  
**Estado:** Aprobado  
**Sprints:** 11–14  

---

## Contexto

El agente lleva Sprints 1-10 completados (192 tests). Las features pendientes del backlog original se integran ahora en una nueva arquitectura que reemplaza la implementación custom (Playwright + scraping) por herramientas especializadas.

---

## Decisión de arquitectura: Enfoque B

Cada sistema hace lo que mejor sabe hacer:

| Sistema | Rol |
|---|---|
| **Django** | Recibe comandos Telegram, despacha jobs async, valida outputs con Gemini, envía respuestas |
| **n8n** | Flujos multi-paso con redes sociales (stats, competidores); autenticación OAuth centralizada |
| **MCP servers** | Capacidades externas: Brave Search, Minimax (video/audio/imagen) |
| **RQ Workers** | Ejecutan jobs async, llaman n8n y MCP servers |

### Flujo general (async)

```
Usuario → Telegram → Django
                       ↓
               RQ Job dispatcha
                  ↙          ↘
         MCP server        n8n webhook
         (HTTP/SSE)        (HTTP POST)
              ↓                 ↓
        resultado crudo    POST /api/n8n/callback/
                  ↘           ↙
              Gemini valida output
                       ↓
                 Telegram responde
```

**Principio clave:** n8n ejecuta y entrega datos crudos. Gemini siempre valida y formatea antes de responder al usuario. El usuario recibe `"⏳ Procesando..."` de inmediato y el resultado cuando el job termina.

---

## Componentes nuevos en Django

### `N8nClient` (infrastructure)
- `dispatch(workflow_id, params, job_id, chat_id)` → HTTP POST al webhook de n8n
- Adjunta `job_id` y `chat_id` en payload para que n8n los devuelva en el callback

### `McpClient` (infrastructure)
- `call(server, tool, params)` → HTTP POST al MCP server correspondiente
- Corre síncrono dentro del RQ worker

### `PendingJob` model (nueva tabla)

| Campo | Tipo | Descripción |
|---|---|---|
| `job_id` | UUID | Identificador único |
| `chat_id` | str | Chat Telegram destino |
| `command` | str | Comando origen (`/estadisticas`, etc.) |
| `workflow` | str | ID workflow n8n |
| `status` | str | `pending` / `completed` / `failed` |
| `created_at` | datetime | — |
| `completed_at` | datetime | nullable |

### `POST /api/n8n/callback/` (nueva view)
1. Recibe `{ job_id, chat_id, data }` con header `X-N8N-Token`
2. Marca `PendingJob` como `completed`
3. Pasa `data` a Gemini para validación y formato
4. Envía mensaje Telegram al `chat_id`

### Contratos de comunicación

**Django → n8n:**
```json
{ "job_id": "uuid-v4", "chat_id": "123456789", "params": {} }
```

**n8n → Django callback:**
```json
{ "job_id": "uuid-v4", "chat_id": "123456789", "status": "ok", "data": {} }
```

---

## MCP Servers (contenedores Docker nuevos)

| MCP Server | Reemplaza / agrega | Sprint |
|---|---|---|
| `brave-search-mcp` | Reemplaza scraping Bing/DDG — fix geolocalización | 11 |
| `minimax-mcp` | Agrega video, audio; mejora `/imagen` | 13 |

---

## Flujos n8n — Redes Sociales

Login de las 4 redes configurado **una sola vez en n8n** (OAuth o API token). Django deja de manejar cookies. `/importcookies` y `/login` quedan deprecados.

| Workflow | Red | Output |
|---|---|---|
| `instagram_stats` | Instagram | posts, likes, alcance, engagement |
| `linkedin_stats` | LinkedIn | posts, impresiones, clics, seguidores |
| `facebook_stats` | Facebook | posts, alcance, reacciones |
| `tiktok_stats` | TikTok | videos, vistas, likes, seguidores |
| `instagram_competitor` | Instagram | bio, seguidores, posts recientes |
| `linkedin_competitor` | LinkedIn | info empresa/persona, posts recientes |
| `facebook_competitor` | Facebook | info página, publicaciones recientes |
| `tiktok_competitor` | TikTok | perfil, videos recientes, métricas |

---

## Roadmap unificado por sprint

### Sprint 11 — Quick wins + Brave Search MCP

**RAG integrado en generación de posts** *(Alta prioridad — backlog original)*  
`/post`, `/imagen`, `/documento`, `/texto` consultan automáticamente los docs RAG del usuario antes de generar. Si no hay docs, funciona igual que hoy.  
- `GeneratePostTool.execute()` llama `RAGQueryTool` internamente antes de generar
- Aplica a todos los comandos de contenido

**`/ayuda` completo** *(Alta prioridad — backlog original)*  
Editar handler `cmd_ayuda` en `run_telegram_bot.py` para incluir todos los comandos de Sprints 6-10: `/buscar`, `/documento`, `/prospecto`, `/imagen`, `/consultar`.

**Brave Search MCP** *(Arquitectura nueva — fix geolocalización)*  
- Levantar `brave-search-mcp` en Docker Compose
- Implementar `McpClient`
- Migrar `WebSearchTool` a cliente HTTP thin → elimina Playwright + scraping Bing/DDG

---

### Sprint 12 — Infraestructura n8n + Redes Sociales

**Infraestructura Django ↔ n8n**  
- Implementar `N8nClient`
- Crear modelo `PendingJob` + migración
- Implementar `POST /api/n8n/callback/` con autenticación por token

**Flujos n8n — Stats (4 redes)**  
Crear workflows: `instagram_stats`, `linkedin_stats`, `facebook_stats`, `tiktok_stats`.  
Migrar `/estadisticas` de Playwright+cookies a `N8nClient`.

**Flujos n8n — Competitor (4 redes)**  
Crear workflows: `*_competitor` para las 4 redes.  
Enriquecer `/prospecto` con perfiles de LinkedIn, Facebook, Instagram, TikTok.

**Deprecar login de cookies**  
Eliminar `/importcookies` y `/login`. Las credenciales viven en n8n.

---

### Sprint 13 — Minimax MCP + Mejoras prospector

**Minimax MCP** *(Baja prioridad original → sube con arquitectura MCP)*  
- Levantar `minimax-mcp` en Docker Compose
- Mejorar `/imagen`: Minimax en vez de HTML template + Playwright screenshot
- Nuevos comandos `/video` y `/audio`

**Mejoras al prospector de mapas** *(Media prioridad — backlog original)*  
- Deduplicación por `place_id`
- Scoring de leads con Gemini (criterios configurables)
- Follow-up tracking: el agente pregunta si se contactaron los prospectos previos

---

### Sprint 14 — Multi-usuario + Integraciones Google via n8n

**Seguridad multi-usuario** *(Media prioridad — backlog original)*  
- Múltiples `chat_id` autorizados con niveles de permiso (admin vs. solo lectura)
- Memoria segmentada por usuario
- Alerta cuando token de red social está próximo a expirar

**Google Calendar via n8n** *(Baja prioridad original → más fácil con n8n)*  
n8n tiene conector nativo de Google Calendar. Leer y crear eventos desde Telegram: `"Agéndame una llamada con cliente X el jueves a las 3pm"`.

**Google Sheets via n8n** *(Backlog original)*  
Leer y escribir datos bidireccional. Exportar prospectos, métricas, reportes.

---

## Migración de comandos existentes

| Comando | Antes (Sprint 1-10) | Después |
|---|---|---|
| `/buscar` | Playwright + Bing/DDG scraping | `McpClient` → `brave-search-mcp` |
| `/estadisticas` | Playwright + cookies Instagram | `N8nClient` → `instagram_stats` |
| `/prospecto` | Playwright + BeautifulSoup web | `N8nClient` → `*_competitor` según red |
| `/imagen` | HTML template + Playwright screenshot | `McpClient` → `minimax-mcp` |
| `/importcookies` | Sube cookies al agente | **Deprecado** (auth en n8n) |
| `/login` | Playwright login | **Deprecado** (auth en n8n) |

---

## Backlog (sin sprint asignado)

- Música / narración de voz para shorts (Gemini TTS o ElevenLabs)
- Generación de propuestas en PDF (complemento al .docx actual)
- Análisis de competencia automatizado (cron semanal de `*_competitor`)
- Canary token en bio de LinkedIn/Instagram (detectar scraping externo)

### WhatsApp Business — análisis detallado (2026-05-18)

El caso real tiene 3 necesidades distintas con soluciones distintas:

**1. Verificar si un número tiene WA / si es Business**
No usar MCP. Usar **2Chat API** (`developers.2chat.co`):
- Verifica existencia, si es cuenta Business, nombre verificado
- No requiere API oficial de Meta, solo una cuenta Business conectada
- Precio: ~$0.005/consulta o planes mensuales
- Alternativas similares: Maytapi, Wassenger

**2. Estadísticas de conversaciones propias (tasa de respuesta, patrones)**
MCP no oficial viable: `lharries/whatsapp-mcp`. Solo lectura → riesgo de ban bajo.
Limitación: hay que construir el tracking, no viene listo.

**3. Seguimiento automatizado de prospectos a escala**
Partners pagados: **WATI** o **Wassenger** (~$40-80/mes). Incluyen bandeja compartida, seguimientos y estadísticas integradas.

**Recomendación para cuando se implemente:**
- Fase validación: 2Chat para limpiar lista + envío manual con mensajes variados
- Fase escala: evaluar WATI/Wassenger cuando el volumen lo justifique
- El agente tiene perfil técnico → puede conectar API de 2Chat directamente

---

## Seguridad

- Callback `/api/n8n/callback/` valida `X-N8N-Token` en cada request
- n8n en red Docker interna, accesible solo desde `chatbot-net`
- MCP servers en red Docker interna, sin puertos externos
- Credenciales de redes sociales solo en n8n, nunca en Django ni `.env`
- Todo contenido externo sigue pasando por `scrape_guard.safe_external_content()` antes de llegar a Gemini
