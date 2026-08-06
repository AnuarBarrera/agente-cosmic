# Pendientes — Agente Cosmic

Archivo maestro de todo lo diagnosticado y no implementado hasta el
2026-07-31. Consolida `hallazgosImagen.txt`, `geminiAnalisis.md`,
`migracionDeModelo.txt` y la investigación de pago-por-calendario. No
reemplaza esos archivos (quedan como fuente de detalle/evidencia), este
es el índice para decidir por dónde seguir.

Nada de esto está implementado salvo lo marcado ✅.

**Cierre de ciclo (2026-08-03)**: todas las secciones de este archivo
están ✅ RESUELTAS o MOVIDAS, salvo **IMG-07** (sección 3, punto 4 —
narración TTS que se corta, logging ya implementado, esperando el
próximo caso real para diagnosticar causa exacta — no bloquea nada).
El punto 5 (pago por calendario) se reclasificó como decisión de
modelo de negocio y se movió a `hallazgos.txt` (HALLAZGO 85). Próximo
uso de este archivo: reabrir con hallazgos nuevos cuando aparezcan.

---

## 0. Recién resuelto

- ✅ **`web_scraper.py` roto** (2026-07-31): línea 133 tenía un
  `IndentationError` sin commitear (`return result` con indentación
  incorrecta) que rompía el import del archivo completo. Arreglado y
  verificado (import limpio + 3/3 tests de `test_web_scraper.py`).

---

## 1. ✅ RESUELTO (2026-08-02) — Inventario de prompts pendientes de analizar con promptfoo

Ya se analizaron 2 de estos (`manual_extractor.py`, `web_scraper.py`,
ver sección 2). Faltan los siguientes — listado completo por archivo,
con el nombre del prompt/método, qué hace, y si recibe texto crudo del
usuario sin sandbox (la señal de riesgo de injection más directa).

### `core/brand_dna/` — extracción y moderación

| Archivo | Prompt / método | Qué hace | ¿Input de usuario directo sin sandbox? |
|---|---|---|---|
| `extractors/logo_analyzer.py:12` | `_VISION_PROMPT` | Analiza el logo subido (imagen) para extraer colores/estilo | No (input es imagen, no texto) |
| `moderation.py:11` | `_MODERATION_PROMPT` | Modera el input inicial del usuario (descripción de negocio) antes de procesar | Sí — es justo el moderador de texto crudo del usuario |
| `views.py:571` | `_regenerate_caption()` (prompt inline, sin constante) | Regenera un caption según feedback del cliente | **Sí, sin sandbox alguno** — `feedback` del usuario se concatena directo, sin bloque `=== DATOS EXTERNOS ===`. Peor que los 2 ya analizados. |
| `views.py:679` | `_reanalyze_brand_field()` (prompt inline, sin constante) | Corrige un campo de marca (descripción/audiencia/keywords) según feedback | **Sí, sin sandbox alguno** — mismo patrón que arriba, `feedback` sin delimitar. |

### `core/content_pipeline/generators/` — generación de contenido

| Archivo | Prompt / método | Qué hace | ¿Input de usuario directo sin sandbox? |
|---|---|---|---|
| `text_generator.py:38` | `_PROMPT` | Genera las 7 captions del calendario a partir del `BrandDNA` | Indirecto (usa campos ya procesados de BrandDNA, no input crudo) |
| `text_generator.py:89` | `_SAFETY_QC_PROMPT` | QC de seguridad de contenido sobre las captions generadas | No (evalúa output propio, no input de usuario) |
| `text_generator.py:107` | `_SAFETY_FIX_PROMPT` | Reescribe una caption que falló el QC de seguridad | No |
| `brand_consistency_qc.py:12` | `_AUDIT_PROMPT` | Audita consistencia de marca (usado en text_generator y reel_script_generator) | Indirecto |
| `brand_consistency_qc.py:35` | `_REWRITE_PROMPT` | Reescribe contenido que falló el audit de marca | Indirecto |
| `reel_script_generator.py:36` | `_PROMPT` | Genera el guion completo del reel (hook, narración, CTA, 6 escenas) — el prompt más grande y central, ligado a IMG-06, IMG-08, IMG-10 | Indirecto (usa BrandDNA ya procesado) |
| `image_generator.py:265` (`_analyze_brand_scene`) | `_FALLBACK_PROMPT` + `gemini_prompt` | Decide la escena de la imagen (STEP 2), ya tocado en el plan de fidelidad del 27-jul | Indirecto |
| `image_generator.py:368` (`_validate_background`) | prompt QC inline (5 criterios) | QC de fondo/composición — el mismo que le falta el criterio de contenido sensible (IMG-09) | No (evalúa imagen generada) |
| `image_generator.py:422` (`_validate_final_image`) | prompt QC inline | Segunda validación, sobre la imagen final ya compuesta | No |
| `image_generator.py:482` (`_generate_post_content`) | prompt inline | Genera contenido/copy para el post de imagen simple | Indirecto |
| `image_generator.py:553` (`_generate_carousel_slides_content`) | prompt inline | Genera contenido para cada slide del carrusel | Indirecto |
| `image_generator.py:655` (`_choose_template_for_image`) | prompt inline | Elige template visual para la imagen | Indirecto |
| `image_generator.py:31` | `_IMAGE_NEGATIVE_PROMPT` | Negative prompt de Imagen (no es prompt de texto a Gemini, es param de generación visual) | N/A |
| `reel_generator.py:446` (`_choose_reel_template`) | prompt inline | Elige template visual del reel (portada/contraportada) | Indirecto |
| `reel_generator.py:693` (`_validate_scene_still`) | prompt QC inline (mismo checklist de 5 criterios, duplicado a propósito) | QC de cada shot del reel — mismo hueco de IMG-09 aquí también | No |
| `reel_generator.py:359` | `_VEO_SAFE_CONSTRAINTS` (class attr) | Negative prompt de Veo | N/A |
| `reel_generator.py:97` | `_MUSIC_FALLBACK_PROMPT` | Prompt de música de fallback (Lyria) | No |
| `product_reference_generator.py:27` | `_SCENE_PROMPT_TEMPLATE` | Genera la escena nueva a partir de la foto de producto real | Indirecto (usa `business_name`, ya tocado el 27-jul con IMG-03) |
| `product_reference_generator.py:45` | `_VIDEO_PROMPT_TEMPLATE` | Anima la escena con Veo | Indirecto |
| `product_reference_generator.py:51` | `_QC_PROMPT` | Mismo checklist de 5 criterios duplicado por 3ra vez (image_generator, reel_generator, este) — mismo hueco de IMG-09 | No |

**Nota importante para el análisis con promptfoo**: el `_QC_PROMPT`/prompt
de 5 criterios (`has_text`, `is_abstract_3d`, `has_screen_content`,
`has_malformed_object`, `has_unrealistic_grounding`) está **duplicado a
propósito en 3 archivos** (`image_generator.py`, `reel_generator.py`,
`product_reference_generator.py` — mismo patrón de duplicación
deliberada que se usa en todo el proyecto). Si decides agregar el
criterio de contenido sensible de IMG-09, hay que replicarlo en los 3,
no solo en uno.

**Prioridad sugerida para el análisis** (mismo criterio que ya usaste:
injection primero, calidad después):
1. `views.py` — `_regenerate_caption`/`_reanalyze_brand_field` (sin
   sandbox alguno, peor que los 2 ya analizados).
2. `moderation.py` — es el moderador de entrada, si falla aquí falla
   antes que nada.
3. `reel_script_generator.py` — el prompt más grande/central, y donde
   viven IMG-06/IMG-08/IMG-10.
4. Los 3 QC de 5 criterios (`_validate_background`,
   `_validate_scene_still`, `_QC_PROMPT`) — para diseñar el fix de
   IMG-09 con el texto exacto de los 3, ya que hay que tocarlos juntos.
5. El resto (`text_generator.py`, `brand_consistency_qc.py`,
   `image_generator.py` restante) — menor riesgo, input ya procesado.

---

## 2. ✅ RESUELTO (2026-08-02) — Seguridad de prompts (`geminiAnalisis.md`, los 17 prompts, todos verificados contra código real)

**Implementado**: plan `docs/superpowers/plans/2026-08-01-prompt-sandbox-schema-hardening-plan.md`,
10 tareas, ejecutado con subagentes Claude/Haiku, cada una verificada por
Claude directamente (diff exacto contra el plan) antes de dar por buena.
629/629 tests de la suite completa (631 base − 2 tests obsoletos de
unwrap-markdown que ya no aplican, eliminados a propósito). Sin commitear
todavía — pendiente de que decidas cuándo subirlo. Cubre: los 9 archivos
con sandbox de variables de usuario + `response_schema`/`response_mime_type`
nativo en los 12 sitios de parsing frágil, el fix de `allow_redirects` en
`web_scraper.py`, y la eliminación de `business_name` del prompt de Veo en
`product_reference_generator.py` (fix IMG-11, ver sección 2.6). Detalle
completo de qué se hizo en cada archivo queda abajo (ya no es "pendiente",
es referencia histórica de lo implementado).

Los 2 primeros (`manual_extractor.py`, `web_scraper.py`) fueron
verificados por Claude directamente. Los 15 restantes se verificaron en
un pase adicional (2026-08-01) — cada hallazgo del documento externo se
contrastó con el código real, no se tomó al pie de la letra. El
documento en sí se autocorrigió una vez a mitad de análisis
(`logo_analyzer.py`) y sobrestimó la severidad de otro (`views.py:679`)
— ambos casos están marcados abajo.

### Los 2 ya conocidos

1. **Prompt injection vía `business_name`** — fuera del sandbox en
   `manual_extractor.py:33-35`. Severidad: Alta.
2. **Prompt injection vía `css_colors`** — fuera del sandbox en
   `web_scraper.py:28-31`. Severidad: Alta.
3. **`allow_redirects=False`** en `web_scraper.py:79,98` — rompe la
   mayoría de sitios reales que redirigen. Severidad: Alta.
4. **`brand_colors: []` pedido al LLM y luego ignorado** en
   `manual_extractor.py:19-21`. Severidad: Baja/Media.
5. **Parsing frágil regex + `json.loads`** — ver punto de abajo, es
   mucho más extendido de lo que se pensaba.
6. **"Sobrescritura ciega" sin bandera de confianza** — mitigado en la
   práctica (el camino real de producción no es el código que se
   analizó originalmente). Severidad: Media.

### Confirmados en la verificación de los 15 restantes

7. **`business_name` sin sandbox también en `moderation.py:11`**
   (`_MODERATION_PROMPT`) — mismo patrón, el moderador de entrada.
   Severidad: Alta.
8. **`post.caption`/`feedback` sin NINGÚN sandbox** en
   `views.py:571` (`_regenerate_caption`) — ya lo sabíamos, confirmado
   otra vez. Severidad: Alta.
9. **Variables sin sandbox también en**: `text_generator.py:107`
   (`_SAFETY_FIX_PROMPT`, `{caption}`), `brand_consistency_qc.py:12,35`
   (`{fields_block}`, `{text}`, `{reason}`), `image_generator.py:265`
   (`_analyze_brand_scene`: `{audience}`, `{kw_str}`, aparte de
   `caption` que ya se sabía es solo fallback — ver IMG-11),
   `image_generator.py:482,553` (`{caption}` en generación de contenido
   de post/carrusel). Total: **9 ubicaciones confirmadas sin sandbox**,
   no solo las 2 originales. Severidad: Media (la mayoría son variables
   indirectas de BrandDNA, no texto libre directo del usuario como en
   `views.py`).
10. **Parsing frágil regex + `json.loads`/`re.search` confirmado en
    12 ubicaciones, 8 archivos** (incluye `product_reference_generator.py`,
    que el documento nunca llegó a analizar pese a decir "17 de 17").
    Es el patrón más repetido de todo el análisis. Severidad: Media.
11. **Cero uso de `response_schema`/Pydantic nativo de Gemini en todo
    el proyecto** (verificado por grep) — la recomendación del
    documento no está adoptada en ningún lado. Si se decide hacer, es
    un cambio de arquitectura limpio que resolvería los 12 puntos de
    parsing frágil de una sola vez. Severidad: Media (oportunidad, no
    bug puntual).

### Correcciones importantes al documento externo

12. **`views.py:679` NO es un crash bug real** — el documento dice que
    `json.loads(raw)` sin try/except provoca un error 500. Verificado:
    el caller SÍ envuelve la llamada en `except ValueError`, y
    `json.JSONDecodeError` hereda de `ValueError` en Python — así que
    se atrapa, y el resultado es un 400 con mensaje técnico feo, no un
    500. Bajar prioridad. Severidad real: Baja (UX del mensaje de
    error, no confiabilidad).
13. **`text_generator.py:89` (`_SAFETY_QC_PROMPT`) no es tan
    reemplazable por regex como dice el documento** — sí evalúa juicio
    semántico real (`has_unverifiable_claim`, ej. "garantizamos tu
    recuperación" sin usar la palabra "garantizado"), que si justifica
    usar LLM. Solo la parte de detección de palabras exactas prohibidas
    podría bajarse a Python. Severidad: Baja/Media (ahorro parcial, no
    total).
14. **`reel_script_generator.py:36`**: el documento propone un
    "negative_prompt en Python en vez del prompt positivo de Gemini"
    como si fuera nuevo — **ya existe parcialmente** vía
    `_VEO_SAFE_CONSTRAINTS` (negative_prompt real de Veo/Imagen) y
    `_scrub_brand_leak()` (backstop determinístico, HALLAZGO 77 ya
    resuelto). El "efecto elefante rosa" que menciona (instrucciones
    negativas contaminan la generación) es una hipótesis plausible de
    comportamiento del modelo, no un bug verificable con evidencia de
    código. Severidad: Media, y la solución es pulir lo que ya existe,
    no construir desde cero.
15. ✅ **Gap de cobertura cerrado** — `product_reference_generator.py`
    ("Prompt 18 de 18") se analizó por separado el 2026-08-01 y ya se
    verificó contra el código real, ver sección 2.6 abajo.

---

## 2.6. ✅ RESUELTO (2026-08-02) — `product_reference_generator.py` (Prompt 18/18, verificado contra código real, 2026-08-01)

Los 3 hallazgos de esta sección (`business_name` sin necesidad en
`_VIDEO_PROMPT_TEMPLATE`, "efecto elefante rosa" sin resolver, QC sin
schema) — el primero y el tercero quedaron implementados en el plan de
la sección 2 (eliminación de `business_name` + `ProductQCSchema`). El
"efecto elefante rosa" (hipótesis sin evidencia de código, punto 17
abajo) sigue sin atacar — no era parte del alcance de sandbox/schema.

16. **`business_name` incluido en el prompt de Veo sin necesidad
    funcional** (`_VIDEO_PROMPT_TEMPLATE`, línea 46: "Cinematic slow
    push-in on this product photography scene for {business_name}.").
    CONFIRMADO y más grave de lo que sugiere el documento: no es solo
    una hipótesis genérica, es el MISMO mecanismo exacto de
    HALLAZGO 77 (ya resuelto, pero en `reel_script_generator.py`, no
    aquí) — el modelo incorporando el nombre del negocio a elementos
    visuales alucinados (ej. "Panadería Estrella" → estrellas en la
    escena). Además, `_animate_scene` recibe la escena YA COMPUESTA
    como `image=` (primer frame fijo) — Veo solo necesita animar
    cámara sobre una imagen ya dada, `business_name` no aporta ninguna
    dirección útil ahí, solo agrega riesgo sin beneficio. Candidato
    fuerte a quitar. Severidad: Media-Alta (mecanismo con precedente
    real confirmado en este mismo proyecto, aunque no reproducido
    todavía específicamente en este archivo).
17. **"Efecto elefante rosa" en `_SCENE_PROMPT_TEMPLATE`** (la
    instrucción "do NOT include any text, logos, brand marks..." podría
    hacer que el modelo dibuje pseudo-texto/garabatos por atención
    negativa) — PLAUSIBLE, mismo tipo de hipótesis sin evidencia de
    código verificable que la de `reel_script_generator.py` (punto 14).
    No hay ningún caso logueado todavía que muestre este patrón
    específico con la redacción actual (post-IMG-03). Severidad: Media,
    a monitorear, no a atacar a ciegas.
18. **Parsing regex del QC** (`_validate_scene`, línea 243) — ya
    contabilizado en las "12 ubicaciones" de la sección 2, sin
    información nueva.
19. **CORRECCIÓN — 2 de los "riesgos" del documento ya estaban
    resueltos**: el código propuesto por el análisis incluye un
    chequeo defensivo de `candidate.content.parts` antes de iterar —
    **esto ya está implementado en el archivo real** (líneas 152-154,
    parte del fix de IMG-01/IMG-07, commit `9e62f8f`, ya en producción).
    El documento parece haber analizado una versión desactualizada del
    archivo. También propone bajar `_VEO_POLL_TIMEOUT_SECONDS` de 300s
    (valor real actual) a 180s sin ninguna justificación — a diferencia
    del hallazgo de timeout de la sección 2.5 (ese sí es un mismatch
    real y medible contra RQ), aquí `generate_sample_task` usa
    `job_timeout=2400s`, muy por encima de 300s o 180s — no hay ningún
    problema de timeout que resolver en este archivo. No adoptar ese
    cambio sin motivo.

**Nota importante para cuando se decida implementar**: el documento
cierra diciendo "100% de las salidas usan response_schema" y "todos
los inputs están aislados" — eso describe el código PROPUESTO, no el
actual. Hoy sigue en 0% de adopción de `response_schema` en todo el
proyecto (confirmado por grep, ver punto 11).

---

## 2.5. ✅ RESUELTO (2026-08-01) — timeout de RQ menor al timeout interno de Veo (Alta, confiabilidad)

Encontrado como efecto colateral de la verificación de los 17 prompts,
sin relación directa a seguridad de prompts pero real y de alto
impacto — no estaba en ningún archivo de pendientes hasta hoy.

- `reel_generator.py`: `_VEO_POLL_TIMEOUT_SECONDS = 1800` (30 min) es
  el límite interno del polling loop que espera a que Veo termine un
  clip.
- `core/content_pipeline/tasks.py:273` (`_enqueue_post_images_then` o
  equivalente): el `job_timeout` que RQ le asigna a un post de formato
  reel es **`600` segundos (10 minutos) — tres veces menor** que el
  límite interno de Veo.
- Si Veo tarda más de 10 minutos (no es raro bajo carga), **RQ mata el
  worker de forma abrupta** antes de que el código pueda degradar
  limpiamente al fallback de imagen — mismo patrón exacto del
  incidente ya documentado en memoria (`project_rq_orphaned_job_2026_07_14`:
  job huérfano, `AnalysisJob` atascado en "processing" para siempre).
- Se agrava con `_HYPERFRAMES_TIMEOUT_SECONDS = 120` (portada +
  contraportada, con 1 reintento cada una) — hasta 480s del mismo
  presupuesto de 600s se pueden ir solo en HyperFrames, antes de tocar
  Veo/Imagen/ffmpeg.
- El pipeline de `generate_sample_task` (modo admin) **no tiene este
  riesgo** — su `job_timeout=2400` sí es mayor que 1800.

Severidad: Alta — bug de confiabilidad real y matemáticamente
demostrable (no hipotético), mismo patrón que un incidente que ya
ocurrió.

**Fix aplicado**: `tasks.py:273` (`_enqueue_post_images_then`) —
`job_timeout` de reels subido de 600s a 2700s (cubre 1800s de Veo +
480s de HyperFrames con margen para TTS/música/ffmpeg/uploads). Un
solo cambio de valor, sin tocar lógica de generación ni de fallback —
solo le da tiempo al fallback que YA existe en el código para correr
antes de que RQ mate el worker. Post simple (no-reel) queda igual en
300s, sin riesgo ahí. Test `test_enqueue_week_images_uses_longer_timeout_for_reel`
actualizado al valor nuevo. 631/631 tests de la suite completa
verificados por Claude tras el cambio.

---

## 3. Calidad de imagen/reel (`hallazgosImagen.txt`, 10 hallazgos + 1 propuesta)

Orden por prioridad según las propias notas del archivo:

1. ✅ **RESUELTO (2026-08-02) — IMG-09 — QC sin criterio de contenido
   sensible/desnudez**. Implementado: criterio nuevo
   `has_suggestive_or_exposed_content` en los 3 QC duplicados
   (`image_generator.py`, `reel_generator.py`,
   `product_reference_generator.py`) + reencuadre cinematográfico de
   `scene_prompts`/modo lifestyle (enfoca en sensación final del
   cliente + efectos de cámara, no en narrar la interacción/el
   servicio) en `reel_script_generator.py` e `image_generator.py`, con
   1 línea de resguardo específico para tratamientos de contacto
   físico. Plan `2026-08-02-image-reel-quality-fixes-round2-plan.md`.
   Sin commitear todavía.
2. ✅ **RESUELTO (2026-08-02) — IMG-05 — mensajes de rechazo
   específicos, prioriza marcas de agua**. `ProductReferenceGenerator`
   ahora devuelve la razón del rechazo (`generate_image`/`generate_reel`
   cambian de firma), traducida a mensaje en español vía
   `_describe_qc_failure` — prioriza el caso de marca de agua
   (`has_text` sin `has_screen_content`) sobre el mensaje genérico,
   con casos específicos también para captura de pantalla y contenido
   sensible.
3. ✅ **RESUELTO (2026-08-02) — IMG-08 — filtro de "nicho sensible"
   granular por proximidad**. `100%` solo cuenta si aparece cerca
   (~40 caracteres) de palabras de promesa de resultado; en vez de
   descartar el guion completo, se reescribe solo el campo
   (`hook_text`/`narration_script`) que disparó el filtro vía
   `rewrite_for_brand_consistency`.
4. **IMG-07 — Alta — narración TTS se corta** en 3 de 5 reels de una
   sesión. Logging ya implementado, falta el próximo caso real para
   confirmar causa exacta. Fuera de esta ronda (sin evidencia nueva).
5. ✅ **RESUELTO (2026-08-02) — IMG-10 — narración en registro
   formal/peninsular + bug de placeholder**. Instrucción de español
   latinoamericano neutro/tuteo agregada al prompt + backstop
   determinista (`_fix_marca_placeholder`, mismo patrón que
   `_scrub_brand_leak` de HALLAZGO 77) que reemplaza `[Marca]`/"Marca."
   por el nombre real si se cuela.
6. ✅ **RESUELTO (2026-08-02) — IMG-06 — productos donde el texto ES el
   producto** (globos, dulces de marca) — resuelto vía la propuesta de
   triage (punto 10): `product_identity_is_text=true` enruta a MEJORAR
   (foto real + mejora clásica, sin pedirle al modelo que omita el
   texto que define al producto).
7. ✅ **RESUELTO (2026-08-02) — IMG-04 — rechazo por
   `has_unrealistic_grounding`** en foto de persona completa modelando
   el producto — mismo mecanismo: `has_full_person_subject=true`
   enruta a MEJORAR, evitando el reto de composición/anclaje físico
   que el modelo no domina de forma confiable.
8. ✅ **RESUELTO (2026-08-02) — IMG-01 — mensaje diferenciado**. Ya no
   es genérico: cuando Gemini se niega a generar la escena, el mensaje
   es específico ("No se pudo generar la escena a partir de la foto —
   el modelo se negó a procesarla."), distinto del mensaje de rechazo
   de QC (`_describe_qc_failure`). Se resolvió como efecto colateral
   del cambio de contrato de IMG-05 (misma sesión). **Decisión de
   Anuar**: no agregar una pista especulativa de copyright sin
   evidencia confirmada — el mensaje actual ya no engaña al usuario,
   que era el problema real del hallazgo original.
9. ✅ **IMG-02, IMG-03 — corregidos** (logging de detalle del QC +
   conflicto de prompt de branding vs. QC de texto).
10. ✅ **RESUELTO (2026-08-02) — Propuesta de triage**. Nuevo paso
    `ProductReferenceGenerator._triage()` (1 llamada barata a Gemini,
    `TriageSchema` de 5 flags) antes de `_generate_scene`, clasifica en
    3 rutas: RECHAZAR (captura de pantalla o marca de agua agresiva —
    mensaje específico, cero gasto en generación), MEJORAR (producto
    depende del texto / persona completa / foto ya profesional — foto
    real + `enhance_photo_classic()` — recorte 1:1 + nitidez +
    autocontraste, cero IA generativa; en modo reel se anima con
    `ffmpeg` zoompan en vez de Veo), REGENERAR (comportamiento actual
    sin cambios). Interfaz externa de `generate_image`/`generate_reel`
    sin cambios. Plan:
    `docs/superpowers/plans/2026-08-02-product-reference-triage-plan.md`,
    3 tareas + revisión final de rama (Opus) que encontró y corrigió 1
    hallazgo Important real (la ruta MEJORAR+reel estiraba el producto
    de 1:1 a 9:16 por el filtro `zoompan` — fix con letterbox
    `pad`+`scale`, verificado empíricamente con ffmpeg real). 663/663
    tests de la suite completa. Sin commitear todavía.
11. ✅ **RESUELTO (2026-08-02) — IMG-11 — pilar "Producto" (día 1)
    reescrito**. `CONTENT_PILLARS[0]['angle']` en `text_generator.py`
    cambió de lenguaje literal/directo ("Presenta que vendes... de
    forma directa y atractiva") a enfoque en sensación/resultado
    ("...mostrando la sensación o resultado que el cliente experimenta
    al usarlo — no una descripción literal del producto o servicio en
    sí"), mismo espíritu que el pilar día 7 y la misma línea de
    "sensación final" del fix de IMG-09 (misma sesión). **Decisión de
    Anuar**: reescribir el pilar en sí (no solo reinterpretar en el
    guion del reel) — así también mejora el caption que ve el cliente
    final, no solo el insumo del reel. Cambio de 1 línea, sin tests que
    dependieran del texto literal, 18/18 tests de `text_generator.py`
    verificados. Sin commitear todavía.

---

## 4. ✅ RESUELTO (2026-08-02) — Migración de modelo Gemini 2.5 → 3.5

Ver `migracionDeModelo.txt` para el detalle de la investigación
original y `docs/superpowers/specs/2026-08-02-gemini-3.5-migration-design.md`
+ `docs/superpowers/plans/2026-08-02-gemini-3.5-migration-plan.md` para
el diseño/plan ejecutado. Resumen:

- `VERTEX_TEXT_MODEL` → `gemini-3.5-flash` (validado con llamadas
  reales), región nueva `GOOGLE_CLOUD_LOCATION_TEXT='global'` para
  todas las llamadas de texto (Veo/Imagen 3/Omni Flash se quedan en
  `us-central1`, no disponibles en `global`).
- `thinking_config=ThinkingConfig(thinking_budget=0)` aplicado a los
  11 call sites de clasificación/QC/extracción/elección de template
  (validado con llamada real: -46%/-53% tokens, mismo output
  correcto); generación creativa (captions, guion de reel) mantiene
  thinking por defecto.
- 4 tareas vía subagentes Haiku + revisión final (Opus). 689/689 tests.
  Commiteado y subido a origin (`e3ccd70`, 2026-08-02).

---

## 5. Pago por calendario individual — MOVIDO

Ya no se trackea aquí. Anuar lo reclasificó (2026-08-03) como una
decisión de **modelo de negocio**, no deuda técnica/hallazgo de
calidad — ver **HALLAZGO 85** en `hallazgos.txt` para el detalle y el
estado (pospuesto, sin decisión tomada).

---

## 6. ⚠️ PENDIENTE (bloqueante para producción, 2026-08-05) — Deploy de `hyperframes_reel` requiere `npm ci` manual en el host

Encontrado en la revisión final de rama del plan
`docs/superpowers/plans/2026-08-05-product-showcase-3d-pipeline-plan.md`
(`ProductShowcaseGenerator`, composiciones de `hyperframes_reel` —
catálogo de 3 templates: `confetti-fall.html`, `frame-assembly.html`,
`glass-shatter-reveal.html`).

- La Tarea 1 de ese plan agregó `three` (`0.181.2`) como dependencia
  de `core/content_pipeline/hyperframes_reel/package.json`, usada por
  la composición Three.js que renderiza `_generate_showcase`.
- `node_modules/` está en `.gitignore` — no se versiona.
  `Dockerfile.worker` corre `npm ci` dentro de la imagen (línea 35),
  así que la imagen sí trae `node_modules` con `three` instalado.
- **Pero** `docker-compose.yml` monta el repo completo con un bind
  mount `.:/app` en los servicios `backend` y `rqworker`. Ese mount
  tapa el `node_modules` que la imagen construyó con el `node_modules`
  del **host** (el directorio real en el filesystem de la VM/laptop).
  Si el host nunca corrió `npm install`/`npm ci` para esta dependencia
  nueva, el contenedor ve el `node_modules` viejo del host (sin
  `three`), no el de la imagen.
- Efecto en producción: un `git pull` + reinicio de contenedores (sin
  tocar el host) deja `three` sin instalar en el host. `_generate_showcase`
  (en `product_showcase_generator.py`) falla ambos intentos de forma
  silenciosa y retorna `None` — `generate_reel` termina devolviendo el
  mensaje genérico `'No se pudo generar el video. Vuelve a intentar.'`
  sin ningún error visible en logs que apunte a la causa real (módulo
  `three` no encontrado).
- **Esto no es nuevo de esta dependencia específica**: la Parte B de
  reels (HyperFrames) ya dependía de `gsap` de la misma forma desde
  antes de este plan — el mismo problema ya existía, simplemente nadie
  lo había documentado ni disparado en un deploy real hasta ahora que
  se agregó `three` y se hizo evidente en la revisión.

**Paso manual requerido** antes (o inmediatamente después) de cualquier
deploy que toque `core/content_pipeline/hyperframes_reel/package.json`:

```bash
cd core/content_pipeline/hyperframes_reel && npm ci
```

Correrlo directamente en el host de deploy (la VM), no dentro del
contenedor — el bind mount hace que el resultado quede en el
filesystem del host, que es justo lo que el contenedor termina viendo.

Sin decisión tomada todavía sobre automatizar esto (ej. hook de
deploy, Dockerfile separado sin bind mount de `node_modules`,
healthcheck que valide `three` instalado). Por ahora, checklist manual.

---

## Cómo usar este archivo

Cada vez que se cierre o se abra un hallazgo nuevo, actualizar aquí Y
en el archivo de detalle correspondiente. Este archivo no lleva el
detalle técnico completo de cada hallazgo — para eso están los
archivos fuente (`hallazgosImagen.txt`, `geminiAnalisis.md`,
`migracionDeModelo.txt`, los specs de `docs/superpowers/`).
