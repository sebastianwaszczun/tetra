"""API FakeCollector (FastAPI).

Uruchamiane przez usługę `api` w docker-compose:
    uvicorn fakecollector.api.main:app --host 0.0.0.0 --port 8000

Dokumentacja interaktywna: http://localhost:8000/docs
"""
from __future__ import annotations

import os
from typing import Any, Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from fakecollector.features import av as av_module
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


class AVCompareRequest(BaseModel):
    a: str = Field(description="Ścieżka do pliku albo folderu w kontenerze, np. data/DANE/5")
    b: str = Field(description="Druga ścieżka")
    asr: bool = Field(False, description="Czy robić transkrypcję (wolne).")


@app.post("/av/compare", tags=["moduł audio i wideo"], summary="Porównanie dwóch nagrań (głos, dźwięk, obraz)")
def av_compare(req: AVCompareRequest) -> dict[str, Any]:
    """Do testów: porównuje dwa pliki albo foldery z danymi zamontowanymi w kontenerze."""
    from pathlib import Path
    feats = []
    data_root = Path(os.environ.get("DATA_DIR", "data")).resolve()
    for p in (req.a, req.b):
        path = Path(p).resolve()
        if not path.is_relative_to(data_root):   # tylko dane projektu, nie dowolne pliki kontenera
            raise HTTPException(400, f"Ścieżka musi być w folderze {data_root}")
        if not path.exists():
            raise HTTPException(404, f"Nie ma takiej ścieżki: {p}")
        f = av_module.analyze_path(path, asr=req.asr)
        if not f:
            raise HTTPException(422, f"Brak plików wideo/audio w {p}")
        feats.append(f)
    return {"compare": av_module.compare(*feats),
            "a": av_module.to_dict(feats[0], with_vectors=False),
            "b": av_module.to_dict(feats[1], with_vectors=False)}


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


ImageRole = Literal["kreacja", "obrazek_strony", "klatka", "zrzut_caly"]


def _image_module():
    """Import przy pierwszym użyciu: brak zależności obrazu (opencv, imagehash…) nie wyłącza całego API."""
    from fakecollector.features.image import potok
    return potok


def _read_image(f: UploadFile, limit: int) -> tuple[str, bytes]:
    data = f.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Obraz {f.filename} jest za duży (limit {limit // 1_000_000} MB).")
    if not data:
        raise HTTPException(400, f"Pusty plik {f.filename}.")
    return f.filename or "obraz", data


@app.post("/image/analyze", tags=["moduł obrazu"], summary="Cechy jednego obrazu (hashe, wygląd, logo, twarze, QR)")
def image_analyze(
    file: UploadFile = File(..., description="Obraz: PNG, JPEG, WEBP, GIF, BMP, HEIC, AVIF."),
    rola: ImageRole = Form("kreacja", description="Czym jest obraz: kreacja reklamy, obrazek ze strony, klatka, zrzut."),
    with_vectors: bool = Form(False, description="Czy zwrócić wektory CLIP i twarzy (do bazy, duża odpowiedź)."),
) -> dict[str, Any]:
    """Zwraca skrót do porównań (sha256, pHash, osoby, logo, QR) i pełne cechy w polu `cechy`.

    Pierwsze wywołanie ładuje modele z /models/obraz (CLIP, SigLIP2, ArcFace…). Brak wag nie jest błędem:
    trafia do `cechy.braki`, a hashe i QR są liczone dalej.
    """
    potok = _image_module()
    name, data = _read_image(file, potok.limit_bajtow())
    result = potok.analizuj_plik(data, name, rola, with_vectors)
    if result["sha256"] is None:
        powod = next((b["powod"] for b in result["cechy"]["braki"] if b["skladnik"] == "wczytywanie"), "nieznany format")
        raise HTTPException(415, f"{name}: {powod}")
    return result


@app.post("/image/compare", tags=["moduł obrazu"], summary="Porównanie dwóch obrazów (dowody z siłą)")
def image_compare(
    a: UploadFile = File(..., description="Pierwszy obraz."),
    b: UploadFile = File(..., description="Drugi obraz."),
) -> dict[str, Any]:
    """Dowody: identyczny plik, duplikat kreacji, ten sam kadr, podobny wygląd, wspólny szablon,
    ta sama osoba / twarz, wspólne logo, wspólny QR – każdy z siłą (silny/średni/słaby) i uzasadnieniem."""
    potok = _image_module()
    limit = potok.limit_bajtow()
    return potok.porownaj_pliki(_read_image(a, limit), _read_image(b, limit))


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
    av: bool = Form(True, description="Czy analizować wideo i audio (głos, dźwięk, obraz, napisy)."),
    asr: bool = Form(True, description="Czy robić transkrypcję mowy (najwolniejsza część, ~1 min na film)."),
    image: bool = Form(True, description="Moduł obrazu: zrzuty, obrazy ze strony i klatki wideo "
                                         "(hashe, wygląd, logo, twarze lektora/celebryty, QR)."),
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
    if av and inc.for_module("av"):
        try:
            av_feats = av_module.extract(inc, asr=asr)
            av_module.attach_to_incident(inc, av_feats)   # transkrypcja -> tekst materiału
            result["av_features"] = av_module.to_dict(av_feats, with_vectors=False)
            result["incident"] = inc.to_dict(with_text=False)
            result["text_preview"] = inc.text[:1000]
        except Exception as exc:  # awaria modułu AV nie może zabrać reszty wyniku
            result["av_features"] = None
            result["av_error"] = f"{exc.__class__.__name__}: {exc}"
    if image and inc.for_module("image"):
        try:
            result["image_features"] = _image_module().extract(inc)
        except Exception as exc:
            result["image_features"] = None
            result["image_error"] = f"{exc.__class__.__name__}: {exc}"
    if text_features and inc.text.strip():
        origin = inc.text_origin[0] if inc.text_origin else "html"
        feats = text_module.analyze_text(inc.text, origin=origin, title=inc.title)
        result["text_features"] = feats.to_dict(with_vectors=False)   # wektory idą do bazy, nie do frontu
    if osint:
        try:
            result["osint"] = run_osint(inc, enrich=True)
        except Exception as exc:
            result["osint"] = None
            result["osint_error"] = f"{exc.__class__.__name__}: {exc}"
    return result
