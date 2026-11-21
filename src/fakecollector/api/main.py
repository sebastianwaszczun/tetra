"""API FakeCollector (FastAPI).

Uruchamiane przez usługę `api` w docker-compose:
    uvicorn fakecollector.api.main:app --host 0.0.0.0 --port 8000

Dokumentacja interaktywna: http://localhost:8000/docs
"""
from __future__ import annotations

import json
import os
from typing import Any, Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from fakecollector.features import text as text_module
from fakecollector.ingest import ingest_upload, run_osint

app = FastAPI(
    title="FakeCollector API",
    version="0.1.0",
    description="Analiza podejrzanych reklam i łączenie ich w kampanie (HackNation, NASK).",
)

# Front w React (Vite) działa na innym porcie, więc przeglądarka potrzebuje zgody CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

Origin = Literal["input", "html", "ocr", "transcript", "adlib", "json"]


class TextAnalyzeRequest(BaseModel):
    text: Optional[str] = Field(None, description="Tekst materiału, np. treść SMS-a, maila albo reklamy.")
    html: Optional[str] = Field(None, description="Kod HTML strony. Tekst zostanie z niego wyciągnięty.")
    origin: Optional[Origin] = Field(
        None, description="Źródło tekstu. Domyślnie 'html' dla pola html i 'input' dla pola text.")
    title: str = Field("", description="Tytuł materiału, jeśli jest znany.")
    with_vectors: bool = Field(
        False, description="Czy zwrócić pełne embeddingi i podpisy MinHash (duża odpowiedź).")


class TextAnalyzeResponse(BaseModel):
    features: dict[str, Any] = Field(description="Cechy tekstu z modułu tekstu (opis pól w TEXT.md).")
    page: Optional[dict[str, Any]] = Field(
        None, description="Metadane strony: tytuł, opis, nagłówki, linki wychodzące (tylko dla pola html).")


@app.post("/text/analyze", response_model=TextAnalyzeResponse, tags=["moduł tekstu"],
          summary="Cechy tekstu materiału")
def analyze_text(req: TextAnalyzeRequest) -> TextAnalyzeResponse:
    """Zwraca cechy narracji: osoby, marki, kwoty, znaczniki perswazji, kategorię narracji,
    słowa kluczowe, szkielet tekstu oraz (opcjonalnie) embeddingi i podpisy MinHash.

    Podaj pole `text` albo `html`. Pierwsze wywołanie trwa dłużej, bo ładuje model embeddingów.
    """
    if not (req.text and req.text.strip()) and not (req.html and req.html.strip()):
        raise HTTPException(status_code=422, detail="Podaj niepusty tekst w polu 'text' albo kod strony w polu 'html'.")

    page = None
    if req.html and req.html.strip():
        text, page = text_module.extract_text_from_html(req.html)
        origin = req.origin or "html"
        title = req.title or page.get("title", "")
    else:
        text = req.text or ""
        origin = req.origin or "input"
        title = req.title

    features = text_module.analyze_text(text, origin=origin, title=title)
    return TextAnalyzeResponse(features=features.to_dict(with_vectors=req.with_vectors), page=page)


# ----------------------------------------------------------------------------- preprocessing

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))


@app.post("/analyze", tags=["preprocessing"], summary="Przyjęcie materiału z frontu (tekst/link + pliki)")
def analyze(
    text: Optional[str] = Form(None, description="Tekst albo link (SMS, treść reklamy, adres strony)."),
    files: list[UploadFile] = File(default=[], description="Zrzuty, wideo, HAR, HTML, JSON, ZIP…"),
    fetch: bool = Form(True, description="Czy pobrać stronę z podanego linku (w kontenerze, Playwright)."),
    text_features: bool = Form(True, description="Czy od razu policzyć cechy tekstu."),
    osint: bool = Form(True, description="Moduł OSINT z pełnym wzbogacaniem (DNS, RDAP/WHOIS, ASN, TLS, crt.sh). "
                                         "Wydłuża odpowiedź o kilka–kilkanaście sekund."),
) -> dict[str, Any]:
    """Preprocessing: rozkłada materiał na składniki i mówi, który moduł co dostaje.

    Zwraca też pola starego stuba (`text`, `files_count`, `files`), żeby front działał bez zmian.
    Funkcja jest synchroniczna – FastAPI uruchamia ją w osobnym wątku (Playwright i OCR blokują).
    """
    text = (text or "").strip() or None
    if not text and not files:
        raise HTTPException(400, "Podaj tekst albo co najmniej jeden plik.")

    received, payload, total = [], [], 0
    for f in files:
        data = f.file.read(MAX_UPLOAD_BYTES + 1)
        total += len(data)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Pliki są za duże (limit MAX_UPLOAD_BYTES).")
        received.append({"name": f.filename, "content_type": f.content_type, "size": len(data)})
        payload.append((f.filename or "plik", data))

    inc = ingest_upload(text=text, files=payload, fetch=fetch)
    result: dict[str, Any] = {
        "text": text is not None,
        "files_count": len(received),
        "files": received,
        "incident": inc.to_dict(with_text=False),
        "routes": inc.routes(),
        "text_preview": inc.text[:1000],
    }
    if text_features and inc.text.strip():
        result["text_features"] = text_module.extract(inc)
    if osint:
        try:
            result["osint"] = run_osint(inc, enrich=True)
        except Exception as exc:  # awaria OSINT nie może zabrać reszty wyniku
            result["osint"] = None
            result["osint_error"] = f"{exc.__class__.__name__}: {exc}"
    _dump_response(result, inc.id)   # TYMCZASOWE: podgląd odpowiedzi w pliku na pulpicie
    return result


def _dump_response(result: dict[str, Any], incident_id: str) -> None:
    """TYMCZASOWE: zapisuje pełną odpowiedź /analyze do ~/Desktop/analyze_<czas>_<id>.txt."""
    from datetime import datetime
    from pathlib import Path
    try:
        out = Path(os.environ.get("ANALYZE_DUMP_DIR", Path.home() / "Desktop"))
        out.mkdir(parents=True, exist_ok=True)
        name = f"analyze_{datetime.now():%Y%m%d_%H%M%S}_{incident_id}.txt"
        (out / name).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass   # zapis podglądu nie może zepsuć odpowiedzi
