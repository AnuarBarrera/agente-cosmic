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


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_analyze_truncates_long_category_to_model_max_length():
    """BrandDNA.product_category es CharField(max_length=100) — una categoria
    larga (el modelo puede ignorar el "1-3 palabras" del prompt) reventaba
    BrandDNA.objects.create() con DataError y tumbaba analyze_brand_task entero."""
    from core.brand_dna.extractors.product_photo_analyzer import ProductPhotoAnalyzer
    long_category = 'joyeria artesanal ' * 20  # 360 chars
    analyzer = ProductPhotoAnalyzer()
    with patch('core.brand_dna.extractors.product_photo_analyzer._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"description": "Aretes de plata", "category": "%s"}' % long_category
        )
        result = analyzer.analyze(b'fake-image-bytes', 'image/jpeg')

    assert len(result['category']) == 100
    assert result['category'] == long_category.strip()[:100]


def test_prompt_forbids_speculating_about_sector_or_intended_use():
    # HALLAZGO 2026-08-22 (produccion): una bata de carnicero blanca con
    # capucha se interpreto como "ideal para laboratorios o sector medico"
    # -- esa especulacion visual contaminaba la generacion de imagen real,
    # que no tiene forma de saber que el negocio es del sector carnico. El
    # analizador debe describir solo lo visual, nunca el uso/sector/publico.
    from core.brand_dna.extractors.product_photo_analyzer import _PROMPT
    assert 'NO especules' in _PROMPT
    assert 'sector' in _PROMPT.lower()
