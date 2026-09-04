from core.brand_dna.fact_profile import build_brand_fact_profile


def test_fact_profile_only_confirms_explicit_commercial_facts():
    profile = build_brand_fact_profile(
        'Marca\nConfecciono prendas con upcycling de mezclilla para mascotas.',
        keywords=['upcycling', 'mezclilla', 'envíos nacionales'],
    )

    assert profile['confirmed_commercial_terms'] == []
    assert profile['confirmed_service_area'] == []
    assert profile['confirmed_materials'] == ['mezclilla']
    assert profile['differentiating_terms'] == ['upcycling', 'mezclilla']
    assert profile['source_fragments'][1]['source'] == 'business_description'


def test_fact_profile_retains_user_provided_promotion_without_inventing_terms():
    profile = build_brand_fact_profile(
        'Comercializadora\nTenemos grandes promociones y precios accesibles.',
    )

    assert profile['confirmed_commercial_terms'] == [
        'Tenemos grandes promociones y precios accesibles.'
    ]
    assert profile['confirmed_service_area'] == []


def test_fact_profile_recognizes_canonical_upcycling_from_user_typo():
    profile = build_brand_fact_profile(
        'Marca\nConfecciono prendas con upcicling de mezclilla.',
        keywords=['upcycling', 'mezclilla'],
    )

    assert profile['differentiating_terms'] == ['upcycling', 'mezclilla']
