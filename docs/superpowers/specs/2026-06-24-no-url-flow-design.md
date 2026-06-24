# No-URL Brand Analysis Flow Design

## Goal

Allow users without a website to use Agente Cosmic by describing their business manually. Gemini structures the description into the same brand DNA format that WebScraper produces, so the entire downstream pipeline (logo analysis, posts analysis, content generation) works without changes.

## Context

The current flow requires a `business_url` as the primary input. `WebScraper.extract(url)` scrapes the site, extracts CSS colors, and sends everything to Gemini to produce structured brand data (`business_name`, `description`, `keywords`, `audience`, `tone`, `brand_colors`). Community validation revealed that many target users (small businesses, market vendors, freelancers) don't have a website.

The existing pipeline already handles optional inputs gracefully:
- Logo analysis runs only if a logo file is uploaded
- Posts analysis accepts text, images, or a social media profile URL (all optional)
- Product images are optional

Only WebScraper strictly requires a URL. This spec replaces that step with a Gemini-powered extractor when no URL is provided.

## Architecture

```
User chooses mode on landing page:

Mode A: "Tengo sitio web"          Mode B: "No tengo sitio web"
├─ business_url (required)          ├─ business_name (required)
├─ logo (optional)                  ├─ business_description (required)
├─ posts/profile (optional)         ├─ logo (optional)
└─ product images (optional)        ├─ posts/profile (optional)
                                    └─ product images (optional)
        │                                    │
        ▼                                    ▼
  WebScraper.extract(url)        ManualBrandExtractor.extract(name, desc)
        │                                    │
        └────────── same JSON schema ────────┘
                         │
                         ▼
              analyze_brand_task (unchanged)
                    ├─ LogoAnalyzer (if logo)
                    ├─ PostsAnalyzer (if posts/profile)
                    └─ BrandDNA.objects.create(...)
                         │
                         ▼
              content_generation_task (unchanged)
```

## Components

### 1. Model changes

**AnalysisJob** (`core/brand_dna/models.py`):
- Add `business_description = models.TextField(blank=True, default='')` — free-text description of the business for no-URL flow.
- Change `business_url` from `URLField()` to `URLField(blank=True, default='')` — no longer required.
- Add validation: at least one of `business_url` or `business_description` must be non-empty.

**BrandDNA** (`core/brand_dna/models.py`):
- Change `business_url` from `URLField()` to `URLField(blank=True, default='')` — stores empty string when no URL.

**Migration:** Schema migration adding `business_description` field and altering `business_url` to allow blank.

### 2. ManualBrandExtractor

**New file:** `core/brand_dna/extractors/manual_extractor.py`

```python
class ManualBrandExtractor:
    def extract(self, business_name: str, description: str) -> dict:
        """
        Uses Gemini to structure a user's business description into the
        same format WebScraper.extract() returns.

        Returns: {
            'business_name': str,
            'description': str,
            'keywords': list[str],
            'audience': str,
            'tone': str,
            'brand_colors': list[str],  # empty — no CSS to extract
        }
        """
```

The Gemini prompt:
- Receives: business name + free-text description
- Generates: structured JSON with `business_name`, `description` (refined), `keywords` (5-8), `audience`, `tone`
- `brand_colors` is always `[]` — there's no website to extract colors from. If the user uploads a logo, LogoAnalyzer provides colors downstream.

Uses `track_external_api('gemini')` and `record_tokens(resp)` for observability, matching existing extractor patterns.

### 3. Pipeline change

**File:** `core/brand_dna/tasks.py`

In `analyze_brand_task`, replace the hardcoded WebScraper call:

```python
# Before
web_data = scraper.extract(job.business_url)

# After
if job.business_url:
    web_data = WebScraper().extract(job.business_url)
else:
    from core.brand_dna.extractors.manual_extractor import ManualBrandExtractor
    web_data = ManualBrandExtractor().extract(
        business_name=job.business_description.split('\n')[0][:100],
        description=job.business_description,
    )
```

Everything downstream remains unchanged — `LogoAnalyzer`, `PostsAnalyzer`, `BrandDNA.objects.create(...)`, `content_generation_task`.

### 4. View changes

**File:** `core/brand_dna/views.py` — `analyze_submit`

Accept `business_description` from POST data. Validate that at least one of `business_url` or `business_description` is provided. Create AnalysisJob with whichever fields are present.

```python
business_url = request.POST.get('business_url', '').strip()
business_description = request.POST.get('business_description', '').strip()

if not business_url and not business_description:
    return render(request, 'brand_dna/landing.html', {
        'error': 'Ingresa la URL de tu negocio o una descripción.',
    })
```

### 5. Frontend — two-mode form

**File:** `core/brand_dna/templates/brand_dna/landing.html`

Two tabs at the top of the form card:
- **Tab 1: "Tengo sitio web"** (default) — shows current URL input (required within this tab)
- **Tab 2: "No tengo sitio web"** — shows:
  - `business_name` (text input, required within this tab): "Nombre de tu negocio"
  - `business_description` (textarea, required within this tab): "Describe tu negocio — ¿Qué vendes? ¿A quién? ¿Qué te hace diferente?"

Common fields below both tabs (always visible):
- Logo upload (optional)
- Post images / posts text / profile URL (optional)
- Product images (optional)

JavaScript toggles visibility and required attributes based on active tab. A hidden input or the presence/absence of `business_url` tells the backend which mode was used.

### 6. Results page adaptation

**File:** `core/brand_dna/templates/brand_dna/results.html`

When `brand_dna.business_url` is empty, don't show the "Visit website" link. Show the business name as the header instead.

## Testing

- **ManualBrandExtractor unit tests:** Mock Gemini, verify JSON output schema matches WebScraper output.
- **Pipeline test:** Job without URL uses ManualBrandExtractor, job with URL uses WebScraper.
- **View test:** POST without URL but with description creates job. POST without both returns error.
- **Frontend:** Manual browser test — toggle between tabs, submit both modes.

## Out of scope

- Conversational chat with Gemini
- Auto-detection of URL vs description in a single field
- Color picker for manual brand colors
- Social media API integration (scraping profile URL already works)
