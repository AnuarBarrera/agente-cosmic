# Catálogo de templates para portada/contraportada de reels — diseño

## Contexto

La Parte B (portada/contraportada con HyperFrames, ver `2026-07-16-reels-hyperframes-intro-outro-design.md`, commits `ce1b847`..`494b3db`) se construyó y verificó en real (HALLAZGO 71), pero Anuar rechazó el resultado visual: "el logo no se ve bien y el fondo blanco al inicio y final tampoco, no podemos dar por terminado con esos resultados". Pidió explícitamente quitar el logo y mejorar el fondo, y aclaró que quería algo "dinámico" — "para que pasemos de un reel genérico a casi un video de agencia publicitaria", no un fondo de color plano.

Se generaron 3 bocetos de motion design (tipografía cinética, fondo con blobs animados, panel wipe estilo noticiero) vía un agente externo (agy/Gemini) y se renderizaron localmente con HyperFrames (sin costo de API) para comparación real. Anuar confirmó: el panel wipe "es exactamente lo que pensaba" y renderizó limpio; los otros 2 tenían un bug real de texto desbordado (confirmado visualmente: "Innovación tecnológica," se salía del cuadro por ambos lados a 110px de fuente en un contenedor `flex` sin wrap).

A partir de ahí, Anuar pidió un enfoque más ambicioso, calcado del patrón que Cosmic ya usa para las imágenes de posts (`image_generator.py::_choose_template_for_image` — catálogo fijo de templates + selección por IA; `_choose_font_preset` — rotación determinista de fuente por seed semanal): un catálogo de 3-5 templates deterministas para portada/contraportada, donde el LLM elige cuál usar según el guion, y los colores/tipografías salen del sistema que Cosmic ya tiene (paleta de marca + los 5 presets de fuente ya usados en imágenes/carrusel).

## Decisiones de diseño (confirmadas con Anuar)

1. **Logo:** se deja de renderizar en las composiciones HTML de portada/contraportada. El resto del pipeline (`BrandDNA.logo_url`, cualquier uso en análisis u otras partes de la app) no se toca.
   - **Nota para revisión de Anuar:** para no dejar un parámetro sin efecto real dentro del pipeline de reels específicamente (principio ya establecido en esta sesión: no dejar código/parámetros muertos), este diseño propone remover `logo_url` de la cadena de llamadas `tasks.py` → `ReelGenerator.generate()` → `_generate_clips_with_branding()` → `_generate_branded_segment()` por completo, ya que dejaría de tener efecto en las 3 últimas. **Esto NO toca** el campo `BrandDNA.logo_url` en el modelo, ni ningún otro código que lo use fuera del pipeline de reels. Si esta interpretación no es la que tenías en mente, avisar antes de aprobar la spec.

2. **Catálogo:** 3 templates deterministas, mezclando los 3 estilos explorados (corrigiendo los 2 que fallaron):
   - `panel-wipe` — el aprobado, sin cambios de concepto.
   - `kinetic-typography` — corregido: texto en flujo de bloque normal con `max-width` (no `flex` sin wrap), fuente reducida a un tamaño que quepa sin desbordar.
   - `dynamic-background` — corregido: mismo fix de texto; blobs de fondo con opacidad reducida donde pasa el texto para mantener contraste legible.

3. **Selección de template — vía IA**, mismo patrón que `_choose_template_for_image`: una llamada a Gemini (texto, sin imagen) lee `hook_text`/`tag_cta` del guion y devuelve el template que mejor calce, con fallback a elección aleatoria si falla. Se llama **una sola vez por reel** (no una vez por portada y otra por contraportada) — portada y contraportada de un mismo reel siempre usan el mismo template.

4. **Tipografía — reutiliza el sistema de fuentes ya existente**, con el MISMO seed semanal que las imágenes de esa semana (`filename_prefix.rsplit('-day', 1)[0]`), para que portada/contraportada calcen visualmente con el resto del contenido de esa semana.

## Arquitectura técnica

### Refactor: `_FONT_PRESETS`/`_choose_font_preset` se mueven a un módulo compartido

Hoy viven en `core/content_pipeline/generators/image_generator.py` (líneas 29-48). Se mueven, sin cambios de comportamiento, a un archivo nuevo `core/shared/font_presets.py`:

```python
import hashlib
import random

FONT_PRESETS = [
    {'font_family': "'Poppins', sans-serif", 'font_import': 'Poppins:wght@400;600;700;900'},
    {'font_family': "'Playfair Display', serif", 'font_import': 'Playfair+Display:wght@400;600;700;900'},
    {'font_family': "'Space Grotesk', sans-serif", 'font_import': 'Space+Grotesk:wght@400;500;600;700'},
    {'font_family': "'Bebas Neue', sans-serif", 'font_import': 'Bebas+Neue'},
    {'font_family': "'DM Sans', sans-serif", 'font_import': 'DM+Sans:wght@400;500;700'},
]


def choose_font_preset(seed: str) -> dict:
    """Elige una fuente de forma determinista a partir de `seed` — mismo seed
    => mismo preset, usado tanto por imágenes/carrusel como por portada/
    contraportada de reels para consistencia visual dentro de una semana."""
    if not seed:
        return random.choice(FONT_PRESETS)
    digest = hashlib.sha256(seed.encode()).hexdigest()
    idx = int(digest, 16) % len(FONT_PRESETS)
    return FONT_PRESETS[idx]
```

`image_generator.py` pasa a importar `FONT_PRESETS as _FONT_PRESETS` y `choose_font_preset as _choose_font_preset` desde `core.shared.font_presets` en vez de definirlos localmente — mismo comportamiento externo, cero cambios en sus tests (los tests de `image_generator.py` que mockean `_choose_font_preset` deben mockear la ruta re-exportada, no la nueva ubicación — se detalla en el plan).

### Fuentes vendorizadas localmente

HyperFrames NO debe depender de red al renderizar (lección de la Parte B: rutas mal resueltas se reescriben silenciosamente a un CDN externo). De los 5 presets, solo Poppins-Bold ya está vendorizado (`hyperframes_reel/assets/Poppins-Bold.ttf`). Se agregan 4 archivos más a `core/content_pipeline/hyperframes_reel/assets/` (Google Fonts, licencia OFL, libres de redistribuir), en el peso más "black/bold" disponible de cada familia:

- `PlayfairDisplay-Bold.ttf` (weight 900 disponible en esta familia)
- `SpaceGrotesk-Bold.ttf` (weight 700, la familia no tiene 900)
- `BebasNeue-Regular.ttf` (peso único de esta familia — ya es un display font muy marcado)
- `DMSans-Bold.ttf` (weight 700, la familia no tiene 900)

Cada composición HTML declara los 5 bloques `@font-face` (uno por preset) pero usa solo el seleccionado vía `font-family: var(--font_family), sans-serif;` — mismo mecanismo de variable CSS ya usado hoy para colores.

### Composiciones: 6 archivos (3 templates × portada/contraportada)

Mismo patrón de nombres que hoy, extendido con el sufijo del template:

- `compositions/portada-panel-wipe.html` / `compositions/contraportada-panel-wipe.html`
- `compositions/portada-kinetic-typography.html` / `compositions/contraportada-kinetic-typography.html`
- `compositions/portada-dynamic-background.html` / `compositions/contraportada-dynamic-background.html`

Se elimina `compositions/portada.html`/`compositions/contraportada.html` (reemplazados por el catálogo) y el directorio `compositions/drafts/` (los 3 bocetos de comparación, ya cumplieron su propósito).

**Variables de composición** (mismas para las 6, `data-composition-variables`):
- Portada: `hook_before`, `hook_highlight`, `hook_after`, `primary_color`, `text_color`, `font_family`.
- Contraportada: `cta_text`, `primary_color`, `text_color`, `font_family`.
- (`logo_url` se elimina de ambas — ver decisión 1.)

**Tratamiento visual por template** (generalizando los 3 bocetos aprobados, usando `var(--primary_color)`/`var(--text_color)` en vez de colores hardcodeados como tenían los bocetos, para que funcione con cualquier color de marca):
- `panel-wipe`: fondo neutro oscuro fijo (`#1a1a2e`), 2 paneles (`primary_color` + una variante más clara del mismo tono al 70% de opacidad, no un segundo color calculado) entran deslizándose, texto/CTA en `text_color` sobre el panel.
- `dynamic-background`: fondo neutro oscuro fijo (`#1a1a2e`), 3 blobs en `primary_color` con opacidades distintas (0.6/0.5/0.4, sin necesidad de calcular tonos derivados) moviéndose lentamente, texto simple en blanco encima.
- `kinetic-typography`: fondo claro fijo (`#ffffff`, da variedad real de tema frente a los otros 2 oscuros), texto en `#1a1a2e` con stagger palabra-por-palabra, palabra resaltada / CTA en badge `primary_color` + `text_color`, líneas decorativas en `primary_color`.

### `reel_generator.py`

**Nuevo método** `_choose_reel_template(self, hook_text: str, tag_cta: str) -> str`, mismo patrón que `_choose_template_for_image`: prompt de texto a Gemini pidiendo JSON `{"template": "panel-wipe"|"kinetic-typography"|"dynamic-background"}` según qué template calza mejor con el tono del hook/CTA, con `try/except` + fallback a `random.choice(['panel-wipe', 'kinetic-typography', 'dynamic-background'])` si falla el parseo o la llamada.

**`_generate_branded_segment` cambia de firma** (elimina `logo_url`, agrega `template`/`font_family`):
```python
def _generate_branded_segment(self, kind: str, hook_text: str, highlight_word: str,
                               tag_cta: str, primary_color: str, template: str,
                               font_family: str) -> bytes | None:
```
Internamente: `composition = f'compositions/{kind}-{template}.html'`, `variables` deja de incluir `logo_url`, agrega `'font_family': font_family`.

**`_generate_clips_with_branding` gana el parámetro `filename_prefix`** (para derivar el seed de fuente) y pierde `logo_url`:
```python
def _generate_clips_with_branding(self, scene_prompts: list[str], hook_text: str,
                                   highlight_word: str, tag_cta: str, primary_color: str,
                                   filename_prefix: str) -> tuple[list[bytes], bool]:
```
Internamente, antes de generar portada/contraportada: calcula `font_seed` (mismo cálculo que `image_generator.py`: `filename_prefix.rsplit('-day', 1)[0] if '-day' in filename_prefix else filename_prefix`), `font_preset = choose_font_preset(font_seed)`, `template = self._choose_reel_template(hook_text, tag_cta)` — ambos se calculan UNA vez y se pasan a las 2 llamadas de `_generate_branded_segment` (portada y contraportada), incluidos sus reintentos.

**`generate()` pierde el parámetro `logo_url`** y pasa `filename_prefix` a `_generate_clips_with_branding` en vez de `logo_url`.

**`tasks.py`** deja de pasar `logo_url=brand_dna.logo_url if brand_dna else ''` a `reel_gen.generate(...)` (1 línea).

## Fuera de alcance

- No se toca `BrandDNA.logo_url` como campo de modelo, ni ningún otro uso de ese campo fuera del pipeline de reels.
- No se agregan más de 3 templates en esta iteración (Anuar puede pedir más después si quiere más variedad).
- No se implementa cálculo de tonos derivados de `primary_color` (shades/tints) — se usa opacidad para variar los blobs de `dynamic-background`, evitando código nuevo de manipulación de color.
- No se toca la verificación de duración/normalización de `_assemble_reel` (ya resuelta en la Parte B) — sigue funcionando igual, ya que sigue recibiendo clips de video normalizados a la misma resolución/fps.

## Testing

Mismo patrón ya establecido en esta sesión (`test_reel_generator.py`, `test_metrics.py` para las 2 tareas anteriores):
- `_choose_reel_template`: éxito (JSON válido → template correcto), fallback en error/JSON inválido.
- `_generate_branded_segment`: variables correctas por `kind` (sin `logo_url`, con `font_family`), ruta de composición correcta según `template`.
- `_generate_clips_with_branding`: cálculo de `font_seed` a partir de `filename_prefix` (con y sin sufijo `-dayN`), que `template`/`font_preset` se calculan una vez y se pasan igual a ambas llamadas.
- `core/shared/font_presets.py`: tests nuevos para `choose_font_preset` (movidos/adaptados de los que hoy cubren `image_generator._choose_font_preset`).
- `image_generator.py`: sus tests existentes de `_choose_font_preset`/`_FONT_PRESETS` deben seguir pasando sin cambios de comportamiento tras el refactor de import.

## Verificación real

Igual que la Parte B: al menos 1 generación real end-to-end por template (3 en total) para confirmar que cada uno renderiza sin desbordar texto, con buen contraste, y que el `font_family` seleccionado se aplica visiblemente. Documentar en `hallazgos.txt` siguiendo el formato ya establecido.
