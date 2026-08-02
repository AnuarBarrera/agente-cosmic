import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

MOCK_HTML = """
<html>
<head><title>Tu Web MX — Diseño Web Profesional</title></head>
<body>
<h1>Diseño web que convierte</h1>
<p>Creamos sitios web modernos para empresas en México.</p>
</body>
</html>
"""

MOCK_GEMINI_RESPONSE = """{
  "business_name": "Tu Web MX",
  "description": "Agencia de diseño web profesional especializada en e-commerce",
  "keywords": ["diseño web", "e-commerce", "landing pages", "México"],
  "audience": "Empresas medianas en México",
  "tone": "profesional"
}"""


def _mock_vertex_client(mock_text):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = mock_text
    mock_client.models.generate_content.return_value = mock_resp
    return mock_client


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_extract_returns_required_keys():
    from core.brand_dna.extractors.web_scraper import WebScraper
    scraper = WebScraper()
    with patch('requests.get') as mock_get, \
         patch('core.brand_dna.extractors.web_scraper._vertex_client') as mock_vc:
        mock_get.return_value.text = MOCK_HTML
        mock_vc.return_value = _mock_vertex_client(MOCK_GEMINI_RESPONSE)

        result = scraper.extract('https://tuwebmx.com')

    assert 'business_name' in result
    assert 'description' in result
    assert 'keywords' in result
    assert 'audience' in result
    assert 'tone' in result
    assert isinstance(result['keywords'], list)


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_extract_parses_vertex_json():
    from core.brand_dna.extractors.web_scraper import WebScraper
    scraper = WebScraper()
    with patch('requests.get') as mock_get, \
         patch('core.brand_dna.extractors.web_scraper._vertex_client') as mock_vc:
        mock_get.return_value.text = MOCK_HTML
        mock_vc.return_value = _mock_vertex_client(MOCK_GEMINI_RESPONSE)

        result = scraper.extract('https://tuwebmx.com')

    assert result['business_name'] == 'Tu Web MX'
    assert result['tone'] == 'profesional'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_extract_handles_request_error():
    from core.brand_dna.extractors.web_scraper import WebScraper
    scraper = WebScraper()
    with patch('requests.get') as mock_get:
        mock_get.side_effect = Exception('Connection error')

        result = scraper.extract('https://sitio-invalido.com')

    assert result['business_name'] == 'Negocio'
    assert result['tone'] == 'profesional'
    assert isinstance(result['keywords'], list)


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_vertex_client_uses_global_text_location():
    with patch('core.brand_dna.extractors.web_scraper.genai.Client') as mock_client:
        from core.brand_dna.extractors.web_scraper import _vertex_client
        _vertex_client()
    mock_client.assert_called_once_with(vertexai=True, project='agente-cosmic', location='global')


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
                    VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash')
def test_analyze_with_vertex_disables_thinking():
    from core.brand_dna.extractors.web_scraper import WebScraper
    with patch('core.brand_dna.extractors.web_scraper._vertex_client') as mock_vc:
        mock_resp = MagicMock()
        mock_resp.text = '{"business_name": "Negocio", "description": "Descripcion", "keywords": [], "audience": "Todos", "tone": "casual", "brand_colors": []}'
        mock_vc.return_value.models.generate_content.return_value = mock_resp
        WebScraper()._analyze_with_vertex('texto de sitio', [])
        call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
    assert call_kwargs['config'].thinking_config.thinking_budget == 0
