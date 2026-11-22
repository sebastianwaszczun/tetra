"""Rekord materiału: wszystkie moduły -> jeden rekord -> Postgres (pgvector) -> kandydaci.

    from fakecollector.record import process
    result = process("data/BACKUP/p1")          # ingest + moduły + zapis + kandydaci
    result["record"]["id"], result["candidates"]

Etapy osobno:
    from fakecollector.ingest import ingest
    from fakecollector.record import analyze_incident, build_record, save_record, find_candidates
    inc = ingest("data/DANE/5", fetch=False)
    res = analyze_incident(inc)          # moduły: AV -> tekst -> OSINT -> obraz
    rec = build_record(inc, res)         # podsumowanie, pełne cechy, wektory, klucze
    save_record(rec)                     # Postgres
    find_candidates(rec.id)              # podobne materiały z bazy (wejście dla grupowania)
"""
from __future__ import annotations

from typing import Any

from .build import MaterialRecord, build_record, front_view, strip_vectors
from .pipeline import ModuleResults, analyze_incident
from .store import (count_materials, delete_material, find_candidates, list_materials, load_features,
                    load_record, load_summary, save_record)

__all__ = ["process", "process_incident", "analyze_incident", "build_record", "front_view", "strip_vectors",
           "save_record", "load_record", "load_summary", "load_features", "list_materials", "delete_material",
           "count_materials", "find_candidates", "MaterialRecord", "ModuleResults"]


def process_incident(inc: Any, *, save: bool = True, candidates: bool = True, **module_opts: Any) -> dict[str, Any]:
    """Materiał po preprocessingu -> moduły -> rekord -> (zapis) -> (kandydaci)."""
    res = analyze_incident(inc, **module_opts)
    rec = build_record(inc, res)
    out: dict[str, Any] = {"record": front_view(rec), "candidates": [], "saved": False, "_record": rec,
                           "_results": res}
    if save:
        try:
            save_record(rec)
            out["saved"] = True
            if candidates:
                out["candidates"] = find_candidates(rec.id)
        except Exception as exc:  # noqa: BLE001
            out["record_error"] = f"{exc.__class__.__name__}: {exc}"
    return out


def process(value: str, *, fetch: bool = False, **opts: Any) -> dict[str, Any]:
    """Wejście (ścieżka, URL, tekst) -> preprocessing -> process_incident."""
    from fakecollector.ingest import ingest
    ingest_keys = {"ocr", "video", "assets", "dedup", "store_root"}
    inc = ingest(value, fetch=fetch, **{k: v for k, v in opts.items() if k in ingest_keys})
    return process_incident(inc, **{k: v for k, v in opts.items() if k not in ingest_keys})
