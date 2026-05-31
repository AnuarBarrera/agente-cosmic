# Agente Autónomo de Negocio

Asistente de negocio autónomo vía Telegram. Genera contenido, automatiza redes sociales y mantiene conversaciones con memoria semántica.

## Stack

- **Backend**: Django 5.2 + PostgreSQL 16 + pgvector + Redis/RQ
- **IA**: Gemini 2.5 Flash + text-embedding-004 (embeddings 768d)
- **Bot**: python-telegram-bot 21.9
- **Browser**: Playwright + Chromium
- **Infra**: Docker Compose

## Inicio rápido

```bash
cp .env.example .env          # configurar GEMINI_API_KEY y TELEGRAM_BOT_TOKEN
docker compose up -d          # levantar todos los servicios
docker compose exec backend python manage.py migrate
```

El bot de Telegram arranca automáticamente con el servicio `telegram_bot`.

## Comandos disponibles

| Comando | Descripción |
|---------|-------------|
| `/post [red] [tono] <tema>` | Genera post para Instagram, Facebook, LinkedIn, Twitter |
| `/texto [tipo] <contexto>` | Redacta emails, anuncios, bios, propuestas |
| `/short <tema>` | Guión para Reels/TikTok/YouTube Shorts |
| `/estadisticas <url>` | Métricas de un post (requiere cookies) |
| `/importcookies <plataforma>` | Importar cookies desde Cookie-Editor |
| `/prospectar <giro> <ciudad>` | Busca negocios en Google Maps |
| `/reporte [mes] [año]` | Reporte mensual de uso |
| `/consumo` | Métricas de BD: tokens, solicitudes, herramientas |
| `/estado` | Estado del agente |

## Variables de entorno

Ver `.env.example` para la lista completa. Las esenciales:

```
GEMINI_API_KEY=       # Google AI Studio
TELEGRAM_BOT_TOKEN=   # BotFather
AUTHORIZED_CHAT_IDS=  # IDs de Telegram autorizados (separados por coma)
DATABASE_URL=         # PostgreSQL con pgvector
```

## Tests

```bash
docker compose exec backend python -m pytest core/agent/tests/ -q
```

149 tests. Sin dependencias externas (Gemini y Telegram mockeados).

## Estructura

```
core/
  agent/              # dominio principal: bot, tools, memoria, browser
  shared/             # middleware, audit, event bus
  tenant_management/  # modelo User (AUTH_USER_MODEL)
saas_chatbot/         # settings, urls, wsgi
load_tests/           # stress tests del agente
```
