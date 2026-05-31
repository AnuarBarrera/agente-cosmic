import logging
import re
import unicodedata
from django.conf import settings
from core.agent.domain.tools import BaseTool, ToolResult
from core.agent.infrastructure.gemini_adapter import GeminiAdapter, FALLBACK_MESSAGE
from core.agent.infrastructure.mcp_client import McpClient
from core.agent.infrastructure import scrape_guard

logger = logging.getLogger(__name__)

MAX_RESULTS = 5

_CONVERSATIONAL_PREFIXES = re.compile(
    r'^(?:puedes?\s+)?(?:busca[rm]e?|buscar|investiga[rm]e?|dame|muéstrame?|dime|quiero\s+saber|encuéntrame?)\s+',
    re.IGNORECASE,
)

_NEWS_KEYWORDS = re.compile(
    r'\b(?:noticia[s]?|últim[ao]s?|reciente[s]?|hoy|esta\s+semana|este\s+mes|'
    r'actualidad|novedades?|avance[s]?|trending|viral|novedad)\b',
    re.IGNORECASE,
)


def _normalize_ascii(text: str) -> str:
    return unicodedata.normalize('NFD', text).encode('ascii', 'ignore').decode('ascii')


def _strip_conversational_prefix(query: str) -> str:
    m = _CONVERSATIONAL_PREFIXES.search(_normalize_ascii(query))
    return query[m.end():].strip() if m else query


class WebSearchTool(BaseTool):
    name = 'web_search'

    def __init__(self):
        self._gemini = GeminiAdapter()
        self._mcp = McpClient()

    def execute(self, query: str) -> ToolResult:
        api_key = settings.GEMINI_API_KEY
        model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')
        if not api_key:
            return self._error('API key de Gemini no configurada.')

        clean_query = _strip_conversational_prefix(query)
        logger.info(f'Query: "{query}" → "{clean_query}"')

        mcp_params = {
            'query': clean_query,
            'count': MAX_RESULTS,
            'country': 'MX',
            'search_lang': 'es',
            'ui_lang': 'es-MX',
        }
        if _NEWS_KEYWORDS.search(clean_query):
            mcp_params['freshness'] = 'pw'

        try:
            data = self._mcp.call('brave_search', 'web_search', mcp_params)
        except Exception as e:
            logger.error(f'Error en McpClient (brave_search): {e}', exc_info=True)
            return self._error(f'Error al buscar en internet: {e}')

        raw_results = data.get('web', {}).get('results', [])
        if not raw_results:
            return self._error(f'No encontré resultados para: {clean_query}')

        snippets = []
        sources = []
        for r in raw_results:
            url = r.get('url', '')
            if not url:
                continue
            title = r.get('title', '')
            body = r.get('description', '')
            safe_body = scrape_guard.safe_external_content(body, source=url)
            snippets.append(f'**{title}**\n{safe_body}')
            sources.append(url)

        if not snippets:
            return self._error(f'No encontré resultados para: {clean_query}')

        context = '\n\n'.join(snippets)
        prompt = (
            f'El usuario busca: "{query}"\n\n'
            f'Aquí están los resultados de búsqueda:\n\n{context}\n\n'
            f'Resume los hallazgos más relevantes en 3-5 puntos concisos en español. '
            f'NO listes las fuentes, eso se hace por separado.'
        )
        summary = self._gemini.generate_response(
            prompt=prompt, api_key=api_key, model_name=model
        )
        if summary == FALLBACK_MESSAGE or not summary.strip():
            return self._error('El servicio de IA no está disponible temporalmente.')

        sources_text = '\n'.join(f'• {url}' for url in sources)
        content = f'{summary}\n\n📎 *Fuentes:*\n{sources_text}'
        return ToolResult(
            content=content,
            tool_name=self.name,
            success=True,
            metadata={'query': query, 'num_results': len(raw_results)},
        )
