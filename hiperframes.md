# HyperFrames (portada/contraportada de reel) — hallazgos y recomendación

Fecha: 2026-08-31
Contexto: investigación en vivo durante la generación de contenido del
calendario de "Pizarrones Aries" (miriam.pizarronesaries@gmail.com,
calendar_id `cbcf2fc9-a9fd-40a3-89ee-77b0b959e34c`).

## Qué es

`_generate_branded_segment()` en `core/content_pipeline/generators/reel_generator.py`
llama al binario de HyperFrames (renderizado con Chromium headless vía
subprocess) para generar dos clips de marca — portada (hook) y
contraportada (CTA) — que se anteponen/anexan al reel. `_wrap_with_branding()`
(línea ~668) corre ambas en paralelo con un timeout fijo de 120s
(`_HYPERFRAMES_TIMEOUT_SECONDS`, línea 68). Si cualquiera de las dos falla,
el reel se publica sin marca (`return clips, False`) — no bloquea el pipeline.

## Hallazgo: HyperFrames falla consistentemente por timeout bajo carga real

Durante la generación del mes completo de Pizarrones Aries (2026-08-31,
19:00:58–19:44:36 UTC, ~44 min totales), **los 3 reels generados (día 43,
50 y 57) fallaron los 3** al renderizar portada/contraportada:

```
HyperFrames portada generation failed tras 120.1s: ... timed out after 120 seconds
HyperFrames contraportada generation failed tras 120.1s: ... timed out after 120 seconds
Portada o contraportada fallaron tras reintento, reel sin marca (estructura Parte A)
```

Cada intento (incluido el reintento automático) tardó siempre ~120.1-120.3s
— nunca varió, nunca uno de los dos segmentos completó mientras el otro
fallaba. Contando todo el historial de logs disponible: **16 fallos vs. 2
éxitos**. El único éxito registrado (52.3s / 52.8s, mismo patrón de código
en paralelo) ocurrió a las 03:11 de hoy, bajo poca carga concurrente.

### Causa raíz

- El entorno (y producción, según comentarios ya existentes en el código)
  tiene **solo 2 vCPUs** (`nproc` = 2, confirmado en `backend` y `rqworker`).
- Hay **3 réplicas de rqworker** corriendo como procesos de SO
  independientes (`docker-compose.yml`), sin ningún límite de concurrencia
  *global* entre ellas.
- El equipo ya había diagnosticado y corregido este mismo patrón para
  `_animate_still_to_clip` (ffmpeg) en el commit `4d119b0` — pero ese fix
  solo acota la concurrencia **dentro de un job** (su propio thread pool a
  `os.cpu_count()`). El par portada/contraportada ya usa 2 threads = 2
  vCPUs, así que en aislamiento está "bien calibrado" (de hecho el
  éxito de 52s de las 03:11 lo confirma).
- El problema real es que **no hay límite entre workers**: cuando los 3
  procesos de rqworker generan contenido al mismo tiempo (exactamente el
  escenario de "generar un mes completo" que es cuando más se necesita),
  varios subprocesos CPU-bound (Chromium de HyperFrames, ffmpeg) terminan
  compitiendo por los mismos 2 núcleos reales. El timeout de 120s está
  calibrado contra el mejor caso sin contención (52s) y no deja margen
  bajo carga real — por eso falla de forma consistente y no ocasional.

### Por qué no vale la pena arreglarlo

- HyperFrames (portada+contraportada) es, medido en producción, uno de los
  tramos más lentos del pipeline de reel (hasta 240s perdidos por reel solo
  en los dos intentos fallidos, antes de caer al fallback).
- El fallback (reel sin marca) ya es el resultado real que reciben los
  clientes casi siempre (16/18 intentos recientes cayeron a este camino).
- Verificación visual: los 3 reels generados hoy sin portada/contraportada
  de marca (días 43, 50, 57) se ven bien — el fallback no es un problema
  cosmético que obligue a mantener el intento.
- Arreglarlo de raíz (semáforo global de concurrencia entre workers vía
  Redis, o subir vCPUs) es trabajo real para sostener una función cuyo
  resultado en la práctica ya no se usa casi nunca.

## Recomendación

**Eliminar HyperFrames del pipeline de reel** (portada/contraportada de
marca) en vez de arreglar el timeout/concurrencia:

- Quitar la llamada a `_wrap_with_branding()` / `_generate_branded_segment*`
  en `core/content_pipeline/generators/reel_generator.py` y dejar que el
  reel se arme siempre con el camino que hoy es el fallback (`clips` sin
  envolver).
- Esto también elimina el binario/proyecto Node de `hyperframes_reel/`
  como dependencia en runtime (y sus `node_modules`), simplificando la
  imagen de `rqworker`.
- Efecto esperado: reels ~2 intentos × 120s (hasta 240s) más rápidos por
  reel cuando hoy se está pagando ese tiempo casi siempre para terminar en
  el mismo resultado visual.

Pendiente de implementar — este documento es el registro de la
investigación y la decisión, no el cambio en sí.
