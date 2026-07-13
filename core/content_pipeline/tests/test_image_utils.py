import io

from PIL import Image

from core.content_pipeline.image_utils import normalize_image


def _jpeg_with_orientation(width: int, height: int, orientation: int) -> bytes:
    img = Image.new('RGB', (width, height), color='red')
    exif = img.getexif()
    exif[274] = orientation  # tag EXIF de orientacion
    buf = io.BytesIO()
    img.save(buf, format='JPEG', exif=exif)
    return buf.getvalue()


class TestNormalizeImage:
    def test_applies_exif_orientation_before_resizing(self):
        # Foto de celular: pixeles guardados en landscape (100x50), pero el
        # EXIF orientation=6 indica que debe mostrarse en portrait (50x100).
        raw = _jpeg_with_orientation(100, 50, orientation=6)
        result = normalize_image(raw)
        img = Image.open(io.BytesIO(result))
        assert img.width == 50
        assert img.height == 100

    def test_output_has_no_leftover_orientation_tag(self):
        raw = _jpeg_with_orientation(100, 50, orientation=6)
        result = normalize_image(raw)
        img = Image.open(io.BytesIO(result))
        assert img.getexif().get(274) is None

    def test_image_without_orientation_tag_unaffected(self):
        img = Image.new('RGB', (100, 50), color='blue')
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        result = normalize_image(buf.getvalue())
        out = Image.open(io.BytesIO(result))
        assert out.width == 100
        assert out.height == 50

    def test_converts_to_webp(self):
        raw = _jpeg_with_orientation(100, 50, orientation=6)
        result = normalize_image(raw)
        img = Image.open(io.BytesIO(result))
        assert img.format == 'WEBP'

    def test_empty_bytes_returns_empty(self):
        assert normalize_image(b'') == b''
