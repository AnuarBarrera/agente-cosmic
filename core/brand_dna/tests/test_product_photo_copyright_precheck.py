from unittest.mock import patch, MagicMock
from django.test import override_settings


def _mock_vertex_client(response_json):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = response_json
    mock_client.models.generate_content.return_value = mock_resp
    return mock_client


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_ok_when_no_flags_active():
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"has_recognizable_brand_logo": false, "has_licensed_character_or_ip": false, '
            '"has_third_party_packaging_design": false, "ok": true}'
        )
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result == {'ok': True}


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_rejects_when_brand_logo_detected():
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"has_recognizable_brand_logo": true, "has_licensed_character_or_ip": false, '
            '"has_third_party_packaging_design": false, "ok": false}'
        )
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result['ok'] is False
    assert result['reason']


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_rejects_when_licensed_character_detected():
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"has_recognizable_brand_logo": false, "has_licensed_character_or_ip": true, '
            '"has_third_party_packaging_design": false, "ok": false}'
        )
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result['ok'] is False


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_rejects_when_third_party_packaging_detected():
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"has_recognizable_brand_logo": false, "has_licensed_character_or_ip": false, '
            '"has_third_party_packaging_design": true, "ok": false}'
        )
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result['ok'] is False


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_ignores_inconsistent_ok_from_llm():
    """Si el LLM manda ok=true pero algun flag individual es true, el
    veredicto se re-deriva en Python (mismo patron que ProductPhotoQCSchema
    en image_generator.py) -- no se confia en el campo ok crudo."""
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"has_recognizable_brand_logo": true, "has_licensed_character_or_ip": false, '
            '"has_third_party_packaging_design": false, "ok": true}'
        )
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result['ok'] is False


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_check_fails_open_on_exception():
    from core.brand_dna.extractors.product_photo_copyright_precheck import ProductPhotoCopyrightPrecheck
    precheck = ProductPhotoCopyrightPrecheck()
    with patch('core.brand_dna.extractors.product_photo_copyright_precheck._vertex_client', side_effect=Exception('boom')):
        result = precheck.check(b'fake-image-bytes', 'image/jpeg')

    assert result == {'ok': True, 'skipped': True}
