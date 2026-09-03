from core.brand_dna.extractors.product_photo_triage import (
    apply_triage_rules, infer_commercial_relationship,
)
from core.brand_dna.models import ProductReferenceAsset


def test_relationship_inference_uses_business_context():
    assert infer_commercial_relationship('Somos una comercializadora veterinaria') == 'reseller'
    assert infer_commercial_relationship('Elaboramos prendas hechas a mano') == 'maker'
    assert infer_commercial_relationship('Productos para mascotas') == 'unknown'


def test_dense_composite_ad_is_context_only():
    mode, reason = apply_triage_rules({
        'is_composite_ad': True, 'has_dense_text': True,
        'has_existing_price_or_promotion': True,
    }, ProductReferenceAsset.RELATIONSHIP_RESELLER)
    assert mode == ProductReferenceAsset.USAGE_CONTEXT_ONLY
    assert reason == 'composite_or_promotion_dense'


def test_clean_maker_product_allows_creative_edit():
    mode, reason = apply_triage_rules({}, ProductReferenceAsset.RELATIONSHIP_MAKER)
    assert mode == ProductReferenceAsset.USAGE_EDIT_ALLOWED
    assert reason == 'clean_maker_product'


def test_clean_third_party_packaging_is_preserved_for_reseller():
    mode, _ = apply_triage_rules(
        {'has_third_party_packaging': True}, ProductReferenceAsset.RELATIONSHIP_RESELLER,
    )
    assert mode == ProductReferenceAsset.USAGE_PRESERVE_ONLY


def test_licensed_character_is_never_edited():
    mode, _ = apply_triage_rules(
        {'has_licensed_character': True}, ProductReferenceAsset.RELATIONSHIP_MAKER,
    )
    assert mode == ProductReferenceAsset.USAGE_CONTEXT_ONLY
