import logging
import re
from google.cloud import speech
from core.shared.metrics_utils import track_external_api, record_stt_call

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
        audio_duration = len(narration_audio) / (_PCM_BYTES_PER_SAMPLE * _PCM_SAMPLE_RATE)
        record_stt_call(audio_duration_seconds=audio_duration)
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


def _split_long_subtitles(subtitles: list[dict], max_words: int = 10) -> list[dict]:
    # Una frase de guion puede tener muchas mas de 10 palabras (sin punto
    # intermedio) — mostrarla completa en un solo cuadro de subtitulo la hace
    # envolverse en demasiadas lineas y desbordarse verticalmente. Se corta en
    # bloques de maximo max_words palabras, cada uno con su propia ventana de
    # tiempo (repartida proporcionalmente por cantidad de palabras dentro de
    # la ventana [start,end] original de la frase).
    result = []
    for sub in subtitles:
        words = sub['text'].split()
        if len(words) <= max_words:
            result.append(sub)
            continue
        chunks = [words[i:i + max_words] for i in range(0, len(words), max_words)]
        total_words = len(words)
        duration = sub['end'] - sub['start']
        cursor = sub['start']
        for chunk in chunks:
            share = len(chunk) / total_words
            chunk_duration = duration * share
            result.append({
                'text': ' '.join(chunk),
                'start': cursor,
                'end': cursor + chunk_duration,
            })
            cursor += chunk_duration
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
            return _split_long_subtitles(aligned)

        total_duration = len(narration_audio) / (_PCM_BYTES_PER_SAMPLE * _PCM_SAMPLE_RATE)
        return _split_long_subtitles(_proportional_fallback(phrases, total_duration))
