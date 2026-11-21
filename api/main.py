"""API dla frontu.

    cd api && uvicorn main:app --reload --port 8000

POST /analyze (multipart/form-data)
    text   – opcjonalny tekst (link, SMS, nazwa produktu…)
    files  – zero, jeden lub wiele plików (obrazy, wideo, HAR, HTML, …)
Na razie tylko loguje, co przyszło.
"""
import logging
import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

log = logging.getLogger("uvicorn.error")

app = FastAPI(title="FakeCollector API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/analyze")
async def analyze(
    text: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
):
    text = (text or "").strip() or None
    if not text and not files:
        raise HTTPException(400, "Podaj tekst albo co najmniej jeden plik.")

    received = []
    for f in files:
        size = len(await f.read())
        received.append({"name": f.filename, "content_type": f.content_type, "size": size})

    log.info("analyze: tekst=%s, plików=%d", "tak" if text else "nie", len(received))
    for r in received:
        log.info("  - %s (%s, %d B)", r["name"], r["content_type"], r["size"])

    return {"text": text is not None, "files_count": len(received), "files": received}
