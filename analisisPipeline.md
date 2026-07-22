# Análisis de errores reportados — sesión de prospección (2026-07-22, ~22:11–22:55 UTC)

Anuar reportó varios problemas distintos durante ~1-2 horas de generación de
muestras (`sample_reel`) para prospección, bajo la cuenta `contacto.neia@gmail.com`.
Se investigaron 7 `AnalysisJob` reales de esa ventana (todos modo `sample_reel`):

| Hora (UTC) | Negocio | Job ID | Resultado |
|---|---|---|---|
| 22:11:12 | Rios Sevicios dentales | `263916f2` | reel completo |
| 22:19:54 | Perrus K-9 | `2a26ee60` | reel completo |
| 22:23:16 | Maika Pet's | `64ea8ad3` | **solo imagen (video falló)** |
| 22:26:50 | Salud en mi mente | `cbda4aac` | reel completo |
| 22:37:29 | Maika Pet's | `f9abdb70` | **solo imagen (video falló)** |
| 22:39:26 | Salud en mi mente | `98d9c335` | reel completo |
| 22:49:06 | Maika Pet's | `24a7668d` | **solo imagen (video falló)** |

Este documento cubre cada problema reportado, con nivel de confianza distinto
según la evidencia encontrada. **No se hizo ningún cambio de código — solo
diagnóstico**, para que el servidor de desarrollo decida cómo y cuándo corregir.

---

## 1. "Elegí reel y me dio imagen" — CONFIRMADO, causa raíz exacta

**Evidencia:** los 3 intentos de "Maika Pet's" (único negocio con apóstrofe en
el nombre, entre los 7 probados) fallaron en generar video, cayendo al
fallback de solo-imagen. Los 3 muestran el **mismo error exacto** en los logs:

```
ERROR reel_generator ... ReelGenerator.generate error: Command ['ffmpeg', ...
  drawtext=...:text='Maika Pet\'s es tu\ncentro integral.':...
] returned non-zero exit status 8.
```

**Causa raíz:** `_escape_drawtext()` en
`core/content_pipeline/generators/reel_generator.py` (línea 104-109) escapa
un apóstrofe como `\'`:

```python
def _escape_drawtext(text: str) -> str:
    text = text.replace('\\', '\\\\')
    text = text.replace(':', '\\:')
    text = text.replace("'", "\\'")   # <-- insuficiente
    text = text.replace('%', '\\%')
    return text
```

El problema es que el texto ya se envuelve en comillas simples al construir
el filtro (`text='{_escape_drawtext(line)}'`). Para ffmpeg, escapar un
apóstrofe **dentro** de una cadena ya delimitada por comillas simples no se
hace con `\'` — hay que cerrar la comilla, escapar una comilla literal, y
reabrir: el patrón correcto es `'\''`. La secuencia `\'` que usa el código
actual **rompe el parser del filtergraph de ffmpeg**, y el proceso completo
de `ffmpeg` truena con exit status 8.

**Impacto:** esto afecta el overlay de subtítulos (línea 802) Y el overlay
de CTA (línea 292) — es decir, **cualquier negocio cuyo nombre, caption o
narración contenga un apóstrofe** pierde el video completo del reel y cae
al fallback de solo-imagen, sin aviso claro al usuario de que "reel" en
realidad no se generó como reel. Nombres con apóstrofe son comunes
(`Domino's`, `McDonald's`, `Denny's`, cualquier "Nombre's ..."), así que esto
no es un caso aislado.

**No se propone el fix exacto aquí** (es un detalle de escaping de ffmpeg que
vale la pena verificar con cuidado) — se deja para que el servidor de
desarrollo lo resuelva y lo pruebe explícitamente con un nombre con
apóstrofe.

---

## 2. "Una de las palabras salió en un color que no contrastaba" — CONFIRMADO

**Evidencia:** frame extraído del reel de "Rios Sevicios dentales" (t≈2s,
portada, plantilla `dynamic-background`):

Texto: "TU SONRISA, NUESTRA **PASIÓN**." — la palabra resaltada "PASIÓN" se
renderiza casi invisible contra el fondo en ese instante del video.

**Causa raíz:** en
`core/content_pipeline/hyperframes_reel/compositions/portada-dynamic-background.html`,
tanto los "blobs" de fondo animados como la palabra resaltada usan la MISMA
variable de color:

```css
.blob { background: var(--primary_color); }
#hook-highlight { color: var(--primary_color); }
```

Los blobs se animan con GSAP (`tl.to('.blob-1', {x:200, y:150, ...})`) y se
mueven por toda la pantalla durante los 3 segundos de la portada. Cuando un
blob pasa detrás de la palabra resaltada — que usa el mismo color — el
contraste cae a prácticamente cero. No es un problema de qué color eligió
la marca en particular; es estructural de la plantilla: **cualquier negocio
con esta plantilla puede sufrir esto**, dependiendo de en qué punto de la
animación quede la palabra resaltada.

---

## 3. Poster/miniatura con texto "cortado"/pálido — CONFIRMADO (hallazgo adicional)

No estaba en la lista original de Anuar, pero se encontró al revisar el
mismo reel de Perrus K-9: el frame usado como **poster** (miniatura estática
que se ve antes de reproducir el video) se extrae en el segundo 1
(`_extract_poster_frame`, `ffmpeg -ss 1 ...`), que cae **dentro** de la
animación de aparición del texto de la portada:

```js
tl.from('#hook', { opacity: 0, scale: 0.9, duration: 1.5, ease: 'power2.out' }, 0.5);
```

La animación empieza en t=0.5s y tarda 1.5s en llegar a opacidad completa
(termina en t=2.0s). El poster se extrae en t=1.0s — es decir, a mitad de la
animación de fade-in — resultando en una miniatura con el texto visiblemente
pálido/translúcido, no en su estado final legible.

Esto podría ser parte de lo que Anuar percibió como "la presentación falló"
— vale la pena que el servidor de desarrollo lo revise junto con el
problema #2, ya que ambos afectan la legibilidad del texto de portada.

---

## 4. "Elegí 7 días de contenido por error y me generó video con logo" — NO CORROBORADO EN LOS DATOS, pero se encontró un riesgo real relacionado

**No se encontró ningún job con `generation_mode='full'`** en las últimas 4+
horas — los 7 jobs de esta sesión son todos `sample_reel`. No fue posible
confirmar directamente cuál generación específica corresponde a este reporte
(es posible que haya sido fuera de la ventana revisada, o que los detalles
se hayan mezclado entre las muchas pruebas rápidas de la sesión).

**Sin embargo, se encontró un riesgo de diseño real y relacionado:**

- En `new_analysis.html` línea 111, el radio button de **"Calendario completo
  (7 días)" viene marcado (`checked`) por default**.
- En `views.py` línea 148, si el campo `generation_mode` no llega en el POST
  por cualquier razón, el fallback del servidor **también es `full`**:
  ```python
  requested_mode = request.POST.get('generation_mode', AnalysisJob.MODE_FULL)
  ```

Es decir, **tanto el default de la UI como el fallback del backend apuntan a
la opción más cara/completa**, no a la muestra rápida. Si por cualquier
motivo (doble clic, recarga de página, un radio que no quedó bien
seleccionado) el modo no se transmite correctamente, el sistema genera un
calendario completo de 7 días en vez de una sola muestra — el peor caso
posible en términos de costo y tiempo de espera. Vale la pena que el
servidor de desarrollo evalúe si el fallback debería ser más conservador
(o bloquear el submit si el modo es ambiguo) cuando el usuario tiene
`allows_sample_generation` activo.

---

## 5. "Uno de los subtítulos no salió" (Salud en mi mente) — NO CONFIRMADO

Se revisaron frames de los 2 reels de "Salud en mi mente" (`cbda4aac` y
`98d9c335`) a intervalos de ~2.5s a lo largo de todo el video (0s-20s en
ambos) y los subtítulos aparecen consistentes en todos los puntos
muestreados — no se encontró un hueco visible. Es posible que el problema
esté en un intervalo más corto que el muestreado, o en una versión/generación
distinta a las 2 revisadas. Se recomienda que Anuar indique el timestamp
aproximado (segundo del video) donde notó el subtítulo faltante, o adjunte
captura, para poder aislarlo con precisión.

## 6. "En la veterinaria las imágenes salieron cortadas" — NO CONFIRMADO

Se revisaron las 3 imágenes de fallback de "Maika Pet's" (las que se
generaron cuando el video falló, ver punto #1) y varios frames del reel de
"Perrus K-9" — ninguna mostró recorte/cropping visible. Es posible que este
reporte se refiera al mismo problema del punto #1 (Maika Pet's, donde
"imagen" salió en vez de "reel", y esa sustitución inesperada se percibió
como "cortado"), o a un caso distinto no capturado en la ventana revisada.
Se recomienda confirmar con Anuar si esto es lo mismo que el punto #1 o un
problema visual distinto.

---

## 7. "La tarjeta verde de 'no es necesario esperar' solo salió una vez" — NO CONFIRMADO, hipótesis a validar

El texto que describe Anuar corresponde al bloque `.leave-notice` en
`results.html` (fondo/borde verdes, "☕ Esto toma unos minutos... puedes
cerrar esta pestaña con confianza"). Este bloque es parte estática del
estado de "cargando" (`{% if job.status == 'done' %}...{% else %}` — el
`else`), por lo que en teoría debería aparecer en TODA generación mientras
el job no esté `done`, no solo una vez.

Se encontró un mecanismo real que podría explicarlo parcialmente: existe una
protección anti-duplicados en `views.py` (línea 143-145) que, si se reenvía
un análisis con el **mismo texto exacto** de `business_description` mientras
el job anterior sigue `pending`/`processing`, redirige al job existente en
vez de crear uno nuevo:

```python
duplicate_job = AnalysisJob.objects.filter(
    user=request.user, business_description=business_description,
    status__in=[STATUS_PENDING, STATUS_PROCESSING],
).first()
if duplicate_job:
    return redirect('results', job_id=duplicate_job.id)
```

Si esto ocurrió, el usuario vería la página del job YA EXISTENTE — que podría
estar en un punto distinto de su ciclo de vida (o ya `done`, saltándose la
pantalla de carga por completo). En los 7 jobs revisados esta sesión, los
tiempos entre envíos del mismo negocio (~12-14 min) son mayores al tiempo de
generación (~6 min), así que no parece que este mecanismo se haya activado
en estos casos puntuales — pero es la explicación más plausible que se
encontró en el código y vale la pena que el servidor de desarrollo la
descarte o confirme con más detalle (¿en qué negocio ocurrió exactamente?).

---

## Resumen de confianza

| # | Problema | Confianza | Causa raíz identificada |
|---|---|---|---|
| 1 | Reel pedido, imagen entregada | 🟢 Alta | Sí — escaping de apóstrofe en `_escape_drawtext` rompe ffmpeg (exit 8) |
| 2 | Palabra sin contraste | 🟢 Alta | Sí — `primary_color` compartido entre blob de fondo y texto resaltado |
| 3 | Poster con texto pálido | 🟢 Alta | Sí — poster extraído en t=1s, dentro de la animación de fade-in (0.5-2.0s) |
| 4 | "7 días" por error | 🟡 Media | Riesgo real de diseño confirmado (default a `full` en UI y backend), pero no se corroboró la instancia específica |
| 5 | Subtítulo faltante | 🔴 Baja | No reproducido en las 2 muestras revisadas |
| 6 | Imágenes cortadas (veterinaria) | 🔴 Baja | No reproducido en las muestras revisadas |
| 7 | Tarjeta verde solo 1 vez | 🟡 Media | Mecanismo plausible identificado (redirect anti-duplicado), no confirmado como causa exacta |

---

## ACTUALIZACIÓN 2026-07-22 (Claude, misma sesión que hizo pull de este análisis) — Hallazgos 1, 2 y 3 RESUELTOS

Verificados independientemente contra el código real antes de tocar nada, y cada fix probado contra ffmpeg/ffprobe real (no solo mockeado) antes de darlo por bueno.

**#1 — apóstrofe rompe el reel.** Se probaron empíricamente TODAS las secuencias de
escape candidatas para `text='...'` inline contra `ffmpeg -filter_complex` real
(el contexto exacto que usa producción, con labels/chaining — no `-vf` simple):
ninguna funciona. `\'` (el código anterior) revienta el parser (`Invalid argument`
/ exit ≠0). El patrón estándar `'\''` (cerrar comilla, escapar, reabrir) evita el
crash pero **renderiza el texto vacío en silencio** — peor que el crash. La
solución verificada: `textfile=` en vez de `text='...'` — el texto se escribe a
un archivo temporal y ffmpeg lo lee de ahí, sin pasar por el parser de comillas
del filtergraph. Con esto, `:` y `'` dejan de necesitar escape; solo `\` y `%`
siguen necesitándolo (sintaxis de expansión propia de drawtext). Afecta 5 puntos
de uso: hook (3 segmentos), CTA, y subtítulos — todos migrados. Verificado con
un render real end-to-end usando "Maika Pet's" en hook, CTA y narración: el
video se genera sin error y el apóstrofe se ve correctamente en el frame
extraído (hook con highlight box + subtítulo).

**#2 — palabra sin contraste.** `#hook-highlight` en
`portada-dynamic-background.html` cambiado de `var(--primary_color)` a
`var(--text_color)` — esta última variable ya se computaba y pasaba
(`reel_generator.py::_readable_text_color(primary_color)`) específicamente para
contrastar contra `primary_color`, y ya es el patrón usado correctamente en
`portada-panel-wipe.html`. Verificado por inspección de código y precedente, no
con un render real de HyperFrames (requiere Chrome headless, fuera del alcance
rápido de esta sesión) — vale la pena una verificación visual real en la
siguiente tanda de reels.

**#3 — poster pálido.** `_extract_poster_frame` ahora acepta `offset_seconds`;
`generate()` usa 2.5s (en vez de 1s) cuando hay portada HyperFrames
(`has_branding=True`) — después del fade-in más lento de las 3 plantillas
(máx. 2.0s) y antes de que termine la portada (3.0s). Sin portada, sigue en 1s
sin cambios.

Todos los tests existentes actualizados + 83/83 en `test_reel_generator.py`,
259/259 en toda la suite de `content_pipeline`. Commits: ver `git log`.

**Pendiente, no tocado — decisión de producto, no bug:** hallazgo #4 (defaults a
`full` en UI y backend). No se cambió sin confirmar con Anuar si el
comportamiento actual es intencional o debe volverse más conservador.
