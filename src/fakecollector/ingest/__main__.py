"""CLI preprocessingu.

    python -m fakecollector.ingest data/BACKUP data/DANE --no-fetch
    python -m fakecollector.ingest https://przyklad.live/ --json data/ingest.json
    python -m fakecollector.ingest "Zobacz: example.com/oferta" --no-fetch --text
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .core import STORE_ROOT, ingest
from .models import Incident


def expand(inputs: list[str]) -> list[str]:
    """Folder zbioru (np. data/BACKUP z podfolderami p1, p2…) rozwija na osobne przypadki."""
    out: list[str] = []
    for value in inputs:
        p = Path(value)
        if p.is_dir():
            files = [c for c in p.iterdir() if c.is_file()]
            subdirs = sorted((c for c in p.iterdir() if c.is_dir()), key=_natural)
            if not files and subdirs and not any(d.name.endswith("_files") for d in subdirs):
                out.extend(str(d) for d in subdirs)
                continue
        out.append(value)
    return out


def _natural(p: Path):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


def summary(inc: Incident) -> str:
    r = inc.routes()
    lines = [f"[{inc.id}] {inc.input_type}: {inc.source}",
             f"   status={inc.status}  tekst={len(inc.text.split())} słów ({','.join(inc.text_origin) or '-'})"
             f"  tytuł={inc.title[:60]!r}",
             f"   moduły: tekst={r['text']} osint={r['osint']} obraz={r['image']} audio/wideo={r['av']}"]
    if inc.urls:
        lines.append(f"   adresy: {', '.join(inc.urls[:4])}" + (" …" if len(inc.urls) > 4 else ""))
    if len(inc.redirect_chain) > 1:
        lines.append(f"   przekierowania: {' -> '.join(inc.redirect_chain[:5])}")
    if inc.duplicate_of:
        lines.append(f"   DUPLIKAT materiału {inc.duplicate_of}")
    for m in inc.missing:
        lines.append(f"   brak: {m}")
    for e in inc.errors[:5]:
        lines.append(f"   błąd: {e}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fakecollector.ingest",
                                 description="Preprocessing: wejście -> składniki dla modułów")
    ap.add_argument("inputs", nargs="+", help="pliki, foldery, adresy URL albo tekst")
    ap.add_argument("--no-fetch", action="store_true", help="nie pobierać stron z internetu")
    ap.add_argument("--no-ocr", action="store_true", help="bez OCR zrzutów")
    ap.add_argument("--no-video", action="store_true", help="bez wyciągania dźwięku i klatek z wideo")
    ap.add_argument("--no-assets", action="store_true", help="bez obrazów z folderów *_files")
    ap.add_argument("--no-dedup", action="store_true", help="bez sprawdzania duplikatów")
    ap.add_argument("--no-save", action="store_true", help="nie zapisywać incident.json")
    ap.add_argument("--store", default=str(STORE_ROOT), help="folder wyników (domyślnie data/store)")
    ap.add_argument("--osint", action="store_true", help="od razu uruchom moduł OSINT (bez wzbogacania)")
    ap.add_argument("--text", action="store_true", help="wypisz wybrany tekst")
    ap.add_argument("--json", help="zapisz wszystkie materiały do jednego pliku JSON")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(message)s")

    opts = dict(fetch=not a.no_fetch, ocr=not a.no_ocr, video=not a.no_video, assets=not a.no_assets,
                dedup=not a.no_dedup, save=not a.no_save, store_root=Path(a.store))
    results = []
    for value in expand(a.inputs):
        inc = ingest(value, **opts)
        print(summary(inc), flush=True)
        if a.osint:
            from .core import run_osint
            rec = run_osint(inc, enrich=False)
            if rec:
                q = rec.get("quality", {})
                print(f"   OSINT: domeny={rec['landing']['registered_domains']} pivoty={q.get('total_pivots')}")
        if a.text and inc.text:
            print("   ---\n   " + inc.text[:1500].replace("\n", "\n   ") + "\n   ---")
        results.append(inc.to_dict(with_text=True))
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Zapisano {len(results)} materiałów do {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
