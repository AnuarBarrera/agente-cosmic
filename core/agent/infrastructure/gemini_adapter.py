import time
import random
import logging
from google import genai
from google.genai import errors as genai_errors, types

logger = logging.getLogger(__name__)

_FALLBACK_MSG = (
    "Disculpa, no pudimos procesar tu solicitud en este momento. "
    "Por favor, inténtalo de nuevo más tarde."
)
FALLBACK_MESSAGE = _FALLBACK_MSG

_DEFAULT_TIMEOUT = 180_000  # milisegundos (HttpOptions usa ms)


class GeminiAdapter:
    def generate_response(
        self,
        prompt: str,
        api_key: str,
        model_name: str = 'gemini-2.5-flash',
        thinking_budget: int | None = None,
        timeout: int = _DEFAULT_TIMEOUT,  # en milisegundos
    ) -> str:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout),
        )
        config = None
        if thinking_budget is not None:
            config = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget)
            )
        max_retries = 3
        base_delay = 2

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )
                return response.text
            except (genai_errors.ClientError, genai_errors.ServerError) as e:
                status = getattr(e, 'status_code', None)
                retryable = status in (429, 503)
                if not retryable:
                    logger.error(f"Error Gemini {status}: {e}", exc_info=True)
                    return _FALLBACK_MSG
                if attempt < max_retries - 1:
                    wait = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(f"Gemini {status}, reintentando en {wait:.1f}s (intento {attempt + 1})")
                    time.sleep(wait)
                else:
                    logger.error(f"Gemini {status}: máximo de reintentos alcanzado")
                    return _FALLBACK_MSG
            except Exception as e:
                logger.error(f"Error Gemini: {e}", exc_info=True)
                return _FALLBACK_MSG

        return _FALLBACK_MSG
