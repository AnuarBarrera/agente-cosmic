# Agente Autónomo de Negocio

Asistente de negocio autónomo vía Telegram con IA (Gemini), automatización de redes sociales (Playwright) y memoria semántica (pgvector).

## Capacidades

### Conversación inteligente
Responde preguntas de negocio con contexto de conversación. Usa memoria semántica (pgvector + text-embedding-004) para recuperar conversaciones relevantes del pasado.

### Herramientas de contenido
- **Generar post** — crea posts para Instagram, Facebook, LinkedIn, Twitter con tono y reglas por plataforma
- **Redactar texto** — emails, descripciones, bios, anuncios, propuestas, guiones
- **Guión de short** — guiones estructurados para Reels/TikTok/YouTube Shorts

### Automatización de redes sociales
- Scraping de métricas de Instagram (likes, comentarios, vistas)
- Login con cookies exportadas de Cookie-Editor
- Publicación automatizada (en desarrollo)

### Herramientas de negocio
- Reportes de actividad y métricas del agente
- Transcripción de audio (Whisper)
- Búsqueda en Google Maps

## Stack tecnológico

- **Backend**: Django 5.2, PostgreSQL 16 + pgvector, Redis + RQ
- **IA**: Gemini 2.5 Flash (texto), text-embedding-004 (embeddings 768d)
- **Bot**: python-telegram-bot 21.9
- **Browser**: Playwright + Chromium
- **Infra**: Docker Compose

## Arquitectura

```
core/agent/
  domain/          # entidades, puertos, herramientas base
  application/     # AgentService — orquesta conversación + tools
  infrastructure/  # GeminiAdapter, EmbeddingService, Browser, Repos
    tools/         # GeneratePostTool, WriteTextTool, BrowserTools...
  interfaces/      # vistas DRF + URLs (api/v1/agent/)
  management/      # run_telegram_bot command
  tests/           # 149 tests
```

## Inicio rápido

```bash
# Copiar variables de entorno
cp .env.example .env  # configurar GEMINI_API_KEY, TELEGRAM_BOT_TOKEN

# Levantar servicios
docker compose up -d

# Migrar base de datos
docker compose exec backend python manage.py migrate

# El bot de Telegram arranca automáticamente con el servicio telegram_bot
```

## Comandos Telegram

| Comando | Descripción |
|---------|-------------|
| `/start` | Bienvenida e instrucciones |
| `/estado` | Estado del agente y sesión |
| `/post <tema>` | Generar post para redes sociales |
| `/importcookies` | Importar cookies de Instagram |
| `/estadisticas <url>` | Métricas de un post |
| `/ayuda` | Lista completa de comandos |

## Admin Django

Accede en `/admin/` para ver:
- Sesiones autorizadas de Telegram
- Historial de conversaciones
- Métricas de solicitudes (últimos 30 días): tasa de éxito, ms promedio, tokens, top tools
- Sesiones de navegador (cookies)
