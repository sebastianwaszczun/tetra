"""Preprocessing: dowolne wejście -> Incident (materiał rozłożony na składniki dla modułów).

    from fakecollector.ingest import ingest, ingest_upload
    inc = ingest("data/DANE/47")
    inc.for_module("image")     # zrzuty, obrazy, klatki
    inc.text, inc.text_origin   # wejście dla modułu tekstu
"""
from .core import Options, finalize, ingest, ingest_upload, is_error_page, run_osint, select_text
from .models import ROUTES, Artifact, Incident

__all__ = ["ingest", "ingest_upload", "run_osint", "finalize", "select_text", "is_error_page", "Options",
           "Incident", "Artifact", "ROUTES"]
