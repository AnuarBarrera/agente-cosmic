from core.content_pipeline.generators.claim_auditor import (
    audit_claims,
    ensure_supported_text,
    has_blocking_claims,
)
from django.test import override_settings


def test_rejects_unconfirmed_discount_and_uses_safe_text():
    corrected, report = ensure_supported_text(
        'Aprovecha 30% de descuento este mes.', {}, field_name='caption',
    )

    assert has_blocking_claims(report)
    assert '30%' not in corrected
    assert 'descuento' not in corrected.lower()


def test_allows_commercial_claim_when_matching_source_is_confirmed():
    profile = {
        'confirmed_commercial_terms': ['Obtén 30% de descuento durante septiembre.'],
    }
    findings = audit_claims('Obtén 30% de descuento durante septiembre.', profile)

    assert findings
    assert all(item['decision'] == 'allow' for item in findings)


def test_rejects_unconfirmed_shipping_and_certification():
    findings = audit_claims('Envíos a todo México. Producto certificado.', {})

    assert {item['category'] for item in findings} == {'service_area', 'certifications'}
    assert all(item['decision'] == 'needs_confirmation' for item in findings)


def test_moderate_claim_is_softened_when_not_explicitly_allowed():
    findings = audit_claims('Una opción duradera para tu hogar.', {})

    assert findings[0]['risk_level'] == 'moderate'
    assert findings[0]['decision'] == 'soften'


@override_settings(CLAIM_GUARD_ENABLED=False)
def test_image_field_guard_is_noop_when_flag_disabled():
    from core.content_pipeline.generators.image_generator import _claim_guard_fields

    fields = {'headline': '30% de descuento', 'subtitle': '', 'cta': 'Compra hoy'}
    assert _claim_guard_fields(fields, {}) == fields


@override_settings(CLAIM_GUARD_ENABLED=True)
def test_image_field_guard_removes_unsupported_claim_when_enabled():
    from core.content_pipeline.generators.image_generator import _claim_guard_fields

    fields = {'headline': '30% de descuento', 'subtitle': '', 'cta': 'Compra hoy'}
    guarded = _claim_guard_fields(fields, {})
    assert '30%' not in guarded['headline']


def test_general_promotion_does_not_authorize_unconfirmed_month_or_percentage():
    profile = {
        'confirmed_commercial_terms': ['Tenemos grandes promociones y precios accesibles.'],
    }

    corrected, findings = ensure_supported_text(
        'Tenemos precios especiales este mes y 10% de descuento.', profile,
    )

    assert 'este mes' not in corrected.lower()
    assert '10%' not in corrected
    assert any(item['decision'] == 'needs_confirmation' for item in findings)


def test_local_shipping_does_not_authorize_national_shipping():
    profile = {'confirmed_service_area': ['Hacemos envíos locales.']}

    corrected, _ = ensure_supported_text('Hacemos envíos a todo el país.', profile)

    assert 'todo el país' not in corrected.lower()
