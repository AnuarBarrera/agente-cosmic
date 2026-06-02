import io

import numpy as np
from PIL import Image

_CHROMA_KEY_COLOR = (255, 0, 255)  # magenta
_CHROMA_TOLERANCE = 40


def remove_chroma_key(
    image_bytes: bytes,
    key_color: tuple = _CHROMA_KEY_COLOR,
    tolerance: int = _CHROMA_TOLERANCE,
) -> Image.Image:
    """Remove solid magenta background, returning RGBA PIL Image."""
    img = Image.open(io.BytesIO(image_bytes)).convert('RGBA')
    arr = np.array(img, dtype=np.int32)
    kr, kg, kb = key_color
    dist = np.sqrt(
        (arr[:, :, 0] - kr) ** 2
        + (arr[:, :, 1] - kg) ** 2
        + (arr[:, :, 2] - kb) ** 2
    )
    arr[:, :, 3] = np.where(dist < tolerance, 0, arr[:, :, 3])
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')


def composite_layers(
    background_bytes: bytes,
    text_asset_bytes: bytes,
    x: float,
    y: float,
    width: float,
    rotation_deg: float = 0.0,
) -> bytes:
    """Composite text asset (chroma-keyed) onto background at relative coords.

    x, y, width are in [0, 1] relative to background dimensions.
    Returns PNG bytes.
    Output is always RGB (alpha flattened).
    """
    bg = Image.open(io.BytesIO(background_bytes)).convert('RGBA')
    bw, bh = bg.size

    text_layer = remove_chroma_key(text_asset_bytes)

    target_w = max(1, int(width * bw))
    orig_w, orig_h = text_layer.size
    scale = target_w / orig_w if orig_w > 0 else 1.0
    target_h = max(1, int(orig_h * scale))
    text_layer = text_layer.resize((target_w, target_h), Image.LANCZOS)

    if rotation_deg != 0.0:
        text_layer = text_layer.rotate(-rotation_deg, expand=True, resample=Image.BICUBIC)

    paste_x = int(x * bw)
    paste_y = int(y * bh)
    # Clamp so the pasted layer stays within canvas bounds
    paste_x = max(0, min(paste_x, bw - text_layer.width))
    paste_y = max(0, min(paste_y, bh - text_layer.height))

    result = bg.copy()
    result.paste(text_layer, (paste_x, paste_y), text_layer)

    out = io.BytesIO()
    result.convert('RGB').save(out, format='PNG', optimize=True)
    return out.getvalue()
