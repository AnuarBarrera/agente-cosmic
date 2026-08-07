# Sombras de contacto para el catálogo de ProductShowcaseGenerator — Diseño

## Contexto

El plan anterior (`docs/superpowers/specs/2026-08-06-showcase-character-catalog-design.md`,
commits `56de650..33c90a7`) amplió el catálogo de `ProductShowcaseGenerator` de 3 a 6
templates, agregando 3 templates de personaje 3D animado (Xbot, asset de Mixamo/Adobe).
Los 3 se construyeron completos, se verificaron con renders reales, y pasaron la revisión
final de rama (con un hallazgo Crítico corregido: `character-walk-reveal` cortaba al
personaje contra el borde de cámara salvo con `camera_motion=slow_orbit`).

Tras generar 6 renders de prueba con una foto de producto real (misma sesión, 2026-08-07) y
pedirle a Gemini un análisis de video, Anuar decidió: **los 3 templates de personaje quedan
descartados por completo** — se ven rígidos/genéricos ("los muñecos si out"), no son
vendibles. Diagnóstico de Gemini (compartido y confirmado como acertado): el pipeline de
renderizado funciona bien (determinista, sin regresión de rendimiento) — el problema es el
TIPO de contenido 3D. Los 3 templates procedurales originales (`confetti-fall`,
`frame-assembly`, `glass-shatter-reveal`) NO recibieron esta crítica y se quedan, con la
expectativa de que sombras de contacto los mejoren significativamente (hoy los objetos 3D
"flotan" sobre la foto porque no proyectan sombra).

Esta spec cubre dos cosas: (1) el retiro completo de los 3 templates de personaje, y (2) el
diseño de sombras de contacto para los 3 templates que quedan. Las otras 3 ideas que trajo
el análisis de Gemini (tipografía 3D/badges/partículas de venta, motion blur/easing de
post-procesado, parallax 2.5D con mapas de profundidad) quedan explícitamente fuera de esta
spec — documentadas en memoria de proyecto para retomar como brainstorms separados.

## Decisiones confirmadas (Anuar, este brainstorm)

- **Retiro completo**, no exclusión-pero-presente: se borran los 3 archivos de composición
  y el asset (`assets/characters/mascot.glb` + `LICENSES.md`). El catálogo Python vuelve a
  3 templates. El historial de git conserva todo el trabajo por si se retoma en el futuro
  con mejores assets — no hace falta dejar código muerto en el árbol actual para eso.
- La arquitectura de `camera_motion` (`sway_dolly`/`static_hold`/`slow_orbit`, Fase B de
  este mismo catálogo) **no se toca** — es independiente de los personajes y sigue
  aplicando íntegra a los 3 templates que quedan. Solo se elimina
  `_CAMERA_MOTION_OVERRIDES` (existía únicamente para forzar `slow_orbit` en
  `character-walk-reveal`, que deja de existir).
- **Sombra real de Three.js vía `THREE.ShadowMaterial`** (no una sombra "falsa" tipo
  mancha/gradiente): se evaluaron ambas, y la real fue la elegida porque el marco
  (`frame-assembly`) tiene bordes rectos donde una sombra de mentira se vería
  notoriamente falsa, y porque `ShadowMaterial` permite tener sombra real sin cambiar el
  material de la foto (que se queda `MeshBasicMaterial`, sin iluminar, para no alterar sus
  colores).
- Spike de validación de rendimiento **obligatorio y bloqueante** antes de construir las
  sombras en los 3 templates completos — mismo criterio ya usado dos veces en este
  catálogo (Fase B con `transmission`, catálogo de personajes con el GLB animado).

## Arquitectura de sombras

En cada uno de los 3 archivos que quedan (`confetti-fall.html`, `frame-assembly.html`,
`glass-shatter-reveal.html`):

- `renderer.shadowMap.enabled = true` y `renderer.shadowMap.type = THREE.PCFSoftShadowMap`
  (sombra suave, no dentada).
- **Solo `keyLight`** (la luz direccional más intensa, ya existente en los 3 templates)
  proyecta sombra (`keyLight.castShadow = true`) — un único mapa de sombra por frame, no
  uno por luz, para acotar el costo. `ambientLight`, `fillLight` y `rimLight` no proyectan
  sombra (siguen existiendo solo para iluminar, como hoy).
- El frustum de la cámara de sombra de `keyLight`
  (`keyLight.shadow.camera.left/right/top/bottom/near/far`) se ajusta al área real donde
  se mueven los objetos de cada template — no el default de Three.js (demasiado grande,
  da sombras borrosas/de baja resolución). Valores exactos por template, verificados con
  render real, no asumidos de antemano.
- Los objetos que hoy dan la identidad visual de cada template proyectan sombra
  (`castShadow = true`): el marco (`confetti-fall`, `frame-assembly`), las gotas de
  confeti, los fragmentos de vidrio. La foto NO proyecta sombra (no tiene sentido, es la
  superficie receptora).
- Se agrega un plano nuevo, del mismo tamaño aproximado que la foto, ubicado al mismo Z
  que la foto (o una fracción delante, para evitar z-fighting), con material
  `new THREE.ShadowMaterial({ opacity: <valor a ajustar visualmente, punto de partida ~0.4> })`
  y `receiveShadow = true`. Este plano es invisible salvo donde le cae sombra encima — no
  cambia el aspecto de la foto en absoluto.
- Sin módulo JS compartido entre los 3 archivos — mismo criterio de duplicación deliberada
  que ya usa este catálogo completo.

## Validación de rendimiento (spike, bloqueante)

Antes de tocar los 3 templates reales: una composición mínima con la configuración de
sombra de arriba (un objeto simple proyectando sombra real, un plano `ShadowMaterial`
recibiéndola) corrida **a través del pipeline real** (`ProductShowcaseGenerator._generate_showcase`
dentro de `rqworker`, no `hyperframes render` suelto por CLI — lección ya aprendida dos
veces en este catálogo) contra `_HYPERFRAMES_TIMEOUT_SECONDS = 120`, con el mismo margen
de 25% (90s) ya usado en el spike de personajes. Solo con veredicto GO se construyen las
sombras en los 3 templates completos.

## Retiro de los templates de personaje

- Borrar: `core/content_pipeline/hyperframes_reel/compositions/character-wave-hello.html`,
  `character-walk-reveal.html`, `character-victory-pose.html`.
- Borrar: `core/content_pipeline/hyperframes_reel/assets/characters/` completo
  (`mascot.glb` + `LICENSES.md`).
- En `core/content_pipeline/generators/product_showcase_generator.py`: `_SHOWCASE_TEMPLATES`,
  `_SHOWCASE_COMPOSITIONS`, `_SHOWCASE_POSTER_OFFSETS` vuelven a 3 entradas (las de
  Fase B). Se elimina `_CAMERA_MOTION_OVERRIDES`. `ShowcaseSelectionSchema.template`
  vuelve a un `Literal` de 3 valores. El prompt de selección que lee Gemini pierde las 3
  descripciones de templates de personaje.
- Tests: se eliminan/revierten los que cubrían específicamente los 3 templates de
  personaje y el override de `camera_motion` para `character-walk-reveal`; se mantienen
  (ajustados a 3 templates) los de integridad de catálogo y selección de 2 dimensiones.

## Contrato de aspect ratio y frustum (sin cambios, obligatorio para los 3)

Se mantiene sin cambios el contrato ya establecido (HALLAZGO 86/87, Fase B): cámara
`PerspectiveCamera(35, 1080/1920, 0.1, 100)`, dolly de `sway_dolly` de z=7 a z=5.5, ningún
`camera_motion` puede acercarse a menos de 5.45 unidades del origen. `PHOTO_MAX_WIDTH=1.8`/
`PHOTO_MAX_HEIGHT=3.0` sin recorte de foto. Las sombras no deben requerir mover la cámara
ni cambiar estos límites.

## Testing

Mismo criterio obligatorio que el resto de este catálogo:

- El spike de rendimiento corre primero, bloqueante.
- `npx hyperframes lint` (0 errores) en los 3 archivos modificados.
- Cada uno de los 3: render real con foto de prueba real + mínimo 3 frames extraídos e
  inspeccionados visualmente, confirmando que la sombra aparece, tiene una silueta
  reconocible del objeto que la proyecta, y no oscurece ni recorta la foto de forma que
  pierda legibilidad.
- Verificar los 3 `camera_motion` (no solo `sway_dolly`) en al menos 1 frame por
  combinación — lección de la revisión final del catálogo de personajes, donde no
  verificar `static_hold`/`slow_orbit` en cada template nuevo dejó pasar un defecto real.
- Suite completa de tests de Python sin regresiones.

## Fuera de alcance

- Tipografía 3D + insignias/badges + partículas de venta, motion blur/easing de
  post-procesado, y parallax 2.5D con mapas de profundidad — documentadas en
  memoria de proyecto (`project_showcase_catalog_roadmap.md`) para brainstorms futuros
  separados, no se tocan aquí.
- Cualquier trabajo futuro de personajes 3D con un asset distinto — descartado por ahora,
  sin plan de retomarlo.
