# Sprint 15C — Competitor Workflows n8n Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Crear los 4 workflows n8n de análisis de competidores (`facebook_competitor`, `instagram_competitor`, `linkedin_competitor`, `tiktok_competitor`) para que el comando `/prospecto <nombre> <url>` funcione con URLs de redes sociales.

**Architecture:** El lado Django ya está completo: `competitor_n8n_job()` en `jobs.py` envía el job a n8n, y el callback en `n8n_views.py` recibe y formatea la respuesta con Gemini. Solo falta crear los workflows en n8n. Cada workflow recibe `{job_id, chat_id, params: {url, name}}` → llama a la API pública de la red social → callback a Django con los datos obtenidos.

**Tech Stack:** n8n (HTTP Request nodes), Facebook Graph API (token existente), Instagram oEmbed API, web scraping para LinkedIn/TikTok.

**Credenciales necesarias por plataforma:**
- **Facebook**: Token ya existente (`FACEBOOK_TOKEN_TUWEBMX` o `FACEBOOK_TOKEN_ANUARBARRERA`) — funciona para leer páginas públicas
- **Instagram**: Mismo token de Facebook (Instagram oEmbed API) — datos básicos sin credenciales especiales
- **LinkedIn**: Sin API oficial pública — workflow placeholder, activar cuando haya credenciales
- **TikTok**: Sin API oficial pública — workflow placeholder, activar cuando haya credenciales

---

## File Structure

Todo el trabajo es en n8n (UI en `http://localhost:5678`). El código Django no requiere cambios.

| Workflow n8n | Estado | Credenciales |
|---|---|---|
| `facebook_competitor` | Funcional ahora | Token Facebook existente |
| `instagram_competitor` | Funcional ahora | Token Facebook (oEmbed) |
| `linkedin_competitor` | Placeholder | Requiere LinkedIn API |
| `tiktok_competitor` | Placeholder | Requiere TikTok API |

---

### Task 1: Workflow `facebook_competitor`

Acceder a n8n `http://localhost:5678`, crear nuevo workflow con nombre `facebook_competitor`.

**Lógica:** Extraer el identificador de página del URL de Facebook → GET a Graph API → Callback con datos de seguidores, posts recientes, engagement.

- [ ] **Step 1: Nodo Webhook**

```json
{
  "name": "Webhook",
  "type": "n8n-nodes-base.webhook",
  "typeVersion": 2,
  "parameters": {
    "httpMethod": "POST",
    "path": "facebook_competitor",
    "responseMode": "responseNode"
  }
}
```

- [ ] **Step 2: Nodo Code — extraer page identifier del URL**

La URL puede ser `https://facebook.com/tuwebmx` o `https://www.facebook.com/pages/Nombre/123456789`.

```json
{
  "name": "Extract Page ID",
  "type": "n8n-nodes-base.code",
  "parameters": {
    "jsCode": "const wb = $input.first().json.body;\nconst url = wb.params.url;\n// Extraer el identificador: last path segment non-empty\nconst parts = url.replace(/\\?.*$/, '').split('/').filter(p => p && p !== 'www.facebook.com' && p !== 'facebook.com' && p !== 'https:' && p !== '');\nconst pageId = parts[parts.length - 1] || parts[0];\nreturn [{ json: {\n  job_id: wb.job_id,\n  chat_id: wb.chat_id,\n  name: wb.params.name,\n  page_id: pageId,\n  original_url: url\n}}];"
  }
}
```

- [ ] **Step 3: Nodo HTTP Request — Graph API**

Reemplazar `ACCESS_TOKEN` con el valor del token de Facebook que ya está en n8n (o usar la variable de entorno de n8n). El token de página pública no necesita permisos especiales.

```json
{
  "name": "Facebook Graph API",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4,
  "parameters": {
    "method": "GET",
    "url": "=https://graph.facebook.com/v20.0/{{ $json.page_id }}",
    "sendQuery": true,
    "queryParameters": {
      "parameters": [
        {"name": "fields", "value": "name,fan_count,followers_count,category,about,posts{message,created_time,full_picture,likes.summary(true),comments.summary(true),shares}"},
        {"name": "access_token", "value": "<TOKEN_FACEBOOK_AQUI>"}
      ]
    }
  }
}
```

**Nota sobre el token:** Usar el mismo token que los workflows `facebook_stats_tuwebmx` o `facebook_stats_anuarbarrera` ya configurados en n8n. Copiar el valor desde esos workflows.

- [ ] **Step 4: Nodo Code — formatear datos**

```json
{
  "name": "Format Data",
  "type": "n8n-nodes-base.code",
  "parameters": {
    "jsCode": "const wb = $('Extract Page ID').first().json;\nconst page = $input.first().json;\nconst posts = (page.posts && page.posts.data) ? page.posts.data.slice(0, 5) : [];\nconst totalLikes = posts.reduce((sum, p) => sum + (p.likes && p.likes.summary ? p.likes.summary.total_count : 0), 0);\nconst totalComments = posts.reduce((sum, p) => sum + (p.comments && p.comments.summary ? p.comments.summary.total_count : 0), 0);\nconst avgEngagement = posts.length > 0 ? Math.round((totalLikes + totalComments) / posts.length) : 0;\nreturn [{ json: {\n  job_id: wb.job_id,\n  chat_id: wb.chat_id,\n  platform: 'facebook',\n  competitor_name: wb.name,\n  page_name: page.name,\n  fans: page.fan_count || 0,\n  followers: page.followers_count || 0,\n  category: page.category || '',\n  about: (page.about || '').slice(0, 200),\n  recent_posts: posts.length,\n  avg_engagement: avgEngagement,\n  url: wb.original_url\n}}];"
  }
}
```

- [ ] **Step 5: Nodo HTTP Request — Callback Django**

```json
{
  "name": "Callback Django",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4,
  "parameters": {
    "method": "POST",
    "url": "http://172.17.0.1:3001/api/v1/agent/n8n/callback/",
    "sendHeaders": true,
    "headerParameters": {
      "parameters": [
        {"name": "X-N8N-Token", "value": "<N8N_CALLBACK_TOKEN del .env>"},
        {"name": "Content-Type", "value": "application/json"}
      ]
    },
    "sendBody": true,
    "contentType": "json",
    "bodyParameters": {
      "parameters": [
        {"name": "job_id",  "value": "={{ $json.job_id }}"},
        {"name": "chat_id", "value": "={{ $json.chat_id }}"},
        {"name": "status",  "value": "ok"},
        {"name": "data",    "value": "={{ JSON.stringify({platform: $json.platform, competitor_name: $json.competitor_name, page_name: $json.page_name, fans: $json.fans, followers: $json.followers, category: $json.category, about: $json.about, recent_posts: $json.recent_posts, avg_engagement: $json.avg_engagement, url: $json.url}) }}"}
      ]
    }
  }
}
```

- [ ] **Step 6: Nodo Respond to Webhook**

```json
{
  "name": "Respond to Webhook",
  "type": "n8n-nodes-base.respondToWebhook",
  "parameters": {
    "respondWith": "json",
    "responseBody": "{\"ok\": true}"
  }
}
```

- [ ] **Step 7: Activar el workflow**

Toggle "Active". Verificar que el path del webhook sea `facebook_competitor`.

- [ ] **Step 8: Test manual**

En Telegram:
```
/prospecto "Competidor Test" https://facebook.com/[pagina-publica-cualquiera]
```

Resultado esperado: mensaje de Telegram con datos de la página: fans, seguidores, engagement promedio.

---

### Task 2: Workflow `instagram_competitor`

Crear nuevo workflow en n8n con nombre `instagram_competitor`.

**Lógica:** Usar Instagram oEmbed API (sin credenciales adicionales con token Facebook) para obtener datos básicos del perfil.

**Limitación importante:** oEmbed solo retorna datos de posts individuales, no del perfil. Para datos de perfil (seguidores, posts totales) se necesita la Instagram Graph API con cuenta Business vinculada. Este workflow usa lo que está disponible con el token existente.

- [ ] **Step 1: Nodo Webhook**

Igual que facebook_competitor pero con `path: "instagram_competitor"`.

- [ ] **Step 2: Nodo HTTP Request — Instagram oEmbed**

```json
{
  "name": "Instagram oEmbed",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4,
  "parameters": {
    "method": "GET",
    "url": "https://graph.facebook.com/v20.0/instagram_oembed",
    "sendQuery": true,
    "queryParameters": {
      "parameters": [
        {"name": "url",          "value": "={{ $('Webhook').first().json.body.params.url }}"},
        {"name": "access_token", "value": "<TOKEN_FACEBOOK_AQUI>"},
        {"name": "fields",       "value": "author_name,author_url,thumbnail_url,title,html"}
      ]
    }
  }
}
```

**Nota:** oEmbed funciona para URLs de posts públicos de Instagram (`/p/` o `/reel/`). Para URLs de perfil (`/username/`) retorna datos del perfil public.

- [ ] **Step 3: Nodo Code — formatear y combinar con datos del Webhook**

```json
{
  "name": "Format Data",
  "type": "n8n-nodes-base.code",
  "parameters": {
    "jsCode": "const wb = $('Webhook').first().json.body;\nconst oembed = $input.first().json;\nreturn [{ json: {\n  job_id: wb.job_id,\n  chat_id: wb.chat_id,\n  platform: 'instagram',\n  competitor_name: wb.params.name,\n  author_name: oembed.author_name || wb.params.name,\n  author_url: oembed.author_url || wb.params.url,\n  title: oembed.title || '',\n  note: 'Datos limitados a oEmbed API. Para métricas completas se requiere Instagram Graph API con cuenta Business.',\n  url: wb.params.url\n}}];"
  }
}
```

- [ ] **Step 4: Nodo HTTP Request — Callback Django**

Igual que en `facebook_competitor`, ajustando los campos `data`:

```
"data": "={{ JSON.stringify({platform: $json.platform, competitor_name: $json.competitor_name, author_name: $json.author_name, author_url: $json.author_url, note: $json.note, url: $json.url}) }}"
```

- [ ] **Step 5: Nodo Respond to Webhook + Activar**

Igual que en Task 1.

---

### Task 3: Workflows placeholder `linkedin_competitor` y `tiktok_competitor`

Crear 2 workflows en n8n que retornan un mensaje informativo indicando que las credenciales están pendientes. Así el bot no falla cuando el usuario pasa una URL de LinkedIn/TikTok.

- [ ] **Step 1: Crear `linkedin_competitor`**

Nuevo workflow `linkedin_competitor` con 3 nodos:

**Nodo 1 — Webhook** (path: `linkedin_competitor`):
```json
{"httpMethod": "POST", "path": "linkedin_competitor", "responseMode": "responseNode"}
```

**Nodo 2 — Code** (mensaje de placeholder):
```json
{
  "jsCode": "const wb = $input.first().json.body;\nreturn [{ json: {\n  job_id: wb.job_id,\n  chat_id: wb.chat_id,\n  platform: 'linkedin',\n  competitor_name: wb.params.name,\n  message: 'Análisis de LinkedIn pendiente de implementación. Se requiere aprobación de LinkedIn Marketing API.',\n  url: wb.params.url\n}}];"
}
```

**Nodo 3 — HTTP Request Callback Django** (igual que Task 1, con `data: JSON.stringify({platform: $json.platform, competitor_name: $json.competitor_name, message: $json.message, url: $json.url})`).

**Nodo 4 — Respond to Webhook**.

Activar el workflow.

- [ ] **Step 2: Crear `tiktok_competitor`**

Igual que `linkedin_competitor` pero con:
- `path: "tiktok_competitor"`
- `platform: 'tiktok'`
- `message: 'Análisis de TikTok pendiente de implementación. Se requiere TikTok Research API.'`

Activar el workflow.

---

### Task 4: Verificar tests del lado Django y test de integración

El lado Django de competitor ya tiene tests en `test_sprint12.py`. Esta tarea verifica que todo funciona end-to-end.

**Files:**
- No hay cambios en código — solo verificación.

- [ ] **Step 1: Correr tests existentes de competitor**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint12.py -v -k "competitor" 2>&1
```

Resultado esperado: todos los tests de competitor PASSED.

- [ ] **Step 2: Correr suite completa para detectar regresiones**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q 2>&1 | tail -5
```

Resultado esperado: todos PASSED, 0 failed.

- [ ] **Step 3: Test manual en Telegram — Facebook**

```
/prospecto "Agencia Test" https://facebook.com/meta
```

Resultado esperado: datos de la página (fans, categoría, engagement).

- [ ] **Step 4: Test manual en Telegram — LinkedIn (placeholder)**

```
/prospecto "Empresa Test" https://linkedin.com/company/google
```

Resultado esperado: mensaje indicando que LinkedIn está pendiente de implementación.

- [ ] **Step 5: Test manual en Telegram — TikTok (placeholder)**

```
/prospecto "Creator Test" https://tiktok.com/@user
```

Resultado esperado: mensaje indicando que TikTok está pendiente.

---

## Notas para cuando lleguen las credenciales

**LinkedIn:** Aplicar a LinkedIn Marketing API Developer Program en `developer.linkedin.com`. Cuando se apruebe:
- Reemplazar el nodo Code placeholder en `linkedin_competitor` con un HTTP Request a `https://api.linkedin.com/v2/organizationalEntityFollowerStatistics?q=organizationalEntity&organizationalEntity={id}`
- Requiere OAuth2 con scope `r_organization_social`

**TikTok:** Aplicar a TikTok Research API en `developers.tiktok.com/products/research-api`. Cuando se apruebe:
- Reemplazar el placeholder con HTTP Request a `https://open.tiktokapis.com/v2/research/user/info/`
- Requiere `client_key` y `client_secret` de TikTok for Developers

**Instagram completo:** Vincular cuenta Instagram Business a página de Facebook en Meta Business Suite. Luego en `instagram_competitor`, reemplazar el oEmbed call con:
```
GET https://graph.facebook.com/v20.0/{ig-user-id}?fields=followers_count,media_count,biography&access_token={TOKEN}
```
