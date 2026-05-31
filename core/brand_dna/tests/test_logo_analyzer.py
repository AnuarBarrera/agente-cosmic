from unittest.mock import patch, MagicMock
from django.test import override_settings


def _mock_vision_response():
    mock = MagicMock()
    color1 = MagicMock()
    color1.color.red = 26
    color1.color.green = 26
    color1.color.blue = 46
    color1.pixel_fraction = 0.6
    color2 = MagicMock()
    color2.color.red = 233
    color2.color.green = 69
    color2.color.blue = 96
    color2.pixel_fraction = 0.3
    mock.image_properties_annotation.dominant_colors.colors = [color1, color2]
    return mock


def _mock_vertex_client(description_text):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = description_text
    mock_client.models.generate_content.return_value = mock_resp
    return mock_client


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_analyze_returns_required_keys():
    from core.brand_dna.extractors.logo_analyzer import LogoAnalyzer
    analyzer = LogoAnalyzer()
    with patch('core.brand_dna.extractors.logo_analyzer.vision') as mock_vision, \
         patch('core.brand_dna.extractors.logo_analyzer._vertex_client') as mock_vc:
        mock_vision.ImageAnnotatorClient.return_value.annotate_image.return_value = _mock_vision_response()
        mock_vc.return_value = _mock_vertex_client('Tipografia sans-serif moderna, diseno minimalista')
        result = analyzer.analyze(b'fake-image-bytes', 'image/png')

    assert 'primary_colors' in result
    assert 'logo_elements' in result
    assert isinstance(result['primary_colors'], list)


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_analyze_extracts_hex_colors():
    from core.brand_dna.extractors.logo_analyzer import LogoAnalyzer
    analyzer = LogoAnalyzer()
    with patch('core.brand_dna.extractors.logo_analyzer.vision') as mock_vision, \
         patch('core.brand_dna.extractors.logo_analyzer._vertex_client') as mock_vc:
        mock_vision.ImageAnnotatorClient.return_value.annotate_image.return_value = _mock_vision_response()
        mock_vc.return_value = _mock_vertex_client('Tipografia moderna')
        result = analyzer.analyze(b'fake-image-bytes', 'image/png')

    assert '#1a1a2e' in result['primary_colors']
    assert '#e94560' in result['primary_colors']


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_analyze_handles_vision_error():
    from core.brand_dna.extractors.logo_analyzer import LogoAnalyzer
    analyzer = LogoAnalyzer()
    with patch('core.brand_dna.extractors.logo_analyzer.vision') as mock_vision, \
         patch('core.brand_dna.extractors.logo_analyzer._vertex_client'):
        mock_vision.ImageAnnotatorClient.return_value.annotate_image.side_effect = Exception('API error')
        result = analyzer.analyze(b'fake-image-bytes', 'image/png')

    assert result['primary_colors'] == []
    assert result['logo_elements'] == ''
