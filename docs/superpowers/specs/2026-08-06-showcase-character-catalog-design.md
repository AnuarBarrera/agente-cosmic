# Catálogo de personajes 3D + movimiento de cámara independiente (ProductShowcaseGenerator) — Diseño

## Contexto

La Fase B (`docs/superpowers/specs/2026-08-06-product-showcase-template-catalog-design.md`,
commits `1d9c781`..`53db9f4`, ya en `main`) construyó y Anuar aprobó
visualmente un catálogo de 3 templates 3D procedurales para
`ProductShowcaseGenerator`: `confetti-fall`, `frame-assembly`,
`glass-shatter-reveal`. Cada uno tiene identidad de movimiento propia,
pero los 3 comparten literalmente las mismas 4 líneas de movimiento de
cámara/foto de fondo (confirmado leyendo el código de los 3 archivos):

```js
camera.position.z = 7 - (t / DURATION) * 1.5;      // dolly in constante
cardGroup.rotation.y = Math.sin(t * 0.5) * 0.35;   // vaivén horizontal
cardGroup.rotation.x = Math.sin(t * 0.35) * 0.08;  // vaivén vertical
cardGroup.position.y = Math.sin(t * 1.2) * 0.15;   // rebote sutil
```

Anuar, tras ver los 3 templates en producción, dio dos piezas de feedback
que no son bugs sino la siguiente iteración de calidad visual (mismo
espíritu de la Fase B: acercarse al impacto de breakoutclips.com):

1. Investigó fuentes reales de modelos 3D pre-hechos con licencias
   permisivas (CC0/MIT, uso comercial sin atribución) y propone que el
   "momento wow" real se parece más a un personaje/objeto haciendo algo
   sorprendente sobre la foto (ej. un personaje caminando) que a los
   efectos de partículas/transición que ya tenemos. Su propuesta clave,
   que resuelve la objeción de riesgo que yo planteé (mezclar assets
   genéricos con negocios arbitrarios ya causó el hallazgo P1 "objetos
   fuera de categoría" documentado en la revisión de reels de julio):
   **nosotros curamos qué efecto/animación hace cada personaje**, y
   Gemini solo elige entre esas opciones curadas — mismo patrón que ya
   usa `_choose_showcase_template`, sin infraestructura nueva (se
   descartó explícitamente la idea de un MCP: el patrón de
   `response_schema` + `Literal` + fallback ya resuelve esto).
2. El balanceo/dolly de cámara de arriba, aunque "bueno", no debe ser
   la norma fija de todo video — debe convertirse en **una opción más,
   independiente del efecto/personaje que ocurra**, y esto aplica
   retroactivamente a los 3 templates actuales (no solo a los nuevos).

Orden de roadmap confirmado por Anuar: primero esta curación de
personajes + arquitectura de cámara (este spec), después — proyecto
separado, futuro, fuera de este alcance — el ensamblaje de un reel
completo multi-escena usando esta misma base ("reel completo sin Veo").

## Decisiones de producto confirmadas (Anuar, este brainstorm)

- **Dos dimensiones de elección independientes**, no una sola: qué
  efecto/personaje ocurre (`template`) y cómo se mueve la cámara de
  fondo (`camera_motion`). Gemini elige ambas en la misma llamada.
- El retrofit de `camera_motion` aplica a **los 6 templates**: los 3
  actuales (sin tocar su efecto propio) más los 3 nuevos de personaje.
- **Un solo personaje/modelo 3D para esta primera curación**, con 3
  animaciones distintas ya incluidas en el archivo (saludo, caminata,
  celebración), cada una detrás de su propio template. Fuente: pack CC0
  de Quaternius (GLB listo, sin conversión, licencia de bloque sin
  ambigüedad) — se prefiere sobre Mixamo (requiere conversión FBX→GLB)
  y sobre packs "anime/chibi" específicos (licencia por-modelo, más
  riesgo) para esta primera ronda. Un estilo visual más "anime"
  específico queda como curación futura, una vez validada la
  arquitectura.
- Sin módulo JS compartido entre archivos — mismo criterio de
  duplicación deliberada que ya sigue este catálogo (Fase B) y el resto
  del codebase de compositions.
- Reel completo multi-escena: **fuera de alcance**, proyecto futuro
  separado.

## Los 3 templates de personaje

Los 3 usan el mismo GLB (un solo personaje, cargado con `GLTFLoader`) y
el mismo mecanismo de animación (`AnimationMixer.setTime(t)` — seek
determinista, nunca `mixer.update(delta)` con reloj real). Cada uno
reproduce un `AnimationClip` distinto del archivo y tiene su propia
coreografía de cuándo aparece el personaje respecto a la foto.

**`character-wave-hello`**: el personaje entra desde un costado, saluda
(clip de saludo), y se retira o se queda de pie mientras la foto ocupa el
centro del cuadro. Duración total 8s (igual que los 3 templates
actuales), saludo concentrado en los primeros ~3s.

**`character-walk-reveal`**: el personaje camina (clip de caminata) de
un extremo a otro del cuadro, pasando delante o detrás de la foto (a
definir en el plan según el resultado visual real — probar ambas
composiciones y quedarse con la que no oculte contenido importante de la
foto), y se detiene hacia la mitad del video, con la foto ya
completamente visible al final.

**`character-victory-pose`**: el personaje aparece junto a la foto y
hace una animación corta de celebración (clip de victoria/celebración),
pensado como el remate final tipo "reveal con aplauso" más que como
introducción.

Los 3 respetan el contrato de `photo_aspect` ya establecido (Fase B,
HALLAZGO 87): la foto nunca se recorta a la fuerza, cada template calcula
su propio `PHOTO_WIDTH`/`PHOTO_HEIGHT` bajo el frustum de su cámara.

## Movimiento de cámara — 3 opciones, independientes del template

Se extraen/agregan 3 funciones con el mismo nombre en los 6 archivos
(3 actuales + 3 nuevos), cada una recibe `(t, camera, cardGroup)`:

- **`applyCameraMotion_swayDolly`**: el comportamiento que ya existe hoy
  en los 3 templates actuales (dolly in + vaivén + rebote), ahora
  nombrado y seleccionable explícitamente en vez de fijo.
- **`applyCameraMotion_staticHold`**: cámara fija a una distancia
  cómoda (sin recortar la foto en el frustum más ajustado, mismo cálculo
  de HALLAZGO 86/87), sin ningún movimiento de `cardGroup` — para cuando
  el efecto/personaje ya aporta suficiente movimiento por sí mismo (ej.
  `character-walk-reveal`) y sumarle vaivén de cámara compite con la
  coreografía del personaje.
- **`applyCameraMotion_slowOrbit`**: la cámara describe un arco lento
  alrededor del grupo (`camera.position.x/z` en función de `sin`/`cos`
  de `t`, con `camera.lookAt(0, 0, 0)` constante), el grupo permanece
  estático. Debe validarse contra el mismo frustum mínimo que los otros
  dos para no recortar la foto en ningún punto del arco.

No se define una matriz de compatibilidad efecto×cámara en esta primera
versión — las 18 combinaciones (6 templates × 3 movimientos) quedan
todas disponibles para que Gemini elija. Si al probar aparece alguna
combinación visualmente mala, se cura como exclusión puntual en una
iteración futura (empírico, no especulativo).

## Mecanismo de selección

Se extiende el esquema ya existente en `product_showcase_generator.py`
(mismo archivo, mismo patrón que `_choose_reel_template` de
`reel_generator.py`):

```python
class ShowcaseSelectionSchema(BaseModel):
    template: Literal[
        'confetti-fall', 'frame-assembly', 'glass-shatter-reveal',
        'character-wave-hello', 'character-walk-reveal', 'character-victory-pose',
    ]
    camera_motion: Literal['sway_dolly', 'static_hold', 'slow_orbit']
```

Una sola llamada a Gemini (`thinking_config=ThinkingConfig(thinking_budget=0)`,
`response_schema=ShowcaseSelectionSchema`, mismo `track_external_api`/
`record_tokens`/fencing de input no confiable ya usado) devuelve ambos
valores. Fallback ante cualquier fallo: `random.choice` independiente
para cada dimensión (mismo criterio que hoy). `_choose_showcase_template`
se renombra/extiende para devolver una tupla `(template, camera_motion)`;
su único call site (`generate_reel`) se actualiza en consecuencia.

## Cambios de interfaz

- `_SHOWCASE_TEMPLATES`: de 3 a 6 valores (se agregan los 3 de
  personaje).
- `_SHOWCASE_COMPOSITIONS`: 3 entradas nuevas apuntando a los 3 archivos
  nuevos.
- `_SHOWCASE_POSTER_OFFSETS`: 3 entradas nuevas (offset de póster
  elegido por template, mismo criterio que Fase B — validar visualmente
  cuál segundo muestra mejor el personaje + la foto).
- `_generate_showcase(self, enhanced_photo_bytes, primary_color, secondary_color, composition_path, camera_motion)`:
  nuevo parámetro `camera_motion`, se pasa como variable de composición
  adicional a `hyperframes render` (mismo mecanismo ya usado para
  `photo_aspect`/colores — el plan debe leer el código actual para
  confirmar el mecanismo exacto de paso de variables por CLI antes de
  implementar).
- `generate_reel`: usa la tupla `(template, camera_motion)` devuelta por
  la selección para armar la llamada a `_generate_showcase`.

## Assets 3D — sourcing, hosting y licencia

- GLB del personaje: descargado de un pack CC0 de Quaternius (o
  equivalente de licencia de bloque igualmente permisiva — Kenney es la
  alternativa si el pack de Quaternius no tiene las 3 animaciones
  necesarias al inspeccionarlo). El plan debe: descargar el GLB,
  inspeccionar sus `AnimationClip`s reales (nombre exacto de cada clip)
  con una herramienta de inspección glTF, y mapear 3 clips concretos a
  saludo/caminata/celebración — si el pack elegido no tiene los 3, se
  prueba el siguiente candidato de la lista antes de escribir código.
- El GLB se auto-hospeda dentro del repo
  (`core/content_pipeline/hyperframes_reel/compositions/assets/characters/`),
  nunca enlazado a un CDN externo — el pipeline de render no debe
  depender de red externa.
- Se crea `LICENSES.md` junto al asset, documentando fuente, URL,
  licencia exacta y fecha de descarga — para poder auditar esto después
  sin tener que re-investigar.

## Validación técnica obligatoria (antes de construir los 3 templates completos)

Un GLB animado con esqueleto (`SkinnedMesh` + `AnimationMixer`) tiene un
costo de render distinto a la geometría procedural simple que ya
tenemos, y la Fase B ya encontró una regresión de rendimiento invisible
en CLI suelto pero real bajo el timeout de producción
(`MeshPhysicalMaterial.transmission`, corregido en `53db9f4`). Antes de
construir los 3 templates completos, se hace un spike de una sola
composición mínima (el GLB cargado, un clip reproduciéndose vía
`mixer.setTime(t)`, sin efecto/coreografía elaborada todavía) y se corre
el render **a través del pipeline real** (`ProductShowcaseGenerator._generate_showcase`
dentro del contenedor `rqworker`, no `hyperframes render` suelto por
CLI) para confirmar que el tiempo total se mantiene holgadamente bajo
`_HYPERFRAMES_TIMEOUT_SECONDS = 120`. Si no lo hace, se resuelve ese
problema (reducir poly count del asset, bakear frames, etc.) antes de
continuar — no se construyen los 3 templates sobre una base cuyo costo
de render no se validó primero.

## Contrato de aspect ratio (obligatorio para los 6 templates)

Se mantiene sin cambios el contrato ya establecido en Fase B/HALLAZGO
87: cada composición declara `photo_aspect` (number, default 1) y
calcula su propio `PHOTO_WIDTH`/`PHOTO_HEIGHT` bajo el frustum real de
su cámara (`semi_visible = d * tan(fov/2) * aspecto_pantalla`), sin
recorte forzado. Los 3 templates actuales ya lo cumplen; los 3 nuevos
deben cumplirlo desde el diseño.

## Testing

Mismo criterio obligatorio que Fase B, ampliado por el alcance de este
proyecto:

- `npx hyperframes lint` (0 errores) en los 6 archivos afectados
  (3 retrofit + 3 nuevos).
- El spike de validación técnica (sección anterior) corre primero,
  bloqueante.
- Cada uno de los 3 templates nuevos: render real con foto de prueba
  real + mínimo 3 frames extraídos e inspeccionados visualmente
  (zoom/crop con PIL si la geometría es difícil de ver a resolución
  completa — lección de Fase B) antes de darlo por terminado.
- Los 3 templates retrofit (`confetti-fall`, `frame-assembly`,
  `glass-shatter-reveal`): verificar que su comportamiento con
  `camera_motion=sway_dolly` es visualmente idéntico al actual (no debe
  cambiar nada para quien no elija otra opción), y revisar visualmente
  al menos un frame de cada uno con `static_hold` y con `slow_orbit`
  para confirmar que ninguna opción nueva recorta la foto.
- No se exige revisión visual exhaustiva de las 18 combinaciones
  posibles — se revisa cada template con su `camera_motion` por
  defecto (`sway_dolly`) a fondo, y una pasada más ligera (un frame,
  sin recorte de foto) de las otras 2 opciones de cámara por template.
- Tests unitarios: extender `test_product_showcase_generator.py` con
  la nueva dimensión `camera_motion` en `ShowcaseSelectionSchema`
  (selección feliz, fallback por error de API, fallback por valor
  inválido) y actualizar los call sites existentes de
  `_generate_showcase` para el nuevo parámetro.

## Fuera de alcance

- Reel completo multi-escena ("sin Veo") — proyecto futuro separado,
  confirmado por Anuar.
- Estilo visual "anime/chibi" específico — curación futura, una vez
  validada esta arquitectura con el pack CC0 de Quaternius.
- Matriz de compatibilidad efecto×cámara — se cura empíricamente más
  adelante si alguna combinación resulta visualmente mala, no se
  especula ahora.
- Retargeting de animaciones de Mixamo — descartado para esta ronda por
  el paso de conversión FBX→GLB que añade; puede reconsiderarse si el
  pack CC0 elegido no cubre bien las 3 animaciones necesarias.
