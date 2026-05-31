from unittest.mock import patch, MagicMock
from django.test import override_settings

MOCK_TEXT_POSTS = """
Post 1: Nuevo proyecto terminado! Disenamos el sitio web de @ClienteMX. #disenoweb #webdesign
Post 2: Tu sitio web convierte visitas en clientes? Nosotros te ayudamos. #marketing
Post 3: Creatividad + estrategia = resultados. Asi trabajamos en Tu Web MX. #agenciadigital
"""

MOCK_VERTEX_JSON = """{
  "posting_style": "Posts cortos y directos con call to action claro",
  "avg_caption_length": 120,
  "common_hashtags": ["#disenoweb", "#webdesign", "#marketing", "#agenciadigital"]
}"""


def _mock_vertex_client(json_text):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json_text
    mock_client.models.generate_content.return_value = mock_resp
    return mock_client


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_analyze_text_returns_required_keys():
    from core.brand_dna.extractors.posts_analyzer import PostsAnalyzer
    analyzer = PostsAnalyzer()
    with patch('core.brand_dna.extractors.posts_analyzer._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_JSON)
        result = analyzer.analyze(text=MOCK_TEXT_POSTS)

    assert 'posting_style' in result
    assert 'avg_caption_length' in result
    assert 'common_hashtags' in result
    assert isinstance(result['common_hashtags'], list)


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_analyze_text_parses_correctly():
    from core.brand_dna.extractors.posts_analyzer import PostsAnalyzer
    analyzer = PostsAnalyzer()
    with patch('core.brand_dna.extractors.posts_analyzer._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_JSON)
        result = analyzer.analyze(text=MOCK_TEXT_POSTS)

    assert result['avg_caption_length'] == 120
    assert '#disenoweb' in result['common_hashtags']


def test_analyze_with_no_input_returns_defaults():
    from core.brand_dna.extractors.posts_analyzer import PostsAnalyzer
    analyzer = PostsAnalyzer()
    result = analyzer.analyze()

    assert result['posting_style'] == ''
    assert result['avg_caption_length'] == 150
    assert result['common_hashtags'] == []


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_analyze_handles_vertex_error():
    from core.brand_dna.extractors.posts_analyzer import PostsAnalyzer
    analyzer = PostsAnalyzer()
    with patch('core.brand_dna.extractors.posts_analyzer._vertex_client') as mock_vc:
        mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
        result = analyzer.analyze(text='Algun texto de posts')

    assert result['posting_style'] == ''
    assert result['avg_caption_length'] == 150
