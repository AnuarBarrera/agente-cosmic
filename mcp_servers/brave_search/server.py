import logging
import os
import httpx
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
SERPER_API_KEY = os.environ.get('SERPER_API_KEY', '')
SERPER_WEB_URL = 'https://google.serper.dev/search'
SERPER_NEWS_URL = 'https://google.serper.dev/news'


def _serper_to_results(data: dict, is_news: bool) -> list:
    """Convierte respuesta de Serper al formato interno {title, url, description}."""
    items = data.get('news' if is_news else 'organic', [])
    return [
        {
            'title': r.get('title', ''),
            'url': r.get('link', ''),
            'description': r.get('snippet', ''),
        }
        for r in items
        if r.get('link')
    ]


@app.post('/call')
def call_tool():
    data = request.get_json(force=True)
    tool = data.get('tool')
    params = data.get('params', {})

    if tool != 'web_search':
        return jsonify({'error': f'Tool not found: {tool}'}), 404

    if not SERPER_API_KEY:
        return jsonify({'error': 'SERPER_API_KEY not set'}), 500

    query = params.get('query', '')
    count = int(params.get('count', 5))
    country = params.get('country', 'mx').lower()
    lang = params.get('search_lang', 'es')
    is_news = bool(params.get('freshness'))

    url = SERPER_NEWS_URL if is_news else SERPER_WEB_URL
    body = {'q': query, 'gl': country, 'hl': lang, 'num': count}

    try:
        resp = httpx.post(
            url,
            headers={'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'},
            json=body,
            timeout=10.0,
        )
        resp.raise_for_status()
        results = _serper_to_results(resp.json(), is_news)
        return jsonify({'web': {'results': results}})
    except httpx.HTTPStatusError as e:
        logger.error(f'Serper API error: {e}')
        return jsonify({'error': str(e)}), 502
    except Exception as e:
        logger.error(f'Server error: {e}')
        return jsonify({'error': str(e)}), 500


@app.get('/health')
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
