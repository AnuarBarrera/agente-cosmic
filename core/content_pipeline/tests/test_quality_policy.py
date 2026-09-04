from io import BytesIO
from unittest.mock import patch

from django.test import override_settings
from PIL import Image

from core.content_pipeline.generators.image_generator import ImageGenerator
from core.content_pipeline.quality import (
    classify_regeneration_feedback,
    classify_scene_complexity,
    simplify_scene_direction,
)
from core.content_pipeline.generators.claim_auditor import ensure_supported_text


def _png(width=80, height=40, color='red'):
    output = BytesIO()
    Image.new('RGB', (width, height), color).save(output, format='PNG')
    return output.getvalue()


def test_feedback_classifier_understands_change_image_as_visual():
    assert classify_regeneration_feedback('CAMBIAR IMAGEN') == 'visual'
    assert classify_regeneration_feedback('corrige el texto') == 'text'
    assert classify_regeneration_feedback('cambia la foto y el título') == 'both'
    assert classify_regeneration_feedback('hazlo mejor') == 'both'


def test_claim_guard_removes_unsupported_medical_outcome():
    corrected, findings = ensure_supported_text(
        'Nuestras gasas ofrecen una absorción superior que optimiza el tiempo en quirófano.',
        {'source_fragments': []},
    )
    assert corrected == 'Conoce lo que tenemos para ti. Contáctanos para más información.'
    assert any(item['category'] == 'unsupported_outcome' for item in findings)


def test_high_complexity_scene_is_simplified():
    result = classify_scene_complexity('Una persona sosteniendo el producto con las manos')
    assert result.level == 'high'
    assert 'no hands' in simplify_scene_direction('original', result)


def test_safe_reference_fallback_uses_original_pixels_in_square_canvas():
    result = ImageGenerator._safe_reference_fallback(_png())
    image = Image.open(BytesIO(result))
    assert image.size == (1080, 1080)
    assert image.getpixel((540, 540)) == (255, 0, 0)


def test_safe_reference_fallback_enlarges_low_resolution_reference():
    result = ImageGenerator._safe_reference_fallback(_png(width=80, height=40))
    image = Image.open(BytesIO(result))
    red_pixels = [
        x for x in range(image.width)
        if image.getpixel((x, image.height // 2)) == (255, 0, 0)
    ]
    assert len(red_pixels) >= 800


@override_settings(COMPARATIVE_PRODUCT_QC_ENABLED=True)
def test_rejected_second_photo_edit_returns_safe_fallback_not_rejected_result():
    gen = ImageGenerator('bucket')
    original = _png(color='blue')
    rejected = _png(color='green')
    with patch.object(gen, '_generate_from_photo_with_retry', return_value=rejected) as generate, \
         patch.object(gen, '_validate_comparative_product_generation',
                      side_effect=[(False, 'wrong shape'), (False, 'wrong product')]):
        result = gen._generate_validated_photo_edit(
            'creative', object(), max_qc_retries=20, original_bytes=original,
        )
    assert generate.call_count == 2
    assert Image.open(BytesIO(result)).getpixel((540, 540)) == (0, 0, 255)
    assert 'SECOND AND FINAL ATTEMPT' in generate.call_args.args[0]


@override_settings(FINAL_MEDIA_QC_ENABLED=True)
def test_layered_pipeline_discards_rejected_overlay_without_regenerating_background():
    gen = ImageGenerator('bucket')
    background = _png()
    with patch.object(gen, '_generate_post_content', return_value={'headline': 'H'}), \
         patch.object(gen, '_render_html_template', return_value=b'bad-overlay'), \
         patch.object(gen, '_validate_final_image', return_value=False):
        assert gen._layered_pipeline('caption', [], 'tone', background_bytes=background) == background
