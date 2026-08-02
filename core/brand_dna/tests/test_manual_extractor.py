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


@pytest.mark.django_db
def test_vertex_client_uses_global_text_location():
    from django.test import override_settings
    with override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global'):
        with patch('core.brand_dna.extractors.manual_extractor.genai.Client') as mock_client:
            from core.brand_dna.extractors.manual_extractor import _vertex_client
            _vertex_client()
        mock_client.assert_called_once_with(vertexai=True, project='agente-cosmic', location='global')


@pytest.mark.django_db
def test_extract_call_disables_thinking():
    from django.test import override_settings
    from core.brand_dna.extractors.manual_extractor import ManualBrandExtractor
    with override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
                            VERTEX_TEXT_MODEL='publishers/google/models/gemini-3.5-flash'):
        with patch('core.brand_dna.extractors.manual_extractor._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"business_name": "Negocio", "description": "Descripcion", "keywords": ["a"], "audience": "Todos", "tone": "casual"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            ManualBrandExtractor().extract('Negocio', 'Una descripcion')
            call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
        assert call_kwargs['config'].thinking_config.thinking_budget == 0
