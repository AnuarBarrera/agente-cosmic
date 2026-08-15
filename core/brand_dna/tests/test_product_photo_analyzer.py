from unittest.mock import patch, MagicMock
from django.test import override_settings


def _mock_vertex_client(response_json):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = response_json
    mock_client.models.generate_content.return_value = mock_resp
    return mock_client


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_analyze_returns_description_and_category():
    from core.brand_dna.extractors.product_photo_analyzer import ProductPhotoAnalyzer
    analyzer = ProductPhotoAnalyzer()
    with patch('core.brand_dna.extractors.product_photo_analyzer._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"description": "Aretes de plata con piedra turquesa, estilo boho artesanal", "category": "joyeria"}'
        )
        result = analyzer.analyze(b'fake-image-bytes', 'image/jpeg')

    assert result['description'] == 'Aretes de plata con piedra turquesa, estilo boho artesanal'
    assert result['category'] == 'joyeria'


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_analyze_handles_error_fail_open():
    from core.brand_dna.extractors.product_photo_analyzer import ProductPhotoAnalyzer
    analyzer = ProductPhotoAnalyzer()
    with patch('core.brand_dna.extractors.product_photo_analyzer._vertex_client', side_effect=Exception('boom')):
        result = analyzer.analyze(b'fake-image-bytes', 'image/jpeg')

    assert result == {'description': '', 'category': ''}


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_analyze_handles_malformed_json_fail_open():
    from core.brand_dna.extractors.product_photo_analyzer import ProductPhotoAnalyzer
    analyzer = ProductPhotoAnalyzer()
    with patch('core.brand_dna.extractors.product_photo_analyzer._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client('no es json')
        result = analyzer.analyze(b'fake-image-bytes', 'image/jpeg')

    assert result == {'description': '', 'category': ''}
