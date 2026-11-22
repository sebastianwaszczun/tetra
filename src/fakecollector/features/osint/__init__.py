"""Moduł OSINT – ślady infrastrukturalne stron ze scamowych reklam.

    from fakecollector.features.osint import analyze_files, analyze_path
    rec = analyze_path("data/incydenty/36")         # folder incydentu (HAR + HTML + ...)
    rec["pivots"]                                    # cechy do łączenia w kampanie
"""
from __future__ import annotations

from pathlib import Path

from .capture import Capture, load_bytes, load_dir
from .pipeline import SCHEMA_VERSION, analyze_capture

__all__ = ["analyze_files", "analyze_path", "SCHEMA_VERSION"]


def analyze_files(files: list[tuple[str, bytes]], url: str | None = None, enrich: bool = True,
                  ct: bool | None = None, render: bool = False) -> dict:
    cap = Capture()
    for name, data in files:
        sub = load_bytes(data, name)
        if sub is None:
            cap.notes.append(f"pominięto nieobsługiwany plik: {name}")
        else:
            cap.merge(sub)
    if url:
        cap.merge(_live(url, render))
        cap.page_url = cap.page_url or url
    return analyze_capture(cap, do_enrich=enrich, do_ct=ct)


def analyze_path(path: str | Path, enrich: bool = True, ct: bool | None = None) -> dict:
    p = Path(path)
    cap = load_dir(p) if p.is_dir() else (load_bytes(p.read_bytes(), p.name) or Capture())
    return analyze_capture(cap, do_enrich=enrich, do_ct=ct)


def _live(url: str, render: bool) -> Capture:
    from .live import fetch_browser, fetch_http
    if render:
        try:
            return fetch_browser(url)
        except Exception as e:  # noqa: BLE001
            cap = fetch_http(url)
            cap.notes.append(f"render niedostępny ({type(e).__name__}), użyto HTTP")
            return cap
    return fetch_http(url)
