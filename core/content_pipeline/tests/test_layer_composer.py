import io
import numpy as np
from PIL import Image


def _solid_image_bytes(color: tuple, size=(64, 64)) -> bytes:
    img = Image.new('RGB', size, color)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _magenta_with_white_center(size=64) -> bytes:
    """Image with magenta background and a white 20x20 center square."""
    img = Image.new('RGB', (size, size), (255, 0, 255))
    center = size // 2
    half = 10
    for y in range(center - half, center + half):
        for x in range(center - half, center + half):
            img.putpixel((x, y), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


class TestRemoveChromaKey:
    def test_full_magenta_image_becomes_transparent(self):
        from core.content_pipeline.generators.layer_composer import remove_chroma_key
        magenta_bytes = _solid_image_bytes((255, 0, 255))
        result = remove_chroma_key(magenta_bytes)
        arr = np.array(result)
        assert arr.shape[2] == 4  # RGBA
        assert arr[:, :, 3].max() == 0  # all pixels transparent

    def test_non_magenta_pixels_keep_opacity(self):
        from core.content_pipeline.generators.layer_composer import remove_chroma_key
        asset_bytes = _magenta_with_white_center()
        result = remove_chroma_key(asset_bytes)
        arr = np.array(result)
        center = 32
        # The white center pixels should remain opaque
        assert arr[center, center, 3] == 255

    def test_returns_rgba_image(self):
        from core.content_pipeline.generators.layer_composer import remove_chroma_key
        result = remove_chroma_key(_solid_image_bytes((100, 150, 200)))
        assert result.mode == 'RGBA'


class TestCompositeLayers:
    def test_composite_returns_valid_png(self):
        from core.content_pipeline.generators.layer_composer import composite_layers
        bg_bytes = _solid_image_bytes((30, 30, 60), size=(128, 128))
        text_bytes = _magenta_with_white_center(size=64)
        result = composite_layers(bg_bytes, text_bytes, x=0.1, y=0.5, width=0.5)
        out = Image.open(io.BytesIO(result))
        assert out.size == (128, 128)
        assert out.mode == 'RGB'

    def test_composite_with_rotation_still_valid(self):
        from core.content_pipeline.generators.layer_composer import composite_layers
        bg_bytes = _solid_image_bytes((30, 30, 60), size=(128, 128))
        text_bytes = _magenta_with_white_center(size=64)
        result = composite_layers(bg_bytes, text_bytes, x=0.1, y=0.5, width=0.5, rotation_deg=5.0)
        out = Image.open(io.BytesIO(result))
        assert out.size == (128, 128)

    def test_composite_clamps_x_y_within_image(self):
        from core.content_pipeline.generators.layer_composer import composite_layers
        bg_bytes = _solid_image_bytes((30, 30, 60), size=(128, 128))
        text_bytes = _magenta_with_white_center(size=64)
        # Should not raise even with x=0.95 (text asset goes off edge)
        result = composite_layers(bg_bytes, text_bytes, x=0.95, y=0.95, width=0.8)
        assert len(result) > 0
