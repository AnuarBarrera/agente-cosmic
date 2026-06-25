# Agente Cosmic — Brand DNA Extractor & AI Content Agent

> **Google AI Hackathon 2026 · Track 2: Optimize**
>
> An autonomous agent that reads a business URL (or a plain description), extracts its Brand DNA, and generates a full 7-day social media content calendar — images included — delivered straight to the inbox. No agency required.

---

## The Problem

Marketing agencies and freelancers spend **4-6 hours per client** manually studying a brand before creating any content. Small businesses cannot afford agencies. Agencies cannot scale this process.

---

## The Solution

1. **Drop a URL** — or describe your business in plain text (no website needed)
2. Optionally upload a logo, sample posts, and up to 7 product photos
3. The agent extracts the brand's personality fingerprint — colors, voice, audience, keywords
4. A **Gemini Art Director** designs a creative lifestyle scene for each post
5. **Imagen 3** generates photo-realistic backgrounds; **PIL** composites text, CTAs, and tags
6. Delivers Day 1 to your inbox. Days 2-7 arrive automatically, one per day

**From URL (or description) to ready-to-publish content in under 3 minutes.**

---

## Agentic Image Pipeline

Each post image is generated through a **multi-step agentic pipeline** — not a single prompt:

### Path A — Brand Scene (no product photo)
1. **Gemini Art Director** analyzes brand tone, keywords, and caption to write a custom lifestyle scene prompt. Banned: offices, laptops, generic workspaces.
2. **Imagen 3** renders a photo-realistic background.
3. **Gemini** generates headline, subtitle, CTA text, and tag in brand voice.
4. **PIL compositor** places text layers with brand palette colors.

### Path B — Product Scene (client uploads product photo)
1. **Gemini Art Director** reads the product image to suggest a premium environment.
2. **Imagen 3 Edit** swaps the background while preserving the product.
3. **Gemini** generates copy in multimodal mode — sees the actual product.
4. **PIL compositor** places text as in Path A.

Gemini decides which path to take per post. Days 4-7 without a product photo fall back to Path A.

---

## How It Works

| Stage | What happens | Google Cloud service |
|-------|-------------|---------------------|
| Web Analysis | Scrapes URL, extracts CSS hex colors, sends clean text to LLM | Vertex AI - Gemini 2.5 Flash |
| Manual Analysis | Structures plain-text business description (no URL required) | Vertex AI - Gemini 2.5 Flash |
| Logo Analysis | Reads logo bytes, detects dominant colors and visual elements | Vertex AI - Gemini 2.5 Flash (multimodal) |
| Post Analysis | Analyzes sample posts to infer posting style and tone | Vertex AI - Gemini 2.5 Flash (multimodal) |
| Brand DNA | Merges all signals: name, tone, audience, colors, keywords | PostgreSQL |
| Caption Generation | Generates 7 distinct captions aligned to brand voice | Vertex AI - Gemini 2.5 Flash |
| Art Direction | Writes a creative scene prompt per post | Vertex AI - Gemini 2.5 Flash |
| Background Generation | Photo-realistic background with Imagen 3 | Vertex AI - Imagen 3 |
| Product Integration | Imagen 3 Edit swaps background, preserves product | Vertex AI - Imagen 3 Edit |
| Text Composition | PIL composites headline, subtitle, CTA, tag with brand colors | Pillow (local) |
| Asset Storage | Uploads to GCS via IAM (uniform access, no ACLs) | Google Cloud Storage |
| Delivery | Day 1 sent immediately. Days 2-7 enqueued in Redis scheduler | Mailgun - Redis/RQ |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 5.2 - Python 3.12 |
| Database | PostgreSQL 16 |
| Queue | Redis - django-rq - RQ Scheduler |
| AI Text + Vision | Vertex AI - gemini-2.5-flash |
| AI Image Generation | Vertex AI - imagen-3.0-generate-001 |
| AI Image Editing | Vertex AI - imagen-3.0-capability-001 |
| Image Composition | Pillow (PIL) |
| Storage | Google Cloud Storage (agente-cosmic-assets) |
| Email | django-anymail - Mailgun |
| Observability | django-prometheus - 20 custom metrics |
| Infrastructure | Docker Compose - nginx - Cloudflare Tunnel |
| Auth | Django sessions - Google OAuth 2.0 |

---

## Engineering Reliability (Track 2 Focus)

- **Async processing**: All brand analysis runs in RQ workers. UI shows real-time progress (10% to 100%) while the job runs in the background.
- **Agentic retry with scene rotation**: If Imagen 3 fails QC, the Art Director generates a new scene prompt and retries. Never repeats a failed prompt. 10 static fallbacks guarantee variety.
- **No-URL mode**: Users without a website describe their business in plain text. ManualBrandExtractor uses Gemini to structure the description into the same Brand DNA schema.
- **Client-side image compression**: Photos compressed in-browser (max 1200px, JPEG 82%) via Canvas API before upload.
- **Top-biased 1:1 crop**: Portrait product photos pre-cropped to square with top-biased pivot via Pillow — preserving subjects instead of cropping faces.
- **Multi-tenant isolation**: TenantIsolationMiddleware enforces per-user data access. SessionTimeoutMiddleware logs out inactive sessions after 30 minutes.
- **Account soft delete**: Danger Zone lets users deactivate their account. Preserved for reactivation on re-registration. GCS images purged after 30 days via management command.
- **Invitation code system**: Admin generates COSMIC-XXXXXX codes. Redeemers promoted to tester group with expanded limits. Rate-limited to 5 attempts/hour.
- **Observability**: 20 Prometheus custom metrics — API latency histograms (Vertex AI, Gemini, GCS, Mailgun), RQ queue depth, active users, email counters.
- **Security hardened**: 4 white-box audits completed. IP spoofing fixed (X-Real-IP via Nginx), PII encryption fail-closed, GCS uniform IAM, cookie domain restricted, defusedxml for SVG parsing.
- **Per-user rate limits**: Calendar and regeneration limits per plan. Soft-deleted calendars count toward the limit.
- **Beta user cap**: Registration closes at MAX_REGISTERED_USERS (env var). Race condition protected.

---

## Demo

Live at `https://cosmic.anuarbarrera.dev`

**Golden path (under 3 minutes):**
1. Enter business URL or switch to "No tengo sitio web" and describe your business
2. Optionally upload logo + up to 7 product photos
3. Watch real-time progress as the agent extracts Brand DNA
4. Review your 7-post calendar — approve, edit, or request changes per post
5. Receive Day 1 immediately; Days 2-7 arrive automatically

---

## Business Case

**Target market:** Marketing agencies, freelancers, and SMBs in Latin America (50M+ SMBs, less than 5% have a defined social media strategy).

**Value proposition:** Reduces content calendar creation from 5 hours to 5 minutes while maintaining brand consistency across 7 days.

**Unit economics:** At $10/month per business, 1,000 clients = $10K MRR with near-zero marginal cost per analysis.

---

## Quick Start

```bash
git clone https://github.com/AnuarBarrera/agente-cosmic.git
cd agente-cosmic

cp .env.example .env
# Fill in: GOOGLE_CLOUD_PROJECT, MAILGUN_API_KEY, SECRET_KEY
# gcloud auth application-default login

docker compose up -d
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py collectstatic --noinput
docker compose exec backend python manage.py createsuperuser
```

Open `http://localhost:3002`.

**Google Cloud requirements:**
- Vertex AI API enabled (Gemini 2.5 Flash + Imagen 3)
- Cloud Storage bucket with uniform IAM access enabled
- IAM: allUsers with roles/storage.legacyObjectReader (public read, no listing)
- Service account: roles/aiplatform.user + roles/storage.objectAdmin

---

## Project Structure

```
core/
  brand_dna/              # Brand DNA extraction + user-facing views
    extractors/
      web_scraper.py      # CSS color harvest + Gemini text analysis
      manual_extractor.py # ManualBrandExtractor from plain-text description
      logo_analyzer.py    # Gemini multimodal color extraction
      posts_analyzer.py   # Gemini multimodal style analysis
    auth_views.py         # Registration, Google OAuth, soft delete, reactivation
    rate_limits.py        # Per-user weekly calendar + regeneration limits
  content_pipeline/       # Content generation + delivery
    generators/
      text_generator.py   # Gemini 2.5 Flash caption generation
      image_generator.py  # Art Director + Imagen 3 + PIL compositor
    tasks.py              # RQ tasks: pipeline + email delivery
    email_sender.py       # Mailgun + RQ scheduling
  tenant_management/      # Users, tenants, plans, invitation codes
    management/commands/
      cleanup_deactivated_images.py  # Purge GCS images after 30 days
  shared/                 # Middleware, metrics, audit, validators
    metrics.py            # 20 Prometheus custom metrics
saas_chatbot/             # Django settings, urls, wsgi
load_tests/               # Apache Bench stress test scripts
.cybersec-exceptions.md   # Accepted security exceptions with rationale
```

---

## Security

4 white-box cybersecurity audits completed (June 2026). 10+ vulnerabilities patched.
Accepted exceptions documented with rationale in `.cybersec-exceptions.md`.

---

## Built by

**Anuar Barrera** - [Tu Web MX](https://tuweb.mx) - [@AnuarBarrera](https://github.com/AnuarBarrera)

Built for the Google AI Hackathon 2026.
Powered by Vertex AI (Gemini 2.5 Flash + Imagen 3) - Google Cloud Storage - Pillow.
