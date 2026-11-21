"""HTTP API modułu OSINT.

Uruchomienie (z katalogu modules/):
    uvicorn osint.main:app --host 0.0.0.0 --port 8010

POST /analyze  (multipart/form-data) – JEDNO wejście na wywołanie:
    file    – jeden plik: .har .html/.htm .mhtml/.mht .json (manifest/yt-dlp) .zip (folder incydentu)
    url     – albo adres reklamy/strony (pobranie na żywo)
    enrich  – true/false: DNS, RDAP/WHOIS, ASN, TLS, crt.sh (domyślnie true)
    ct      – true/false: Certificate Transparency (crt.sh), wolne (domyślnie z OSINT_ENABLE_CT)
    render  – true/false: render w Chromium (Playwright) zamiast zwykłego HTTP
GET  /analyze?url=...   – skrót dla samego URL
GET  /pivots/weights    – tabela rodzajów pivotów, wag i opisów (dla korelatora)
GET  /health
"""
from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile

from . import analyze_url, config
from .capture import load_bytes
from .pipeline import analyze_capture
from .live import UnsafeTarget
from .pivots import WEIGHTS
from .pipeline import SCHEMA_VERSION

app = FastAPI(title="FakeCollector – moduł OSINT", version="1.0",
              description="Ślady infrastrukturalne (WHOIS/RDAP, DNS, IP/ASN, TLS, nagłówki, trackery, kit, "
                          "łańcuch URL) wyciągane z URL / HAR / HTML / MHTML i zamieniane na pivoty do korelacji.")


@app.get("/health")
def health():
    return {"status": "ok", "schema": SCHEMA_VERSION}


@app.get("/pivots/weights")
def pivot_weights():
    return {k: {"weight": w, "scope": s, "description": d} for k, (w, s, d) in WEIGHTS.items()}


SUPPORTED = (".har", ".html", ".htm", ".mhtml", ".mht", ".json", ".zip")


@app.post("/analyze")
def analyze(
    file: Annotated[UploadFile | None, File()] = None,
    url: Annotated[str | None, Form()] = None,
    enrich: Annotated[bool, Form()] = True,
    ct: Annotated[bool | None, Form()] = None,
    render: Annotated[bool, Form()] = False,
):
    """Jedno wejście na wywołanie: albo `file`, albo `url`."""
    url = (url or "").strip() or None
    if bool(file) == bool(url):
        raise HTTPException(400, "Podaj dokładnie jedno: 'file' (jeden plik) albo 'url'.")
    try:
        if url:
            return analyze_url(url, enrich=enrich, ct=ct, render=render)
        data = file.file.read(config.MAX_UPLOAD_BYTES + 1)
        if len(data) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Plik za duży (OSINT_MAX_UPLOAD_BYTES).")
        name = file.filename or "upload"
        cap = load_bytes(data, name)          # rozpoznanie typu po rozszerzeniu / treści
        if cap is None:
            raise HTTPException(415, f"Nieobsługiwany typ pliku '{name}'. Obsługiwane: {', '.join(SUPPORTED)}")
        return analyze_capture(cap, do_enrich=enrich, do_ct=ct)
    except UnsafeTarget as e:
        raise HTTPException(400, f"Niedozwolony cel: {e}") from e
    except (ValueError, KeyError) as e:
        raise HTTPException(422, f"Nie udało się przetworzyć materiału: {type(e).__name__}: {e}") from e


@app.get("/analyze")
def analyze_get(url: str = Query(...), enrich: bool = True, ct: bool | None = None, render: bool = False):
    try:
        return analyze_url(url, enrich=enrich, ct=ct, render=render)
    except UnsafeTarget as e:
        raise HTTPException(400, f"Niedozwolony cel: {e}") from e
