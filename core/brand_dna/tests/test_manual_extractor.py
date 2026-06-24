import json
import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.django_db

MOCK_GEMINI_RESPONSE = json.dumps({
    'business_name': 'Tamales Doña Lupita',
    'description': 'Tamales oaxaqueños artesanales vendidos en el mercado de Coyoacán.',
    'keywords': ['tamales', 'oaxaqueños', 'artesanales', 'comida mexicana', 'mercado'],
    'audience': 'Personas que buscan comida tradicional mexicana de calidad.',
    'tone': 'amigable',
    'brand_colors': [],
})


def _mock_resp(text):
    resp = MagicMock()
    resp.text = text
    resp.usage_metadata = MagicMock()
    resp.usage_metadata.prompt_token_count = 100
    resp.usage_metadata.candidates_token_count = 50
    resp.usage_metadata.total_token_count = 150
    return resp


def test_extract_returns_expected_keys():
    from core.brand_dna.extractors.manual_extractor import ManualBrandExtractor

    with patch('core.brand_dna.extractors.manual_extractor._vertex_client') as mock_client:
        mock_client.return_value.models.generate_content.return_value = _mock_resp(MOCK_GEMINI_RESPONSE)
        result = ManualBrandExtractor().extract(
            business_name='Tamales Doña Lupita',
            description='Vendo tamales oaxaqueños en el mercado de Coyoacán',
        )

    assert result['business_name'] == 'Tamales Doña Lupita'
    assert 'tamales' in result['keywords']
    assert result['tone'] in ('formal', 'casual', 'inspiracional', 'urgente', 'profesional', 'amigable')
    assert isinstance(result['brand_colors'], list)
    assert isinstance(result['keywords'], list)


def test_extract_handles_gemini_error():
    from core.brand_dna.extractors.manual_extractor import ManualBrandExtractor

    with patch('core.brand_dna.extractors.manual_extractor._vertex_client') as mock_client:
        mock_client.return_value.models.generate_content.side_effect = Exception('API down')
        result = ManualBrandExtractor().extract(
            business_name='Mi Negocio',
            description='Vendo cosas',
        )

    assert result['business_name'] == 'Mi Negocio'
    assert result['tone'] == 'profesional'
    assert result['brand_colors'] == []


def test_extract_handles_json_in_code_block():
    from core.brand_dna.extractors.manual_extractor import ManualBrandExtractor

    wrapped = '```json\n' + MOCK_GEMINI_RESPONSE + '\n```'
    with patch('core.brand_dna.extractors.manual_extractor._vertex_client') as mock_client:
        mock_client.return_value.models.generate_content.return_value = _mock_resp(wrapped)
        result = ManualBrandExtractor().extract(
            business_name='Tamales',
            description='Tamales en el mercado',
        )

    assert result['business_name'] == 'Tamales Doña Lupita'
