"""Wsadowa analiza z linii poleceń (np. cały udostępniony zbiór).

    cd modules
    python -m osint.cli ~/Downloads/DANE/do_udostępnienia/* --out ../data/osint_records.jsonl
    python -m osint.cli --url https://przyklad.xyz/ --render
    python -m osint.correlate ../data/osint_records.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import analyze_path, analyze_url


def main(argv=None):
    ap = argparse.ArgumentParser(description="OSINT: HAR/HTML/MHTML/ZIP/folder/URL -> rekord JSON")
    ap.add_argument("paths", nargs="*", help="pliki lub foldery incydentów")
    ap.add_argument("--url", action="append", default=[], help="adres do pobrania na żywo (można kilka)")
    ap.add_argument("--render", action="store_true", help="render w Chromium (Playwright)")
    ap.add_argument("--no-enrich", action="store_true", help="bez DNS/RDAP/ASN/TLS (offline)")
    ap.add_argument("--no-ct", action="store_true", help="bez crt.sh")
    ap.add_argument("--each", action="store_true", help="każdy podfolder strona_* jako osobny rekord")
    ap.add_argument("--out", help="plik JSONL (domyślnie stdout)")
    a = ap.parse_args(argv)

    targets: list[tuple[str, Path]] = []
    for p in map(Path, a.paths):
        subs = sorted(x for x in p.iterdir() if x.is_dir() and not x.name.endswith("_files")) if p.is_dir() else []
        own_material = any(p.glob("*.har")) or any(p.glob("*.htm*")) if p.is_dir() else True
        if a.each and subs and not own_material:
            targets += [(f"{p.name}/{s.name}", s) for s in subs]
        else:
            targets.append((p.name, p))

    out = open(a.out, "w", encoding="utf-8") if a.out else sys.stdout
    try:
        for label, p in targets:
            print(f"[osint] {label} ...", file=sys.stderr, flush=True)
            try:
                rec = analyze_path(p, enrich=not a.no_enrich, ct=not a.no_ct)
            except Exception as e:  # noqa: BLE001
                print(f"[osint]   BŁĄD: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            rec["_label"] = label
            q = rec["quality"]
            print(f"[osint]   {rec['landing']['registered_domains']} pivoty={q['total_pivots']} "
                  f"silne={q['strong_pivots']} kompletność={q['completeness']} t={q['elapsed_s']}s",
                  file=sys.stderr)
            out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        for u in a.url:
            rec = analyze_url(u, enrich=not a.no_enrich, ct=not a.no_ct, render=a.render)
            rec["_label"] = u
            out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    finally:
        if a.out:
            out.close()


if __name__ == "__main__":
    main()
