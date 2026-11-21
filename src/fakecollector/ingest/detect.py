"""Rozpoznanie rodzaju wejścia (odpowiednik front/src/resolveInput.js po stronie backendu)."""
from __future__ import annotations

import re
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".heif", ".avif", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".3gp"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".aac", ".flac"}
HTML_EXT = {".html", ".htm", ".mhtml", ".xhtml"}
TEXT_EXT = {".txt", ".eml", ".md"}

URL_ONLY = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:[a-z0-9¡-￿](?:[a-z0-9¡-￿-]*[a-z0-9¡-￿])?\.)+"
    r"[a-z¡-￿]{2,}(?::\d{2,5})?(?:[/?#]\S*)?$", re.I)


def detect_input(value: str | Path) -> str:
    """Zwraca: folder, archive, image, video, audio, har, html, json, textfile, url albo text."""
    if isinstance(value, Path) or (isinstance(value, str) and len(value) < 1024 and "\n" not in value):
        p = Path(value)
        try:
            exists = p.exists()
        except OSError:
            exists = False
        if exists:
            if p.is_dir():
                return "folder"
            ext = p.suffix.lower() or (p.name.lower() if p.name.startswith(".") else "")
            if ext == ".zip":
                return "archive"
            if ext == ".har":
                return "har"
            if ext in IMAGE_EXT:
                return "image"
            if ext in VIDEO_EXT:
                return "video"
            if ext in AUDIO_EXT:
                return "audio"
            if ext in HTML_EXT:
                return "html"
            if ext == ".json":
                return "json"
            if ext in TEXT_EXT:
                return "textfile"
            return "file"
    text = str(value).strip()
    if text and not re.search(r"\s", text) and URL_ONLY.match(text):
        return "url"
    return "text"
