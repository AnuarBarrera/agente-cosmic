# Agente Cosmic — Diseño de Sistema (Hackathon Google 2026-06-05)

## Alcance

Sistema agéntico que recibe la URL de un negocio, analiza su ADN de marca (sitio web + logo + posts anteriores) y genera automáticamente un calendario de 7 días de contenido para redes sociales, entregado por email día a día.

**Fuera de alcance (v2):**
- Cuentas de usuario, tiers y billing
- Análisis de resultados de posts (requiere aprobación de APIs de RRSS)
- WebSocket / SSE (se usa polling simple)

---

## Arquitectura

Dos apps Django nuevas dentro de `core/`:

```
core/
  brand_dna/
    models.py              BrandDNA, AnalysisJob
    extractors/
      web_scraper.py       requests + BeautifulSoup → Gemini (JSON estructurado)
      logo_analyzer.py     Cloud Vision API (colores) + Gemini Vision (elementos)
      posts_analyzer.py    Gemini Vision (imágenes) / Gemini (texto) / scraping (URL)
    tasks.py               Tarea RQ principal que orquesta los 3 extractores
    views.py + urls.py
  content_pipeline/
    models.py              ContentCalendar, ContentPost
    generators/
      text_generator.py    Gemini → 7 captions con ADN como contexto
      image_generator.py   Pollinations.ai → imagen por día con colores del ADN
    scheduler.py           Programa 6 RQ jobs con fecha/hora para días 2-7
    email_sender.py        Plantillas HTML + envío via Mailgun (django-anymail)
```

### Flujo completo

```
Landing (formulario en /)
  ↓ POST /analizar/
AnalysisJob creado → RQ job encolado → redirect /resultados/<job_id>/
  ↓ polling JS cada 3s a /api/status/<job_id>/
  Etapas visibles:
    Web     (0→30%)   scraping homepage + Gemini
    Logo    (30→55%)  Cloud Vision + Gemini Vision
    Posts   (55→75%)  análisis de posts anteriores
    Content (75→95%)  generación de 7 captions + 7 imágenes
    Done    (95→100%) email #1 enviado + días 2-7 programados
  ↓ status = done
Página de resultados: panel ADN + 7 cards del calendario
Emails días 2-7 llegan cada día según scheduled_at del ContentPost
```

---

## Modelos de datos

### `AnalysisJob`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | UUID PK | |
| email | EmailField | destino de los correos |
| business_url | URLField | URL del negocio |
| status | CharField | pending / processing / done / failed |
| stage | CharField | web / logo / posts / content / complete |
| progress | IntegerField | 0-100 |
| error_message | TextField | vacío si no hay error |
| created_at | DateTimeField | |

### `BrandDNA`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | UUID PK | |
| job | OneToOneField(AnalysisJob) | |
| business_name | CharField | extraído del scraping |
| business_url | URLField | |
| description | TextField | qué hace el negocio |
| keywords | JSONField | ["sustentable", "premium", ...] |
| audience | TextField | perfil del cliente ideal |
| tone | CharField | formal / casual / inspiracional / urgente |
| primary_colors | JSONField | ["#FF5733", "#2C3E50"] |
| logo_url | URLField | blank=True |
| logo_elements | TextField | descripción del logo |
| posting_style | TextField | resumen del estilo inferido de posts |
| avg_caption_length | IntegerField | |
| common_hashtags | JSONField | |
| created_at | DateTimeField | |

### `ContentCalendar`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | UUID PK | |
| brand_dna | OneToOneField(BrandDNA) | |
| created_at | DateTimeField | |

### `ContentPost`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | UUID PK | |
| calendar | ForeignKey(ContentCalendar) | |
| day_number | IntegerField | 1-7 |
| caption | TextField | |
| image_url | URLField | URL en Cloud Storage |
| suggested_time | TimeField | ej: 19:00 |
| hashtags | JSONField | |
| status | CharField | pending / sent / failed |
| scheduled_at | DateTimeField | fecha+hora programada |
| sent_at | DateTimeField | null=True |

---

## Landing page y UX

### Formulario (`/`)
```
[ Email del usuario ]
[ URL del negocio ]
[ Logo ] — upload imagen (jpg/png, max 5MB)

Sección opcional "Posts anteriores" (mejora el resultado):
  [ Hasta 5 imágenes de posts ]
  [ Texto de tus últimos posts ] — textarea
  [ URL de perfil público ] — solo perfiles públicos accesibles vía web
                              (Instagram bloquea scraping; funciona mejor con sitios propios,
                               Facebook público, LinkedIn público)
  (se acepta cualquier combinación de los tres)

[ → Analizar mi marca ]
```

### Página de progreso y resultados (`/resultados/<job_id>/`)

**Durante procesamiento:** barra de progreso con etapas. JS hace `fetch('/api/status/<job_id>/')` cada 3 segundos.

**Al completar:** sin redirección — la misma página revela:
- Panel de ADN: swatches de colores, tono, audiencia, keywords, estilo
- 7 cards del calendario: caption + imagen + día + horario sugerido
- Nota: "Recibirás el contenido de cada día en tu correo a las [hora]"

### Endpoint de polling
```
GET /api/brand-dna/status/<job_id>/
Response: {
  "status": "processing",
  "stage": "logo",
  "progress": 50,
  "brand_dna": null   ← se llena cuando stage=complete
}
```

---

## Flujo de emails (Mailgun)

### Email #1 — inmediato al completar
```
Asunto: Tu ADN de marca está listo — [Nombre del negocio]
Contenido:
  - Resumen del ADN (colores, tono, audiencia, keywords)
  - Vista del calendario (7 días en texto)
  - Contenido completo Día 1: caption + imagen adjunta
  - "Mañana recibirás el Día 2 en este correo"
```

### Emails Días 2-7 — programados en RQ
```
Asunto: Día [N] de tu calendario — [Nombre del negocio]
Contenido:
  - Caption listo para publicar
  - Imagen adjunta
  - Horario sugerido
  - Hashtags sugeridos
```
Los 6 jobs de RQ se programan con `enqueue_at(scheduled_at)` justo después de generar el calendario.

---

## Google Cloud (crítico para hackathon)

| Servicio | Uso | Costo aprox. por análisis |
|----------|-----|--------------------------|
| Cloud Vision API | Colores + labels del logo | $0.0015 |
| Gemini 1.5 Flash | Análisis web + posts + generación de captions | $0.002 |
| Cloud Storage | 7 imágenes generadas por análisis | $0.001 |
| Pollinations.ai | Generación de imágenes (gratuito; fallback: placeholder con colores del ADN) | $0.00 |
| **Total** | | **~$0.005 USD** |

Con $500 USD en créditos → capacidad para ~100,000 análisis completos.

**Variables de entorno nuevas requeridas:**
```
GOOGLE_CLOUD_PROJECT=<project-id>
GOOGLE_APPLICATION_CREDENTIALS=/app/google-credentials.json
GOOGLE_CLOUD_STORAGE_BUCKET=agente-cosmic-assets
```

---

## Protección de acceso

Cloudflare Access (Zero Trust) — configuración en dashboard de Cloudflare, sin código:
1. Agregar el dominio a Cloudflare
2. Crear Application → Self-hosted → URL del servidor
3. Policy: permitir emails específicos (cliente + equipo)
4. Los visitantes ven login de Cloudflare antes de llegar a la landing

---

## Tests mínimos requeridos

- `TestAnalysisJob` — creación y transiciones de estado
- `TestBrandDNAExtractor` — mock del scraping + respuesta de Gemini
- `TestLogoAnalyzer` — mock de Cloud Vision API
- `TestContentGenerator` — dado un BrandDNA, genera 7 posts válidos
- `TestEmailSender` — verifica estructura del email #1 y que se programan 6 jobs
- `TestStatusEndpoint` — polling devuelve progreso correcto
