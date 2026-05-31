import logging
import django_rq
from django.conf import settings
from core.agent.domain.tools import BaseTool, ToolResult
from core.agent.infrastructure.browser import detect_platform

logger = logging.getLogger(__name__)

SUPPORTED_PLATFORMS = {'instagram', 'tiktok', 'facebook', 'linkedin', 'twitter'}

# Atajos de página: palabra clave → plataforma interna (mapea al workflow n8n)
PAGE_KEYWORDS = {
    'tuwebmx': 'facebook_tuwebmx',
    'anuarbarrera': 'facebook_anuarbarrera',
    'linkedin': 'linkedin',
    'instagram': 'instagram',
}

PAGE_LABELS = {
    'facebook_tuwebmx': 'Tu Web MX (Facebook)',
    'facebook_anuarbarrera': 'Anuar Barrera (Facebook)',
    'linkedin': 'LinkedIn',
    'instagram': 'Instagram',
}


class GetPostStatsTool(BaseTool):
    name = 'get_post_stats'

    def execute(self, url: str, chat_id: int = None) -> ToolResult:
        # Primero verificar si es un atajo de página (ej. "tuwebmx")
        keyword = url.lower().strip().lstrip('/')
        if keyword in PAGE_KEYWORDS:
            platform = PAGE_KEYWORDS[keyword]
            label = PAGE_LABELS.get(platform, platform)
        else:
            platform = detect_platform(url)
            if platform == 'unknown':
                return self._error(
                    "No reconozco esa URL o página. Plataformas soportadas: "
                    "Instagram, Facebook, LinkedIn.\n"
                    "Atajos disponibles: `tuwebmx`, `anuarbarrera`, `linkedin`"
                )
            label = platform.capitalize()

        try:
            queue = django_rq.get_queue('default')
            queue.enqueue(
                'core.agent.infrastructure.jobs.stats_n8n_job',
                url=url,
                platform=platform,
                chat_id=chat_id,
                job_timeout=60,
            )
        except Exception as e:
            logger.error(f"Error encolar stats_n8n_job: {e}", exc_info=True)
            return self._error(f"No pude iniciar el análisis: {e}")

        return ToolResult(
            content=(
                f"⏳ Obteniendo estadísticas de *{label}*...\n\n"
                f"Te aviso aquí cuando estén listas (suele tardar menos de 1 minuto)."
            ),
            tool_name=self.name,
            success=True,
            metadata={'platform': platform, 'url': url},
        )

    def _check_rate_limit(self, chat_id: int) -> bool:
        from django.core.cache import cache
        key = f"stats_rate:{chat_id}"
        count = cache.get(key, 0)
        if count >= 5:
            return False
        cache.set(key, count + 1, timeout=3600)
        return True
