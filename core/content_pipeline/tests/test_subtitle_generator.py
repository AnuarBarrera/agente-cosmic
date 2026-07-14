import pytest
from unittest.mock import patch, MagicMock
from core.content_pipeline.generators.subtitle_generator import (
    SubtitleGenerator, _split_into_phrases, _split_long_subtitles,
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
    def test_splits_long_aligned_phrase_into_multiple_subtitles(self, mock_speech_client):
        # Una sola frase (sin punto intermedio) con 12 palabras -> debe quedar
        # partida en 2 subtitulos de <=10 palabras cada uno.
        script_words = [f'palabra{i}' for i in range(1, 13)]
        script = ' '.join(script_words) + '.'
        words = [_make_word(w, float(i), float(i) + 0.5) for i, w in enumerate(script_words[:-1])]
        words.append(_make_word(script_words[-1] + '.', 11.0, 11.5))
        mock_client_instance = MagicMock()
        mock_client_instance.recognize.return_value = _make_stt_response(words)
        mock_speech_client.return_value = mock_client_instance

        gen = SubtitleGenerator()
        result = gen.generate(b'\x00\x01' * 100, script)

        assert len(result) == 2
        assert len(result[0]['text'].split()) == 10
        assert len(result[1]['text'].split()) == 2

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


class TestSplitLongSubtitles:
    def test_leaves_short_subtitle_unchanged(self):
        subs = [{'text': 'Tu negocio en linea.', 'start': 0.0, 'end': 1.5}]
        assert _split_long_subtitles(subs, max_words=10) == subs

    def test_splits_subtitle_over_max_words_into_chunks(self):
        # 14 palabras -> 2 bloques de 10 y 4, tiempo repartido proporcional
        # a la cantidad de palabras de cada bloque.
        text = ' '.join(f'palabra{i}' for i in range(1, 15))
        subs = [{'text': text, 'start': 0.0, 'end': 14.0}]
        result = _split_long_subtitles(subs, max_words=10)

        assert len(result) == 2
        assert result[0]['text'] == ' '.join(f'palabra{i}' for i in range(1, 11))
        assert result[1]['text'] == ' '.join(f'palabra{i}' for i in range(11, 15))
        assert result[0]['start'] == 0.0
        assert result[0]['end'] == pytest.approx(10.0)
        assert result[1]['start'] == pytest.approx(10.0)
        assert result[1]['end'] == pytest.approx(14.0)

    def test_preserves_short_subtitles_among_long_ones(self):
        long_text = ' '.join(f'p{i}' for i in range(12))
        subs = [
            {'text': 'Corto.', 'start': 0.0, 'end': 1.0},
            {'text': long_text, 'start': 1.0, 'end': 5.0},
        ]
        result = _split_long_subtitles(subs, max_words=10)
        assert result[0] == {'text': 'Corto.', 'start': 0.0, 'end': 1.0}
        assert len(result) == 3


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
