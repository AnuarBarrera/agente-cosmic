# Subtítulos sincronizados para Reels — Diseño

## Contexto

El pipeline de Reels de Agente Cosmic (ver `docs/superpowers/specs/2026-07-13-reels-pipeline-design.md`)
ya genera: 3 clips mudos con Veo, música con Lyria 3, narración en español con TTS
(`gemini-2.5-flash-tts`, devuelve PCM crudo 24kHz mono sin timestamps), y overlays
estáticos de hook (0-3s, arriba) y CTA (últimos 3s, abajo) renderizados con
Playwright+HTML y compuestos con ffmpeg.

Tras probar un reel real, Anuar pidió subtítulos sincronizados con la narración
completa — no solo el hook/CTA estáticos que ya existen. Esta sesión se investigó
la factibilidad (ver `.superpowers/sdd/reel-subtitles-report.md`) y se definieron las
decisiones de diseño con Anuar directamente. Este documento formaliza esas
decisiones en un spec implementable.

## Decisiones de diseño (ya validadas con Anuar)

- **Estilo visual**: subtítulos simples — texto blanco con contorno negro, una línea
  a la vez. NO el estilo "premium" (pastilla de color, rotación) usado en hook/CTA.
- **Agrupamiento**: líneas largas por frase completa (no palabra por palabra, no
  fragmentos cortos tipo karaoke).
- **Fuente del timing**: Google Cloud Speech-to-Text (`enable_word_time_offsets`)
  sobre el audio ya generado por TTS — NO Whisper local. Motivo explícito de Anuar:
  la instancia de producción es e2-standard-2 (2 vCPU/8GB) ya compartida entre
  gunicorn, 3 rqworkers y el ffmpeg de ensamblaje del reel; prioriza minimizar carga
  de cómputo local y delegar a APIs de Google Cloud, mismo patrón que Veo/Lyria/TTS.
- **Composición**: filtros `drawtext` de ffmpeg encadenados en el `filter_complex`
  existente — mismo mecanismo que ya usan hook/CTA (`enable='between(t,inicio,fin)'`),
  sin Playwright ni archivos `.ass`.
- **Posición y solapamiento**: subtítulos corren de forma continua durante TODA la
  narración, incluida la ventana del hook (0-3s) y la del CTA (últimos 3s) — sin
  ocultarse. Para evitar choque visual con el CTA, **el CTA se reposiciona al centro
  de la pantalla** (antes estaba abajo) — el hook se mantiene arriba, sin cambios.

**Fuera de alcance** (tratado como mejora de infraestructura aparte, ver memoria
`project_gcp_migration.md`): mover el ensamblaje ffmpeg completo a Cloud Run Jobs.

## Arquitectura

Nuevo módulo `core/content_pipeline/generators/subtitle_generator.py`, siguiendo la
misma separación de responsabilidades que ya existe (`reel_script_generator.py`
genera el guion vía Gemini; `reel_generator.py` orquesta generación pesada y
ensambla con ffmpeg):

```python
class SubtitleGenerator:
    def generate(self, narration_audio: bytes, narration_script: str) -> list[dict]:
        """Devuelve [{'text': str, 'start': float, 'end': float}, ...] o [] si falla."""
```

`reel_generator.py` la invoca en `ReelGenerator.generate()`, junto a los demás pasos
(música, narración, overlays), y pasa el resultado a `_assemble_reel`.

## Timing: Cloud Speech-to-Text + alineación

1. **Llamada a STT**: `google-cloud-speech`, `enable_word_time_offsets=True`,
   `language_code='es-ES'`, `encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16`,
   `sample_rate_hertz=24000` — mismo audio PCM crudo que ya produce el TTS, sin
   conversión de formato.
2. **División en frases**: `narration_script` (el texto exacto ya conocido, generado
   por `ReelScriptGenerator`) se divide en frases por puntuación de cierre
   (`.`, `!`, `?`, `¡...!`). Este texto —no la transcripción de STT— es el que se
   renderiza; STT solo aporta el timing.
3. **Alineación por orden posicional**: se asume que STT transcribe las palabras en
   el mismo orden que el guion (la narración es TTS leyendo exactamente ese texto).
   El inicio de una frase = `start_time` de su primera palabra en la lista de
   palabras de STT; el fin = `end_time` de su última palabra. Se avanza un cursor
   sobre la lista de palabras de STT según la cantidad de palabras de cada frase.
4. **Fallback si no calzan las cantidades**: si `len(palabras_STT) != len(palabras_guion)`
   (por ejemplo "48" transcrito distinto a "cuarenta y ocho", o un error de
   reconocimiento), se abandona la alineación por posición y se reparte el tiempo
   total del audio proporcionalmente entre las frases según su longitud en
   caracteres (mismo cálculo simple que la Ruta 2 del reporte de investigación,
   usado aquí solo como red de seguridad). Esto garantiza que siempre haya
   subtítulos razonables, nunca un crash ni una lista vacía por un desajuste menor.
5. **Duración del audio**: se calcula igual que ya hace el código existente en
   `_assemble_reel` para el narration PCM: `len(narration_audio) / (2 * 24000)`
   segundos (16-bit mono a 24kHz).

## Composición: `drawtext` encadenado

En `_assemble_reel`, se agrega un filtro `drawtext` por frase al `filter_complex`
existente (junto a los `overlay` de hook/CTA), cada uno con:

```
drawtext=fontfile=<ruta-poppins-bold>:text='<frase-escapada>':fontcolor=white:
fontsize=56:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-300:
enable='between(t,<inicio>,<fin>)'
```

- **Fuente**: `drawtext` necesita un archivo de fuente local (no funciona con
  `@import` de Google Fonts como Playwright). Se agrega `static/fonts/Poppins-Bold.ttf`
  al repo.
- **Posición**: `y=h-300` — mismo margen inferior que usaba el CTA antes de
  reposicionarse, para quedar en zona segura (no tapado por la UI nativa de
  Instagram/TikTok).
- **Escape de texto**: nueva función `_escape_drawtext(text: str) -> str` que escapa
  comillas simples, dos puntos, `%` y backslash según la sintaxis de `drawtext`.
- **Salto de línea**: si una frase excede ~30 caracteres, se inserta un `\n` manual
  en el punto medio más cercano a un espacio (sin medición de píxeles — corte simple
  por longitud de caracteres). `drawtext` no hace wrap automático, así que sin esto
  una frase larga se saldría del cuadro.

## Reposición del CTA

`core/content_pipeline/templates/content_pipeline/reel_cta.html`: cambiar
`.wrap { justify-content: flex-end; padding-bottom: 260px; }` a
`.wrap { justify-content: center; }` (sin padding-bottom) — el CTA queda centrado
verticalmente en los últimos 3s, liberando la franja inferior para los subtítulos
que siguen corriendo en esa ventana.

## Manejo de errores

Mismo patrón que Veo/Lyria en `reel_generator.py`: la llamada a Cloud STT se
reintenta una vez si falla; si el segundo intento también falla, `SubtitleGenerator.generate()`
devuelve `[]` y `_assemble_reel` genera el reel **sin subtítulos** (degradación
graceful, no aborta el pipeline completo) — igual que ya ocurre hoy si falla la
música o la narración.

## Dependencias nuevas

`google-cloud-speech` — agregar a `requirements.txt`. Reutiliza las credenciales de
GCP/Vertex AI ya configuradas en el proyecto, sin infraestructura de auth nueva.

## Testing

- `subtitle_generator.py`: tests con mock del cliente de Cloud STT — casos: STT
  devuelve palabras alineadas 1:1 con el guion (caso feliz), STT devuelve cantidad
  distinta de palabras (fallback proporcional), STT falla tras el reintento (retorna
  `[]`).
- `reel_generator.py` / `_assemble_reel`: tests que verifican la construcción exacta
  del `filter_complex` con los filtros `drawtext` agregados (mismo estilo que los
  tests existentes de `amix` — verificar los argumentos exactos pasados a
  `subprocess.run`), incluyendo el caso de texto con comillas/caracteres especiales
  para validar el escape.
- Sin llamadas reales a APIs en la suite (mocks siempre). Como ya es el patrón
  establecido en este pipeline, después de implementar se corre una prueba
  controlada contra la API real de Cloud STT (siguiendo el mismo procedimiento usado
  para verificar los bugs de Veo/Lyria/TTS) antes de dar el feature por terminado —
  los tests con mocks no habrían detectado ninguno de los 6 bugs reales encontrados
  previamente en este pipeline.
