import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db


@pytest.fixture
def brand_dna():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://elperrorebelde.com')
    return BrandDNA.objects.create(
        job=job, business_name='El Perro Rebelde', business_url='https://elperrorebelde.com',
        description='Ropa y accesorios para mascotas hechos con tecnica de upcycling',
        keywords=['upcycling', 'moda sostenible'],
        audience='Dueños de mascotas conscientes', tone='premium y consciente',
        primary_colors=['#1a1a2e'],
    )


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
)
def test_audit_returns_empty_when_all_fields_ok(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import audit_brand_consistency
    response_json = '{"narration_script": {"ok": true, "reason": ""}}'
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = audit_brand_consistency({'narration_script': 'Hecho con upcycling.'}, brand_dna)
    assert result == {}


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_audit_returns_issue_when_field_flagged(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import audit_brand_consistency
    response_json = (
        '{"narration_script": {"ok": false, "reason": '
        '"Reemplaza upcycling por materiales reutilizados, connotacion inferior"}}'
    )
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = audit_brand_consistency(
            {'narration_script': 'Hecho con materiales reutilizados.'}, brand_dna,
        )
    assert 'narration_script' in result
    assert 'upcycling' in result['narration_script']


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_audit_fails_open_on_exception(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import audit_brand_consistency
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
        result = audit_brand_consistency({'narration_script': 'texto'}, brand_dna)
    assert result == {}


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_audit_fails_open_on_unparseable_response(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import audit_brand_consistency
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client('esto no es json')
        result = audit_brand_consistency({'narration_script': 'texto'}, brand_dna)
    assert result == {}


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_rewrite_returns_new_text_on_success(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import rewrite_for_brand_consistency
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client('Hecho con upcycling, moda circular consciente.')
        result = rewrite_for_brand_consistency(
            'narration_script', 'Hecho con materiales reutilizados.',
            'Reemplaza upcycling por un termino de connotacion inferior', brand_dna,
        )
    assert result == 'Hecho con upcycling, moda circular consciente.'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_rewrite_returns_original_text_on_failure(brand_dna):
    from core.content_pipeline.generators.brand_consistency_qc import rewrite_for_brand_consistency
    with patch('core.content_pipeline.generators.brand_consistency_qc._vertex_client') as mock_vc:
        mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
        original = 'Hecho con materiales reutilizados.'
        result = rewrite_for_brand_consistency('narration_script', original, 'razon', brand_dna)
    assert result == original
