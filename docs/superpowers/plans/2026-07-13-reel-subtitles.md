# Subtítulos sincronizados para Reels — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar subtítulos sincronizados (texto blanco, contorno negro, una línea por frase completa) a los Reels de Agente Cosmic, usando Google Cloud Speech-to-Text para el timing y `drawtext` de ffmpeg para la composición — sin Playwright ni cómputo local pesado adicional.

**Architecture:** Nuevo módulo `SubtitleGenerator` (timing vía Cloud STT con fallback proporcional) se integra en `ReelGenerator.generate()`, que pasa la lista de frases con su ventana de tiempo a `_assemble_reel`, donde se agregan filtros `drawtext` encadenados al `filter_complex` ya existente (mismo mecanismo que hook/CTA). El CTA se reposiciona al centro de la pantalla para no chocar con los subtítulos, que corren durante toda la narración.

**Tech Stack:** `google-cloud-speech` (nuevo), ffmpeg `drawtext`, Python puro para alineación/fallback. Sin Playwright, sin Whisper local.

## Global Constraints

- Estilo de subtítulo: texto blanco, contorno negro, una línea por frase completa (NO palabra por palabra, NO estilo "premium" con pastilla).
- Timing: Google Cloud Speech-to-Text, `enable_word_time_offsets=True`, `language_code='es-ES'`, `encoding=LINEAR16`, `sample_rate_hertz=24000` — sobre el mismo PCM crudo que ya produce el TTS existente.
- División de frases: por puntuación de cierre (`.`, `!`, `?`) sobre `narration_script` — el texto exacto que se renderiza es el del guion, STT solo aporta timing.
- Alineación: posicional por cantidad de palabras. Si `len(palabras_STT) != len(palabras_guion)`, cae a reparto proporcional por longitud de caracteres sobre la duración total del audio (`len(audio) / (2 * 24000)` segundos).
- Reintento: 1 reintento a Cloud STT si falla (mismo patrón que Veo/Lyria). Si el segundo intento también falla, el reel se genera SIN subtítulos (`[]`), nunca aborta el pipeline.
- Composición: filtros `drawtext` encadenados en el `filter_complex` ya existente de `_assemble_reel`, con `enable='between(t,inicio,fin)'` por frase, `fontcolor=white:borderw=3:bordercolor=black`, posición `y=h-300`.
- Fuente: `static/fonts/Poppins-Bold.ttf` (archivo local nuevo, `drawtext` no soporta `@import` de Google Fonts).
- CTA: `reel_cta.html` cambia de `justify-content: flex-end; padding-bottom: 260px` a `justify-content: center` (sin padding-bottom).
- Nueva dependencia: `google-cloud-speech` en `requirements.txt` — reutiliza credenciales GCP/Vertex ya configuradas, sin infra de auth nueva.
- Testing: sin llamadas reales a APIs en la suite de pytest (todo mockeado). Verificación final contra la API real de Cloud STT en un task de prueba controlada aparte (Task 4).

---

### Task 1: `SubtitleGenerator` — timing desde Cloud STT con alineación y fallback

**Files:**
- Modify: `requirements.txt`
- Create: `core/content_pipeline/generators/subtitle_generator.py`
- Test: `core/content_pipeline/tests/test_subtitle_generator.py`

**Interfaces:**
- Produces: `class SubtitleGenerator: def generate(self, narration_audio: bytes, narration_script: str) -> list[dict]`, donde cada dict es `{'text': str, 'start': float, 'end': float}`, o `[]` si no hay subtítulos (guion vacío o STT falla tras reintento).
- Produces (funciones módulo, usadas también por sus propios tests): `_split_into_phrases(script: str) -> list[str]`.

- [ ] **Step 1: Agregar la dependencia**

En `requirements.txt`, agregar esta línea junto a las demás `google-cloud-*` (después de `google-cloud-storage>=2.18.0`):

```
google-cloud-speech>=2.31.0
```

- [ ] **Step 2: Reconstruir el contenedor backend para instalar la dependencia**

```bash
docker compose build backend rqworker
docker compose up -d --force-recreate --no-deps backend rqworker
docker compose exec -T backend python -c "from google.cloud import speech; print('OK')"
```

Expected: imprime `OK` sin error de import.

- [ ] **Step 3: Escribir el archivo de test completo**

Crear `core/content_pipeline/tests/test_subtitle_generator.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from core.content_pipeline.generators.subtitle_generator import (
    SubtitleGenerator, _split_into_phrases,
)


def _make_word(word, start, end):
    w = MagicMock()
    w.word = word
    w.start_time.total_seconds.return_value = start
    w.end_time.total_seconds.return_value = end
    return w


def _make_stt_response(words):
    alternative = MagicMock()
    alternative.words = words
    result = MagicMock()
    result.alternatives = [alternative]
    response = MagicMock()
    response.results = [result]
    return response


class TestSplitIntoPhrases:
    def test_splits_on_sentence_punctuation(self):
        result = _split_into_phrases('Tu negocio en linea. Contactanos hoy.')
        assert result == ['Tu negocio en linea.', 'Contactanos hoy.']

    def test_handles_exclamation_and_question_marks(self):
        result = _split_into_phrases('¡Increible oferta! ¿Que esperas?')
        assert result == ['¡Increible oferta!', '¿Que esperas?']

    def test_returns_whole_text_as_one_phrase_when_no_punctuation(self):
        result = _split_into_phrases('Tu negocio en linea')
        assert result == ['Tu negocio en linea']

    def test_returns_empty_list_for_empty_string(self):
        assert _split_into_phrases('') == []
        assert _split_into_phrases('   ') == []


class TestGenerateHappyPath:
    @patch('core.content_pipeline.generators.subtitle_generator.speech.SpeechClient')
    def test_returns_phrases_aligned_with_stt_timing(self, mock_speech_client):
        words = [
            _make_word('Tu', 0.0, 0.2),
            _make_word('negocio', 0.2, 0.7),
            _make_word('en', 0.7, 0.9),
            _make_word('linea.', 0.9, 1.5),
            _make_word('Contactanos', 1.6, 2.3),
            _make_word('hoy.', 2.3, 2.8),
        ]
        mock_client_instance = MagicMock()
        mock_client_instance.recognize.return_value = _make_stt_response(words)
        mock_speech_client.return_value = mock_client_instance

        gen = SubtitleGenerator()
        result = gen.generate(b'\x00\x01' * 100, 'Tu negocio en linea. Contactanos hoy.')

        assert result == [
            {'text': 'Tu negocio en linea.', 'start': 0.0, 'end': 1.5},
            {'text': 'Contactanos hoy.', 'start': 1.6, 'end': 2.8},
        ]

    @patch('core.content_pipeline.generators.subtitle_generator.speech.SpeechClient')
    def test_sends_correct_recognition_config(self, mock_speech_client):
        words = [_make_word('Hola.', 0.0, 0.5)]
        mock_client_instance = MagicMock()
        mock_client_instance.recognize.return_value = _make_stt_response(words)
        mock_speech_client.return_value = mock_client_instance

        gen = SubtitleGenerator()
        gen.generate(b'\x00\x01' * 100, 'Hola.')

        call_kwargs = mock_client_instance.recognize.call_args.kwargs
        config = call_kwargs['config']
        assert config.sample_rate_hertz == 24000
        assert config.language_code == 'es-ES'
        assert config.enable_word_time_offsets is True


class TestGenerateFallback:
    @patch('core.content_pipeline.generators.subtitle_generator.speech.SpeechClient')
    def test_falls_back_to_proportional_when_word_count_mismatches(self, mock_speech_client):
        # STT devuelve 3 palabras pero el guion tiene 6 (numeros mal transcritos,
        # error de reconocimiento, etc.) — debe usar reparto proporcional, no crashear.
        words = [
            _make_word('Tu', 0.0, 0.2),
            _make_word('negocio', 0.2, 0.7),
            _make_word('ya.', 0.7, 1.0),
        ]
        mock_client_instance = MagicMock()
        mock_client_instance.recognize.return_value = _make_stt_response(words)
        mock_speech_client.return_value = mock_client_instance

        gen = SubtitleGenerator()
        narration_audio = b'\x00\x01' * (4 * 24000)  # 4s de PCM 16-bit mono 24kHz
        script = 'Tu negocio en linea. Contactanos hoy.'
        result = gen.generate(narration_audio, script)

        phrase1, phrase2 = 'Tu negocio en linea.', 'Contactanos hoy.'
        total_chars = len(phrase1) + len(phrase2)
        expected_split = 4.0 * len(phrase1) / total_chars

        assert len(result) == 2
        assert result[0] == {'text': phrase1, 'start': 0.0, 'end': pytest.approx(expected_split)}
        assert result[1]['text'] == phrase2
        assert result[1]['start'] == pytest.approx(expected_split)
        assert result[1]['end'] == pytest.approx(4.0)


class TestGenerateDegradation:
    @patch('core.content_pipeline.generators.subtitle_generator.speech.SpeechClient')
    def test_returns_empty_list_when_stt_fails_after_retry(self, mock_speech_client):
        mock_client_instance = MagicMock()
        mock_client_instance.recognize.side_effect = Exception('quota exceeded')
        mock_speech_client.return_value = mock_client_instance

        gen = SubtitleGenerator()
        result = gen.generate(b'\x00\x01' * 100, 'Tu negocio en linea.')

        assert result == []
        assert mock_client_instance.recognize.call_count == 2

    @patch('core.content_pipeline.generators.subtitle_generator.speech.SpeechClient')
    def test_retries_once_and_succeeds_on_second_attempt(self, mock_speech_client):
        words = [_make_word('Hola.', 0.0, 0.5)]
        mock_client_instance = MagicMock()
        mock_client_instance.recognize.side_effect = [
            Exception('timeout'), _make_stt_response(words),
        ]
        mock_speech_client.return_value = mock_client_instance

        gen = SubtitleGenerator()
        result = gen.generate(b'\x00\x01' * 100, 'Hola.')

        assert result == [{'text': 'Hola.', 'start': 0.0, 'end': 0.5}]
        assert mock_client_instance.recognize.call_count == 2


class TestGenerateEdgeCases:
    @patch('core.content_pipeline.generators.subtitle_generator.speech.SpeechClient')
    def test_returns_empty_list_when_script_is_empty(self, mock_speech_client):
        gen = SubtitleGenerator()
        result = gen.generate(b'', '')
        assert result == []
        mock_speech_client.assert_not_called()
```

- [ ] **Step 4: Correr los tests y confirmar que fallan (el módulo no existe todavía)**

```bash
docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_subtitle_generator.py -v
```

Expected: `ModuleNotFoundError: No module named 'core.content_pipeline.generators.subtitle_generator'`

- [ ] **Step 5: Implementar `subtitle_generator.py`**

Crear `core/content_pipeline/generators/subtitle_generator.py`:

```python
import logging
import re
from google.cloud import speech
from core.shared.metrics_utils import track_external_api

logger = logging.getLogger(__name__)

_PCM_SAMPLE_RATE = 24000
_PCM_BYTES_PER_SAMPLE = 2  # 16-bit mono


def _split_into_phrases(script: str) -> list[str]:
    stripped = script.strip()
    if not stripped:
        return []
    raw = re.split(r'(?<=[.!?])\s+', stripped)
    return [p.strip() for p in raw if p.strip()]


def _call_stt_attempt(narration_audio: bytes) -> list[dict] | None:
    try:
        client = speech.SpeechClient()
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=_PCM_SAMPLE_RATE,
            language_code='es-ES',
            enable_word_time_offsets=True,
        )
        audio = speech.RecognitionAudio(content=narration_audio)
        with track_external_api('speech_to_text', operation='word_timestamps'):
            response = client.recognize(config=config, audio=audio)
        words = []
        for result in response.results:
            for word_info in result.alternatives[0].words:
                words.append({
                    'word': word_info.word,
                    'start': word_info.start_time.total_seconds(),
                    'end': word_info.end_time.total_seconds(),
                })
        return words
    except Exception as e:
        logger.warning(f"Cloud Speech-to-Text failed: {e}")
        return None


def _call_stt(narration_audio: bytes) -> list[dict] | None:
    words = _call_stt_attempt(narration_audio)
    if words is None:
        words = _call_stt_attempt(narration_audio)
    return words


def _align_phrases_with_stt(phrases: list[str], stt_words: list[dict]) -> list[dict] | None:
    phrase_word_counts = [len(p.split()) for p in phrases]
    if sum(phrase_word_counts) != len(stt_words):
        return None
    result = []
    cursor = 0
    for phrase, count in zip(phrases, phrase_word_counts):
        words_for_phrase = stt_words[cursor:cursor + count]
        result.append({
            'text': phrase,
            'start': words_for_phrase[0]['start'],
            'end': words_for_phrase[-1]['end'],
        })
        cursor += count
    return result


def _proportional_fallback(phrases: list[str], total_duration: float) -> list[dict]:
    total_chars = sum(len(p) for p in phrases) or 1
    result = []
    cursor = 0.0
    for phrase in phrases:
        share = len(phrase) / total_chars
        duration = total_duration * share
        result.append({'text': phrase, 'start': cursor, 'end': cursor + duration})
        cursor += duration
    return result


class SubtitleGenerator:
    def generate(self, narration_audio: bytes, narration_script: str) -> list[dict]:
        phrases = _split_into_phrases(narration_script)
        if not phrases:
            return []

        stt_words = _call_stt(narration_audio)
        if stt_words is None:
            return []

        aligned = _align_phrases_with_stt(phrases, stt_words)
        if aligned is not None:
            return aligned

        total_duration = len(narration_audio) / (_PCM_BYTES_PER_SAMPLE * _PCM_SAMPLE_RATE)
        return _proportional_fallback(phrases, total_duration)
```

- [ ] **Step 6: Correr los tests y confirmar que pasan**

```bash
docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_subtitle_generator.py -v
```

Expected: todos los tests PASSED.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt core/content_pipeline/generators/subtitle_generator.py core/content_pipeline/tests/test_subtitle_generator.py
git commit -m "feat(reels): SubtitleGenerator — timing de subtitulos via Cloud Speech-to-Text"
```

---

### Task 2: Reposicionar el CTA al centro de la pantalla

**Files:**
- Modify: `core/content_pipeline/templates/content_pipeline/reel_cta.html`

**Interfaces:**
- Consumes: nada nuevo — mismo template ya usado por `ReelGenerator._render_text_overlay(..., style='cta', ...)`.

- [ ] **Step 1: Cambiar el CSS de posicionamiento**

En `core/content_pipeline/templates/content_pipeline/reel_cta.html`, reemplazar:

```html
  .wrap {
    width: 100%; height: 100%; display: flex; flex-direction: column;
    align-items: center; justify-content: flex-end; padding-bottom: 260px;
  }
```

por:

```html
  .wrap {
    width: 100%; height: 100%; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
  }
```

- [ ] **Step 2: Generar un PNG de verificación visual**

```bash
docker compose exec -T backend python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'saas_chatbot.settings')
django.setup()
from core.content_pipeline.generators.reel_generator import ReelGenerator
gen = ReelGenerator(bucket_name='verify')
png = gen._render_text_overlay('', '', 'cta', ['#2b5fd9'], cta_text='Contáctanos hoy')
with open('/tmp/cta_verify.png', 'wb') as f:
    f.write(png)
print('OK')
"
docker cp agente-cosmic-backend-1:/tmp/cta_verify.png ./cta_verify.png
```

**Nota para el controlador (Claude, no delegar):** ver `./cta_verify.png` con la
herramienta Read antes de aprobar este task — confirmar visualmente que el CTA
quedó centrado verticalmente en el cuadro de 1080x1920, no pegado abajo. Borrar el
PNG del repo después de revisarlo (`rm cta_verify.png`) — es solo un artefacto de
verificación, no debe quedar commiteado.

- [ ] **Step 3: Commit**

```bash
git add core/content_pipeline/templates/content_pipeline/reel_cta.html
git commit -m "fix(reels): reposicionar CTA al centro para no chocar con subtitulos"
```

---

### Task 3: Integrar subtítulos en `_assemble_reel` y `generate()` (drawtext + fuente)

**Files:**
- Create (asset binario): `static/fonts/Poppins-Bold.ttf`
- Modify: `core/content_pipeline/generators/reel_generator.py`
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Consumes: `SubtitleGenerator` de Task 1 (`core.content_pipeline.generators.subtitle_generator.SubtitleGenerator`, método `generate(narration_audio: bytes, narration_script: str) -> list[dict]`).
- Modifies: `ReelGenerator._assemble_reel(self, clips, music, narration, hook_png, cta_png, subtitles: list[dict] | None = None) -> bytes` — agrega parámetro `subtitles` con default `None` (compatibilidad con las llamadas existentes).
- Modifies: `ReelGenerator.generate(self, script, colors, filename_prefix) -> tuple[str, str]` — ahora también genera y pasa `subtitles` a `_assemble_reel`.

- [ ] **Step 1: Descargar la fuente Poppins Bold**

```bash
mkdir -p static/fonts
curl -L -o static/fonts/Poppins-Bold.ttf https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf
file static/fonts/Poppins-Bold.ttf
ls -la static/fonts/Poppins-Bold.ttf
```

Expected: `file` reporta algo como "TrueType Font data" y el tamaño del archivo es
mayor a 100 KB (un HTML de error de descarga fallida sería mucho más chico y
`file` lo reportaría como texto, no como fuente).

- [ ] **Step 2: Agregar los tests de las funciones auxiliares (`_escape_drawtext`, `_wrap_subtitle_text`)**

En `core/content_pipeline/tests/test_reel_generator.py`, agregar (después de los
imports existentes, antes de `class TestRenderTextOverlay:`):

```python
from core.content_pipeline.generators.reel_generator import (
    _escape_drawtext, _wrap_subtitle_text,
)


class TestEscapeDrawtext:
    def test_escapes_colon(self):
        assert _escape_drawtext('Hola: bienvenido') == 'Hola\\: bienvenido'

    def test_escapes_single_quote(self):
        assert _escape_drawtext("Tu 'mejor' opcion") == "Tu \\'mejor\\' opcion"

    def test_escapes_percent(self):
        assert _escape_drawtext('50% de descuento') == '50\\% de descuento'

    def test_escapes_backslash_first(self):
        assert _escape_drawtext('a\\b') == 'a\\\\b'


class TestWrapSubtitleText:
    def test_returns_unchanged_when_short(self):
        assert _wrap_subtitle_text('Hola mundo') == 'Hola mundo'

    def test_wraps_long_text_into_two_lines(self):
        text = 'Tu negocio en linea en menos de 48 horas'
        result = _wrap_subtitle_text(text, max_chars=20)
        assert result == 'Tu negocio en linea\nen menos de 48 horas'
```

- [ ] **Step 3: Agregar los tests de `_assemble_reel` con subtítulos**

En la misma archivo, dentro de `class TestAssembleReel:` (después del método
`test_works_without_music_or_narration`), agregar:

```python
    def test_adds_drawtext_filters_for_subtitles(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        subtitles = [
            {'text': 'Tu negocio en linea.', 'start': 0.0, 'end': 2.5},
            {'text': 'Contactanos hoy.', 'start': 2.5, 'end': 5.0},
        ]
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=fake_run) as mock_run:
            result = gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                hook_png=b'hook-png-bytes', cta_png=b'cta-png-bytes',
                subtitles=subtitles,
            )
        assert result == fake_output
        overlay_cmd = mock_run.call_args_list[1].args[0]
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert 'drawtext=fontfile=' in filter_complex
        assert "text='Tu negocio en linea.'" in filter_complex
        assert "text='Contactanos hoy.'" in filter_complex
        assert "enable='between(t,0.0,2.5)'" in filter_complex
        assert "enable='between(t,2.5,5.0)'" in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[sub1]'

    def test_omits_drawtext_filters_when_no_subtitles(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=fake_run) as mock_run:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                hook_png=b'hook-png-bytes', cta_png=b'cta-png-bytes',
            )
        overlay_cmd = mock_run.call_args_list[1].args[0]
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert 'drawtext' not in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[v2]'
```

- [ ] **Step 4: Actualizar `TestGenerate` para mockear `SubtitleGenerator` y verificar la integración**

En la misma archivo, reemplazar el método `test_returns_video_and_poster_urls_on_success`
completo (dentro de `class TestGenerate:`) por:

```python
    def test_returns_video_and_poster_urls_on_success(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'c1', b'c2', b'c3']), \
             patch.object(gen, '_generate_music', return_value=b'music'), \
             patch.object(gen, '_generate_narration', return_value=b'narration'), \
             patch('core.content_pipeline.generators.reel_generator.SubtitleGenerator') as mock_sub_gen, \
             patch.object(gen, '_render_text_overlay', return_value=b'overlay-png'), \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4') as mock_assemble, \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png'), \
             patch.object(gen, '_upload_video_to_storage', return_value='https://storage.test/reel.mp4') as mock_up_video, \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/poster.png') as mock_up_poster:
            mock_sub_gen.return_value.generate.return_value = [{'text': 'Hola.', 'start': 0.0, 'end': 1.0}]
            video_url, poster_url = gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')

        assert video_url == 'https://storage.test/reel.mp4'
        assert poster_url == 'https://storage.test/poster.png'
        mock_up_video.assert_called_once_with(b'final-mp4', 'job1-day1')
        mock_up_poster.assert_called_once_with(b'poster-png', 'job1-day1-poster')
        mock_assemble.assert_called_once_with(
            [b'c1', b'c2', b'c3'], b'music', b'narration', b'overlay-png', b'overlay-png',
            [{'text': 'Hola.', 'start': 0.0, 'end': 1.0}],
        )
```

Y agregar este nuevo test después de él, todavía dentro de `class TestGenerate:`:

```python
    def test_skips_subtitle_generation_when_narration_fails(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'c1', b'c2', b'c3']), \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch('core.content_pipeline.generators.reel_generator.SubtitleGenerator') as mock_sub_gen, \
             patch.object(gen, '_render_text_overlay', return_value=b'overlay-png'), \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4') as mock_assemble, \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png'), \
             patch.object(gen, '_upload_video_to_storage', return_value='url1'), \
             patch.object(gen, '_upload_to_storage', return_value='url2'):
            gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')

        mock_sub_gen.return_value.generate.assert_not_called()
        assembled_args = mock_assemble.call_args.args
        assert assembled_args[-1] == []
```

- [ ] **Step 5: Correr los tests y confirmar que fallan**

```bash
docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v
```

Expected: fallan los tests nuevos (`ImportError` por `_escape_drawtext`/`_wrap_subtitle_text`
no definidos, y `AssertionError` en los de `_assemble_reel`/`generate` por la firma/comportamiento
todavía sin cambiar).

- [ ] **Step 6: Implementar los cambios en `reel_generator.py`**

Agregar el import de `SubtitleGenerator` junto a los demás imports, al principio del
archivo (después de `from core.shared.rate_limiter import call_with_429_retry`):

```python
from core.content_pipeline.generators.subtitle_generator import SubtitleGenerator
```

Agregar estas constantes y funciones módulo-nivel, después de `_TEMPLATE_MAP` y
antes de `def _vertex_client():`:

```python
_SUBTITLE_FONT_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'static', 'fonts', 'Poppins-Bold.ttf',
))
_SUBTITLE_FONTSIZE = 56
_SUBTITLE_Y = 'h-300'


def _escape_drawtext(text: str) -> str:
    text = text.replace('\\', '\\\\')
    text = text.replace(':', '\\:')
    text = text.replace("'", "\\'")
    text = text.replace('%', '\\%')
    return text


def _wrap_subtitle_text(text: str, max_chars: int = 30) -> str:
    if len(text) <= max_chars:
        return text
    words = text.split()
    lines = []
    current = ''
    for word in words:
        candidate = f'{current} {word}'.strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return '\n'.join(lines)
```

Reemplazar la firma de `_assemble_reel` (actualmente
`def _assemble_reel(self, clips: list[bytes], music: bytes | None, narration: bytes | None, hook_png: bytes, cta_png: bytes) -> bytes:`)
por:

```python
    def _assemble_reel(self, clips: list[bytes], music: bytes | None, narration: bytes | None,
                        hook_png: bytes, cta_png: bytes, subtitles: list[dict] | None = None) -> bytes:
```

Dentro de `_assemble_reel`, reemplazar el bloque que construye `overlay_path`
(desde `duration = len(clips) * 8` hasta el `subprocess.run(...)` de ese paso) por:

```python
            duration = len(clips) * 8
            cta_start = max(0, duration - 3)
            overlay_path = os.path.join(tmp, 'overlay.mp4')
            filter_parts = [
                "[0:v][1:v]overlay=0:0:enable='between(t,0,3)'[v1]",
                f"[v1][2:v]overlay=0:0:enable='between(t,{cta_start},{duration})'[v2]",
            ]
            last_label = 'v2'
            for i, sub in enumerate(subtitles or []):
                next_label = f'sub{i}'
                text = _escape_drawtext(_wrap_subtitle_text(sub['text']))
                filter_parts.append(
                    f"[{last_label}]drawtext=fontfile={_SUBTITLE_FONT_PATH}:text='{text}':"
                    f"fontcolor=white:fontsize={_SUBTITLE_FONTSIZE}:borderw=3:bordercolor=black:"
                    f"x=(w-text_w)/2:y={_SUBTITLE_Y}:"
                    f"enable='between(t,{sub['start']},{sub['end']})'[{next_label}]"
                )
                last_label = next_label
            filter_complex = ';'.join(filter_parts)
            subprocess.run(
                ['ffmpeg', '-y', '-i', concat_path, '-i', hook_path, '-i', cta_path,
                 '-filter_complex', filter_complex,
                 '-map', f'[{last_label}]', '-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                 overlay_path],
                check=True, capture_output=True,
            )
```

En `generate()`, reemplazar:

```python
            music = self._generate_music(script['music_mood'])
            narration = self._generate_narration(script['narration_script'])
            hook_png = self._render_text_overlay(script['hook_text'], script['highlight_word'], 'hook', colors)
            cta_png = self._render_text_overlay('', '', 'cta', colors, cta_text=script['tag_cta'])

            final_video = self._assemble_reel(clips, music, narration, hook_png, cta_png)
```

por:

```python
            music = self._generate_music(script['music_mood'])
            narration = self._generate_narration(script['narration_script'])
            subtitles = []
            if narration is not None:
                subtitles = SubtitleGenerator().generate(narration, script['narration_script'])
            hook_png = self._render_text_overlay(script['hook_text'], script['highlight_word'], 'hook', colors)
            cta_png = self._render_text_overlay('', '', 'cta', colors, cta_text=script['tag_cta'])

            final_video = self._assemble_reel(clips, music, narration, hook_png, cta_png, subtitles)
```

- [ ] **Step 7: Correr los tests y confirmar que pasan**

```bash
docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v
```

Expected: todos los tests PASSED (los 17 existentes + los nuevos de esta task).

- [ ] **Step 8: Commit**

```bash
git add static/fonts/Poppins-Bold.ttf core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): componer subtitulos con drawtext encadenado en el ensamblaje ffmpeg"
```

---

### Task 4: Prueba controlada contra la API real de Cloud Speech-to-Text

**Files:** ninguno (task de verificación, sin cambios de código).

**Interfaces:** ninguna nueva — ejercita `SubtitleGenerator.generate()` (Task 1) y
`ReelGenerator._assemble_reel(..., subtitles=...)` (Task 3) juntos, contra la API
real de Cloud STT (no mockeada), siguiendo el mismo patrón de verificación ya usado
en este pipeline para Veo/Lyria/TTS — los mocks no detectan bugs reales de
integración con APIs externas (ver los 6 bugs reales encontrados previamente en
este mismo pipeline, ninguno detectable con tests mockeados).

- [ ] **Step 1: Escribir y correr el script de prueba controlada**

Este script genera narración TTS real (necesaria para que Cloud STT tenga audio
real que alinear) pero usa clips de video SINTÉTICOS vía ffmpeg `lavfi` (no paga
Veo) — el objetivo es verificar la integración STT + drawtext, no repetir la
verificación de Veo ya hecha.

```bash
docker compose exec -T backend python -c "
import os, subprocess, tempfile
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'saas_chatbot.settings')
import django
django.setup()

from django.conf import settings
from core.content_pipeline.generators.reel_generator import ReelGenerator
from core.content_pipeline.generators.subtitle_generator import SubtitleGenerator

gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

narration_script = (
    'Transforma tu negocio con presencia digital profesional. '
    'Contactanos hoy y descubre como podemos ayudarte a crecer en linea.'
)

narration = gen._generate_narration(narration_script)
assert narration is not None, 'TTS fallo, no se puede probar STT sin audio real'

subtitles = SubtitleGenerator().generate(narration, narration_script)
print('SUBTITLES:', subtitles)
assert subtitles, 'SubtitleGenerator devolvio lista vacia con audio real de TTS'

with tempfile.TemporaryDirectory() as tmp:
    clips = []
    for i, color in enumerate(['red', 'green', 'blue']):
        path = os.path.join(tmp, f'clip{i}.mp4')
        subprocess.run(
            ['ffmpeg', '-y', '-f', 'lavfi', '-i', f'color=c={color}:s=1080x1920:d=8',
             '-c:v', 'libx264', '-pix_fmt', 'yuv420p', path],
            check=True, capture_output=True,
        )
        with open(path, 'rb') as f:
            clips.append(f.read())

hook_png = gen._render_text_overlay('Crece tu negocio hoy', 'hoy', 'hook', ['#2b5fd9'])
cta_png = gen._render_text_overlay('', '', 'cta', ['#2b5fd9'], cta_text='Contáctanos ya')

final_video = gen._assemble_reel(clips, None, narration, hook_png, cta_png, subtitles)
with open('/tmp/reel_subtitles_test.mp4', 'wb') as f:
    f.write(final_video)
print('OK - video en /tmp/reel_subtitles_test.mp4')
"
docker cp agente-cosmic-backend-1:/tmp/reel_subtitles_test.mp4 ./reel_subtitles_test.mp4
```

Expected: imprime la lista real de `SUBTITLES` con texto y tiempos (revisar que los
tiempos sean crecientes y razonables, no todos en 0.0), y termina con
`OK - video en /tmp/reel_subtitles_test.mp4`.

- [ ] **Step 2: Extraer frames en cada ventana de subtítulo para inspección visual**

Usar los tiempos impresos en `SUBTITLES` del step anterior. Por cada frase, extraer
un frame a la mitad de su ventana `[start, end]`:

```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 ./reel_subtitles_test.mp4
# Por cada frase impresa en SUBTITLES, con inicio=S y fin=E:
ffmpeg -y -ss $(( (S + E) / 2 )) -i ./reel_subtitles_test.mp4 -vframes 1 ./frame_frase_N.png
```

(Ajustar el cálculo del punto medio y el nombre del archivo de salida por cada
frase real reportada — el número de frases depende del guion generado por Gemini
en tiempo real, no es fijo.)

- [ ] **Step 3: Revisión del controlador (Claude, no delegar)**

Ver cada `./frame_frase_N.png` con la herramienta Read y confirmar:
- El texto de cada frase es legible, blanco con contorno negro, no se corta ni se
  sale del cuadro (si se corta, revisar `_wrap_subtitle_text` — puede necesitar un
  `max_chars` menor).
- El subtítulo coincide temporalmente con lo que se esperaría de la narración en
  ese punto (no hace falta escuchar el audio para esto — solo confirmar que hay
  *algún* subtítulo visible en cada frame extraído, ya que cada uno cae dentro de
  una ventana `[start,end]` real).
- Extraer también un frame en el último segundo del video (`duration - 1`) y
  confirmar que el CTA aparece centrado verticalmente, no pegado abajo.

Si algo falla visualmente, ajustar el parámetro correspondiente (`max_chars` de
`_wrap_subtitle_text`, `_SUBTITLE_Y`, `_SUBTITLE_FONTSIZE`) y repetir desde el
Step 1 — son constantes de ajuste fino, no requieren cambiar la lógica.

- [ ] **Step 4: Limpieza**

```bash
rm -f ./reel_subtitles_test.mp4 ./frame_frase_*.png
```

(Son artefactos de verificación local, no deben quedar en el repo — no hay nada
que commitear en este task.)
