"""Uruchomienie wszystkich modułów cech na jednym materiale (Incident z preprocessingu).

Kolejność ma znaczenie:
  1. audio i wideo – transkrypcja i napisy z filmu trafiają do teczki jako tekst,
  2. tekst         – dostaje najlepszy tekst (strona > … > transkrypcja > OCR),
  3. OSINT         – HAR, HTML, adresy,
  4. obraz         – zrzuty, obrazy ze stron, klatki filmów.
Awaria jednego modułu nie zatrzymuje pozostałych: trafia do `errors`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ModuleResults:
    text: dict[str, Any] | None = None
    av: dict[str, Any] | None = None
    osint: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
    errors: dict[str, str] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


def _run(results: ModuleResults, name: str, fn):
    t0 = time.time()
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 – moduł nie może zabrać wyniku pozostałych
        log.exception("Moduł %s", name)
        results.errors[name] = f"{exc.__class__.__name__}: {exc}"
        return None
    finally:
        results.timings[name] = round(time.time() - t0, 2)


def analyze_incident(inc: Any, *, text: bool = True, osint: bool = True, enrich: bool = False,
                     av: bool = True, asr: bool = True, image: bool = True) -> ModuleResults:
    """Cechy ze wszystkich modułów dla jednego materiału (pełne, z wektorami)."""
    r = ModuleResults()

    if av and inc.for_module("av"):
        from fakecollector.features import av as av_module

        def _av():
            feats = av_module.extract(inc, asr=asr)
            av_module.attach_to_incident(inc, feats)
            return feats
        r.av = _run(r, "av", _av)
    elif av:
        r.skipped["av"] = "brak nagrań"

    if text and inc.text.strip():
        from fakecollector.features import text as text_module
        origin = inc.text_origin[0] if inc.text_origin else "html"
        r.text = _run(r, "text", lambda: text_module.analyze_text(inc.text, origin=origin, title=inc.title).to_dict())
    elif text:
        r.skipped["text"] = "brak tekstu"

    if osint and inc.for_module("osint"):
        from fakecollector.ingest import run_osint

        def _osint():
            rec = run_osint(inc, enrich=enrich)
            if rec is None and inc.errors and "OSINT" in inc.errors[-1]:
                raise RuntimeError(inc.errors.pop())    # błąd zapisujemy raz – w wyniku modułu
            return rec
        r.osint = _run(r, "osint", _osint)
    elif osint:
        r.skipped["osint"] = "brak adresu, HAR-a i strony"

    if image and inc.for_module("image"):
        # adapter kolegi z modułu obrazu (features/image/potok.py); wektory potrzebne do bazy
        from fakecollector.features.image import potok
        r.image = _run(r, "image", lambda: potok.extract(inc, wektory=True))
    elif image:
        r.skipped["image"] = "brak obrazów"
    return r
