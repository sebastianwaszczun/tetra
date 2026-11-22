"""API FakeCollector (FastAPI).

Uruchamiane przez usługę `api` w docker-compose:
    uvicorn fakecollector.api.main:app --host 0.0.0.0 --port 8000

Dokumentacja interaktywna: http://localhost:8000/docs
"""
from __future__ import annotations

import os
import threading
from typing import Any, Literal, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from fakecollector.features import av as av_module
from fakecollector.features import text as text_module
from fakecollector import graph as graph_module
from fakecollector import grouping as grouping_module
from fakecollector import record as record_module
from fakecollector.ingest import ingest_upload

app = FastAPI(
    title="FakeCollector API",
    version="0.1.0",
    description="Analiza podejrzanych reklam i łączenie ich w kampanie (HackNation, NASK).",
)

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
    features: dict[str, Any] = Field(description="Cechy tekstu z modułu tekstu (opis pól w features/text/README.md).")
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
        if not path.is_relative_to(data_root):
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


_skaner_obj = None
_skaner_blokada = threading.Lock()


def _skaner():
    """Import i połączenie z bazą przy pierwszym użyciu: brak tokenu albo bazy nie wyłącza reszty API."""
    global _skaner_obj
    with _skaner_blokada:
        if _skaner_obj is None:
            from fakecollector.adlib import Skaner
            _skaner_obj = Skaner.z_srodowiska()
        return _skaner_obj


@app.get("/adlib/status", tags=["skaner Biblioteki Reklam"], summary="Stan skanera: ostatnie skany, liczby, token")
def adlib_status() -> dict[str, Any]:
    return _skaner().status()


@app.get("/adlib/ads", tags=["skaner Biblioteki Reklam"], summary="Reklamy znalezione przez skaner")
def adlib_ads(status: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    """Statusy: kandydat, pominieta, w_analizie, przeanalizowana, blad. Przy każdej reklamie wynik filtra,
    powody i podsumowanie analizy z id zgłoszenia."""
    return _skaner().magazyn.reklamy(status, min(max(limit, 1), 500))


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
    save: bool = Form(True, description="Czy zapisać rekord materiału w bazie i poszukać podobnych."),
    group: bool = Form(True, description="Czy przypisać materiał do kampanii (wymaga save)."),
) -> dict[str, Any]:
    """Preprocessing -> moduły (AV, tekst, OSINT, obraz) -> rekord -> zapis -> kandydaci -> kampania.

    Na początku odpowiedzi jest opis tego, co przyszło (`text`, `files_count`, `files`).
    Funkcja jest synchroniczna – FastAPI uruchamia ją w osobnym wątku (Playwright, OCR i modele blokują).
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
    out = record_module.process_incident(inc, save=save, text=text_features, osint=osint, enrich=True,
                                         av=av, asr=asr, image=image)
    res, rec = out["_results"], out["_record"]
    result: dict[str, Any] = {
        "text": text is not None,
        "files_count": len(received),
        "files": received,
        "material_id": rec.id,
        "saved": out["saved"],
        "incident": inc.to_dict(with_text=False),
        "routes": inc.routes(),
        "modules": rec.summary["modules"],
        "text_preview": inc.text[:1000],
        "candidates": out["candidates"],
    }
    if out.get("record_error"):
        result["record_error"] = out["record_error"]
    for name, key in (("text", "text_features"), ("av", "av_features"), ("image", "image_features")):
        data = getattr(res, name)
        if data is not None:
            result[key] = record_module.strip_vectors(data)
        if name in res.errors:
            result[key] = None
            result[f"{name}_error"] = res.errors[name]
    if osint:
        result["osint"] = res.osint
        if "osint" in res.errors:
            result["osint_error"] = res.errors["osint"]
    if save and group and out["saved"]:
        try:
            result["assignment"] = grouping_module.assign_material(rec.id)
        except Exception as exc:  # noqa: BLE001
            result["assignment"] = None
            result["assignment_error"] = f"{exc.__class__.__name__}: {exc}"
    return result


@app.get("/materials/{material_id}", tags=["rekord materiału"], summary="Rekord materiału (bez wektorów)")
def material(material_id: str) -> dict[str, Any]:
    rec = record_module.load_record(material_id)
    if rec is None:
        raise HTTPException(404, "Nie ma takiego materiału.")
    return record_module.front_view(rec)


IMAGE_KINDS = {"screenshot": "zrzut", "image": "obraz ze strony", "frame": "klatka filmu"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"}


def _material_images(material_id: str) -> list[dict[str, Any]]:
    from pathlib import Path
    rec = record_module.load_record(material_id)
    if rec is None:
        raise HTTPException(404, "Nie ma takiego materiału.")
    out = []
    for n, a in enumerate((rec.summary.get("incident") or {}).get("artifacts") or []):
        path = a.get("path")
        if a.get("kind") in IMAGE_KINDS and path and Path(path).suffix.lower() in IMAGE_SUFFIXES:
            video = (a.get("meta") or {}).get("video")
            out.append({"n": n, "kind": a["kind"], "label": IMAGE_KINDS[a["kind"]], "name": Path(path).name,
                        "source": a.get("source"), "video": Path(video).name if video else None, "_path": path})
    return out


@app.get("/materials/{material_id}/images", tags=["rekord materiału"],
         summary="Zrzuty, obrazy ze strony i klatki filmów materiału")
def material_images(material_id: str) -> dict[str, Any]:
    items = [{k: v for k, v in i.items() if k != "_path"} for i in _material_images(material_id)]
    return {"material_id": material_id, "count": len(items), "items": items}


@app.get("/materials/{material_id}/images/{n}", tags=["rekord materiału"], summary="Plik obrazu materiału")
def material_image(material_id: str, n: int) -> FileResponse:
    from pathlib import Path
    item = next((i for i in _material_images(material_id) if i["n"] == n), None)
    if item is None:
        raise HTTPException(404, "Nie ma takiego obrazu.")
    path = Path(item["_path"]).resolve()
    data_root = Path(os.environ.get("DATA_DIR", "data")).resolve()
    if not path.is_relative_to(data_root) or not path.is_file():
        raise HTTPException(404, "Plik obrazu niedostępny.")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


class DecisionRequest(BaseModel):
    decision: Literal["potwierdz", "odrzuc", "cofnij"] = Field(
        description="potwierdz – materiał należy do kampanii; odrzuc – nie należy (dostaje własną kampanię); "
                    "cofnij – usuń decyzję i przypisz materiał od nowa.")
    note: str = Field("", description="Notatka analityka (opcjonalnie).")


@app.post("/materials/{material_id}/decision", tags=["kampanie"], summary="Decyzja analityka o przynależności")
def material_decision(material_id: str, req: DecisionRequest = Body(...)) -> dict[str, Any]:
    """Decyzja dotyczy par materiałów, więc przetrwa przeliczenie kampanii (`python -m fakecollector.grouping rebuild`)."""
    from fakecollector.grouping.decisions import DecisionError, decide
    try:
        return decide(material_id, req.decision, req.note)
    except KeyError:
        raise HTTPException(404, "Nie ma takiego materiału.") from None
    except DecisionError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/campaigns", tags=["kampanie"], summary="Lista kampanii")
def campaigns(min_size: int = 1) -> dict[str, Any]:
    try:
        items = grouping_module.list_campaigns(min_size=min_size)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Baza niedostępna: {exc.__class__.__name__}: {exc}") from exc
    return {"count": len(items), "items": items}


@app.get("/campaigns/{campaign_id}", tags=["kampanie"], summary="Kampania: materiały, powiązania, przesłanki")
def campaign(campaign_id: int) -> dict[str, Any]:
    c = grouping_module.get_campaign(campaign_id)
    if c is None:
        raise HTTPException(404, "Nie ma takiej kampanii.")
    return c


@app.get("/compare", tags=["kampanie"], summary="Porównanie dwóch zapisanych materiałów (wszystkie moduły)")
def compare(a: str, b: str) -> dict[str, Any]:
    for mid in (a, b):
        if record_module.load_summary(mid) is None:
            raise HTTPException(404, f"Nie ma materiału {mid}.")
    return grouping_module.compare_materials(a, b).to_dict()


@app.get("/graph", tags=["graf"], summary="Graf powiązań: węzły i krawędzie")
def graph(campaign_id: Optional[int] = None, operator: Optional[int] = None, material_id: Optional[str] = None,
          entities: bool = True, shared_only: bool = True, min_score: float = 0.5) -> dict[str, Any]:
    """Cały graf albo wycinek: `campaign_id` (jedna kampania), `operator` (kampanie operatora),
    `material_id` (otoczenie materiału).

    Węzły: operator, campaign, material, domain, person, account, kit, infra (pole `type`).
    Krawędzie: w_kampanii, operator, ta_sama_kampania, do_weryfikacji, ten_sam_operator, uzywa,
    wymienia, pokazuje (pole `type`); przesłanki w `data.reasons`. Opis typów w polu `legend`.
    `shared_only=true` pokazuje tylko ślady wspólne dla co najmniej dwóch materiałów (czytelniejszy graf).
    """
    try:
        return graph_module.build_graph(campaign_id=campaign_id, operator_group=operator, material_id=material_id,
                                        entities=entities, shared_only=shared_only, min_score=min_score)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Baza niedostępna: {exc.__class__.__name__}: {exc}") from exc


@app.get("/stats", tags=["graf"], summary="Liczby do pulpitu: materiały, kampanie, operatorzy, do weryfikacji")
def stats() -> dict[str, Any]:
    from fakecollector.grouping.campaigns import connect
    try:
        with connect() as conn:
            row = conn.execute(
                """SELECT (SELECT count(*) FROM materials),
                          (SELECT count(*) FROM campaigns WHERE size >= 2),
                          (SELECT count(*) FROM campaigns WHERE size = 1),
                          (SELECT count(DISTINCT operator_group) FROM campaigns WHERE operator_group IS NOT NULL),
                          (SELECT count(*) FROM materials WHERE assignment = 'do_weryfikacji'),
                          (SELECT count(*) FROM materials WHERE campaign_id IS NULL),
                          (SELECT count(DISTINCT domain) FROM materials WHERE domain IS NOT NULL)""").fetchone()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Baza niedostępna: {exc.__class__.__name__}: {exc}") from exc
    keys = ("materials", "campaigns", "single_material_campaigns", "operators", "to_review", "unassigned", "domains")
    return dict(zip(keys, row))
