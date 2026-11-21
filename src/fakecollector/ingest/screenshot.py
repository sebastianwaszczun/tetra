"""OCR zrzutów ekranu i zdjęć (Tesseract, język polski i angielski) oraz szukanie linków."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

OCR_LANG = os.environ.get("OCR_LANG", "pol+eng")
TILE_HEIGHT = 2500      # wysokie zrzuty całej strony czytamy w kawałkach
MAX_TILES = 12          # najwyżej ~30 000 px wysokości

# Domeny z OCR: tesseract często wstawia spacje wokół kropek ("civicdaybook . live")
OCR_URL_RE = re.compile(
    r"(?:https?://\S+|www\.\S+|\b[a-z0-9][a-z0-9-]{1,62}\s?\.\s?(?:pl|com|net|org|eu|info|shop|online|xyz|"
    r"top|site|store|app|io|me|ly|live|click|pro|icu|life|biz|club)\b(?:/\S*)?)", re.I)


def ocr_image(path: str | Path, lang: str = OCR_LANG) -> str:
    """Tekst odczytany z obrazu. Pusty napis, gdy obrazu nie da się odczytać."""
    import pytesseract
    from PIL import Image, ImageOps

    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img).convert("L")
    except Exception as exc:
        log.warning("Nie można otworzyć obrazu %s: %s", path, exc)
        return ""
    if img.width < 1000:  # małe zrzuty powiększamy – OCR czyta wtedy dużo lepiej
        scale = 1000 / img.width
        img = img.resize((1000, int(img.height * scale)))
    parts = []
    for i, top in enumerate(range(0, img.height, TILE_HEIGHT)):
        if i >= MAX_TILES:
            break
        tile = img.crop((0, top, img.width, min(top + TILE_HEIGHT, img.height)))
        try:
            parts.append(pytesseract.image_to_string(tile, lang=lang))
        except Exception as exc:
            log.warning("OCR nie powiódł się dla %s: %s", path, exc)
            break
    return "\n".join(p.strip() for p in parts if p.strip())


def find_urls(text: str) -> list[str]:
    """Adresy stron w tekście (także z OCR, gdzie wokół kropki bywa spacja)."""
    found = []
    for m in OCR_URL_RE.finditer(text):
        url = re.sub(r"\s", "", m.group(0)).rstrip(".,;:)]}>\"'")
        if url.lower() not in {u.lower() for u in found}:
            found.append(url)
    return found
