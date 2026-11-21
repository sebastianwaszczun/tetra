"""Odczyt plików HAR: lista zapytań, strona główna, łańcuch przekierowań.

Pełna analiza infrastruktury (trackery, skrypty, hosty) należy do modułu OSINT.
Tu wyciągamy tylko to, czego potrzebuje preprocessing: adres strony, jej HTML
(dla modułu tekstu, gdy brak zapisanej strony) i przekierowania.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit


def load_har(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"{path}: to nie jest poprawny plik HAR")


def _body(entry: dict[str, Any]) -> str:
    content = entry.get("response", {}).get("content", {}) or {}
    text = content.get("text") or ""
    if text and content.get("encoding") == "base64":
        try:
            text = base64.b64decode(text).decode("utf-8", "replace")
        except Exception:
            return ""
    return text


def entries(har: dict[str, Any]) -> list[dict[str, Any]]:
    """Uproszczona lista zapytań z HAR-a."""
    out = []
    for e in har.get("log", {}).get("entries", []):
        req, resp = e.get("request", {}), e.get("response", {})
        content = resp.get("content", {}) or {}
        out.append({
            "url": req.get("url", ""),
            "method": req.get("method", ""),
            "status": resp.get("status"),
            "mime": (content.get("mimeType") or "").split(";")[0].strip().lower(),
            "redirect": resp.get("redirectURL") or "",
            "server_ip": e.get("serverIPAddress"),
            "type": e.get("_resourceType"),
            "started": e.get("startedDateTime"),
            "size": content.get("size"),
        })
    return out


def main_document(har: dict[str, Any]) -> tuple[str | None, str]:
    """Adres i HTML strony głównej: odpowiedź 200 typu text/html, najlepiej typu document, największa."""
    best: tuple[int, str, str] | None = None
    for e in har.get("log", {}).get("entries", []):
        resp = e.get("response", {})
        mime = ((resp.get("content") or {}).get("mimeType") or "").lower()
        if resp.get("status") != 200 or "html" not in mime:
            continue
        body = _body(e)
        if not body:
            continue
        score = len(body) + (10**9 if e.get("_resourceType") == "document" else 0)
        if best is None or score > best[0]:
            best = (score, e.get("request", {}).get("url", ""), body)
    return (best[1], best[2]) if best else (None, "")


def first_navigation(har: dict[str, Any]) -> str | None:
    """Pierwszy adres, który przeglądarka otworzyła (zwykle link z reklamy)."""
    for e in har.get("log", {}).get("entries", []):
        url = e.get("request", {}).get("url", "")
        if url.startswith("http") and (e.get("_resourceType") in (None, "document")):
            return url
    return None


def redirect_chain(har: dict[str, Any]) -> list[str]:
    """Łańcuch przekierowań HTTP (3xx) od pierwszej nawigacji do strony końcowej."""
    by_url: dict[str, str] = {}
    for e in har.get("log", {}).get("entries", []):
        resp = e.get("response", {})
        status = resp.get("status") or 0
        if 300 <= status < 400:
            src = e.get("request", {}).get("url", "")
            target = resp.get("redirectURL") or next(
                (h.get("value", "") for h in resp.get("headers", []) if h.get("name", "").lower() == "location"), "")
            if src and target:
                by_url[src] = urljoin(src, target)
    start = first_navigation(har)
    if not start:
        return []
    chain = [start]
    while chain[-1] in by_url and len(chain) < 20:
        nxt = by_url[chain[-1]]
        if nxt in chain:
            break
        chain.append(nxt)
    return chain


def hosts(har: dict[str, Any]) -> list[str]:
    return sorted({urlsplit(e.get("request", {}).get("url", "")).hostname or ""
                   for e in har.get("log", {}).get("entries", [])} - {""})
