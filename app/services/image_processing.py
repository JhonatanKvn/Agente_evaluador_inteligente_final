import os
from io import BytesIO
from typing import Tuple

from PIL import Image


def prepare_image_for_ocr(raw_bytes: bytes, original_filename: str) -> Tuple[bytes, str]:
    """
    Deja la imagen lista para OCR.Space:
    - lado mayor máximo de 1000px
    - escala de grises
    - JPG calidad 70
    - intenta quedar por debajo de 1MB
    """
    with Image.open(BytesIO(raw_bytes)) as img:
        img = img.convert("L")
        img.thumbnail((1000, 1000), Image.Resampling.LANCZOS)

        out = BytesIO()
        img.save(out, format="JPEG", quality=70, optimize=True)
        data = out.getvalue()

        while len(data) > 1024 * 1024:
            w, h = img.size
            if w <= 300 or h <= 300:
                break
            img = img.resize((int(w * 0.9), int(h * 0.9)), Image.Resampling.LANCZOS)
            out = BytesIO()
            img.save(out, format="JPEG", quality=70, optimize=True)
            data = out.getvalue()

    base = os.path.splitext(original_filename or "imagen")[0]
    return data, f"{base}_ocr.jpg"


