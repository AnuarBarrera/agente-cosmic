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
2. Optionally upload a logo and up to 7 product photos
3. The agent extracts the brand's personality fingerprint — colors, voice, audience, keywords
4. A **Gemini Art Director** designs a creative lifestyle scene for each post
5. **Imagen 3** generates photo-realistic backgrounds; **PIL** composites text, CTAs, and tags
6. All 7 days are generated upfront — review, approve, or request changes from the dashboard immediately, no waiting between days
7. Daily reminder emails nudge you when it's time to publish. At the end of each week, a short survey decides whether to generate the next one — the calendar keeps rolling for as long as the user wants

**From URL (or description) to a full week of ready-to-publish content, generated upfront.**

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
| Brand DNA | Merges all signals: name, tone, audience, colors, keywords | PostgreSQL |
| Caption Generation | Generates 7 distinct captions aligned to brand voice | Vertex AI - Gemini 2.5 Flash |
| Art Direction | Writes a creative scene prompt per post | Vertex AI - Gemini 2.5 Flash |
| Background Generation | Photo-realistic background with Imagen 3 | Vertex AI - Imagen 3 |
| Product Integration | Imagen 3 Edit swaps background, preserves product | Vertex AI - Imagen 3 Edit |
| Text Composition | PIL composites headline, subtitle, CTA, tag with brand colors | Pillow (local) |
| Asset Storage | Uploads to GCS via IAM (uniform access, no ACLs) | Google Cloud Storage |
| Delivery | All 7 images generated upfront. Daily reminder emails scheduled in Redis | Mailgun - Redis/RQ |
| Continuation | End-of-week survey decides whether to generate the next 7 days | Redis/RQ |

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
- **Upfront generation, resumable continuation**: All 7 images are generated in one background job instead of trickling in day by day. Each week's continuation is its own tracked job (`ContentCalendar.next_week_generating`), with a confirmation email on completion so the user never has to babysit the tab.
- **Robust LLM output parsing**: Caption and image-copy generation extract the JSON payload with a regex pass before parsing, tolerating trailing text a model may add around the array/object — a real failure mode observed in production, more likely on prompts that trigger extra safety caveats (health, finance, legal niches).
- **Safe deploy checks**: a management command inspects the RQ "started" job registry before a worker restart, so a live generation job never gets silently killed mid-run. A companion command backfills any image left pending by an older deploy. Both are meant to run as an ordered pre/post-deploy check — see [Deployment](#deployment).
- **Cache-busted asset URLs**: regenerated images reuse the same storage path, so every upload appends a versioned query parameter — otherwise browsers keep serving the previous cached image after a regeneration.
- **Test suite as a deploy gate**: 290+ pytest tests (unit + integration, external AI/storage calls mocked) run before every deploy; the two commands above are part of the same pre-flight discipline, laying the groundwork for a CI pipeline to enforce this automatically on every push.

---

## Demo

Live at `https://cosmic.anuarbarrera.dev`

**Golden path (under 3 minutes):**
1. Describe your business — a URL is optional, not required
2. Optionally upload a logo + up to 7 product photos
3. Watch real-time progress as the agent extracts Brand DNA and generates the week
4. Review your 7-post calendar — approve, edit, or request changes per post
5. Publish at your own pace; daily reminder emails nudge you when it's time
6. At the end of the week, a short survey decides whether to keep the calendar going

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

## Deployment

Since generation now runs as a single upfront background job per week instead of
trickling in day by day, an RQ worker can be mid-generation at any moment. Restarting
`rqworker` kills whatever job is running at that exact instant — scheduled/queued jobs
survive in Redis, but the one actively executing does not. Updating a running instance
follows three steps, in order:

```bash
# 1. Wait until no job is actively running (exit 0 = safe, exit 1 = something's running)
until docker compose exec -T backend python manage.py check_rq_safe_to_deploy; do
  sleep 30
done

# 2. Pull and restart
git pull
docker compose up -d --force-recreate --no-deps backend rqworker

# 3. Backfill any image left pending by an older deploy (dry-run first)
docker compose exec -T backend python manage.py backfill_missing_images --dry-run
docker compose exec -T backend python manage.py backfill_missing_images
```

The full pytest suite is expected to pass before step 2 — currently a manual gate,
with CI (running the same suite automatically on every push) as the natural next step.

---

## Project Structure

```
core/
  brand_dna/              # Brand DNA extraction + user-facing views
    extractors/
      web_scraper.py      # CSS color harvest + Gemini text analysis
      manual_extractor.py # ManualBrandExtractor from plain-text description
      logo_analyzer.py    # Gemini multimodal color extraction
    auth_views.py         # Registration, Google OAuth, soft delete, reactivation
    rate_limits.py        # Per-user weekly calendar + regeneration limits
  content_pipeline/       # Content generation + delivery
    generators/
      text_generator.py   # Gemini 2.5 Flash caption generation
      image_generator.py  # Art Director + Imagen 3 + PIL compositor
    tasks.py              # RQ tasks: upfront generation, weekly continuation, email delivery
    email_sender.py       # Mailgun + RQ scheduling
    management/commands/
      backfill_missing_images.py  # Backfill images left pending by an older deploy
  tenant_management/      # Users, tenants, plans, invitation codes
    management/commands/
      cleanup_deactivated_images.py  # Purge GCS images after 30 days
  shared/                 # Middleware, metrics, audit, validators
    metrics.py            # 20 Prometheus custom metrics
    management/commands/
      check_rq_safe_to_deploy.py  # Pre-deploy check: no active RQ jobs before a worker restart
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
