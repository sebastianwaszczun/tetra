"""CLI rekordu materiału.

    python -m fakecollector.record add data/BACKUP data/DANE          # wczytaj paczkę do bazy
    python -m fakecollector.record add data/DANE/5 --no-asr            # bez transkrypcji (szybciej)
    python -m fakecollector.record list
    python -m fakecollector.record show <id> [--json]
    python -m fakecollector.record candidates <id>
    python -m fakecollector.record delete <id>
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time


def _print_candidates(cands: list[dict], top: int = 5, indent: str = "   ") -> None:
    if not cands:
        print(f"{indent}kandydaci: brak")
        return
    print(f"{indent}kandydaci:")
    for c in cands[:top]:
        sig = ", ".join(_signal_text(s) for s in c["signals"][:4])
        print(f"{indent}  {c['rank']:.2f}  {c['id']}  {str(c.get('source') or '')[-28:]:<28} {sig}")


def _signal_text(s: dict) -> str:
    if "similarity" in s:
        return f"{s['signal']}={s['similarity']}"
    if "distance" in s:
        return f"{s['signal']}(Hamming {s['distance']})"
    return f"{s['signal']}×{s.get('matches', 1)}"


def cmd_add(a: argparse.Namespace) -> int:
    from fakecollector.ingest.__main__ import expand
    from . import process
    opts = dict(fetch=a.fetch, ocr=not a.no_ocr, text=not a.no_text, av=not a.no_av, asr=not a.no_asr,
                osint=not a.no_osint, enrich=a.enrich, image=not a.no_image, save=not a.no_save)
    total = time.time()
    for value in expand(a.inputs):
        t0 = time.time()
        out = process(value, **opts)
        r = out["record"]
        mods = " ".join(f"{m}:{v['status']}" for m, v in r["modules"].items())
        print(f"[{r['id']}] {value}  ({time.time() - t0:.0f} s)")
        print(f"   {r['status']}, {r['domain']}, „{(r['title'] or '')[:60]}”")
        print(f"   tekst: {r['n_words']} słów ({r['text_origin']}), osoby: {', '.join(r['persons'][:3]) or '-'}"
              f"{', na obrazach: ' + ', '.join(r['persons_visual']) if r['persons_visual'] else ''}")
        print(f"   moduły: {mods}   wektory: {r['counts']['vectors']}, klucze: {r['counts']['keys']}")
        for e in r["errors"][:3]:
            print(f"   błąd: {e}")
        if out.get("record_error"):
            print(f"   ZAPIS NIEUDANY: {out['record_error']}")
        elif out["saved"]:
            _print_candidates(out["candidates"])
    print(f"Gotowe w {time.time() - total:.0f} s.")
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    from . import count_materials, list_materials
    rows = list_materials(a.limit)
    print(f"Materiałów w bazie: {count_materials()}")
    for r in rows:
        print(f"{r['id']}  {r['created_at'][:19]}  {r['status']:<7} {str(r['domain'] or '-'):<26} "
              f"{str(r['source'])[-30:]:<30} {', '.join(r['persons'][:2])}")
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    from . import front_view, load_record
    rec = load_record(a.id)
    if not rec:
        print("Nie ma takiego materiału.", file=sys.stderr)
        return 1
    view = front_view(rec)
    if a.json:
        print(json.dumps(view, ensure_ascii=False, indent=1, default=str))
        return 0
    for k in ("source", "status", "domain", "domains", "final_url", "title", "text_origin", "n_words", "persons",
              "persons_visual", "brands", "missing", "campaign_id", "assignment"):
        print(f"{k:>15}: {view.get(k)}")
    print(f"{'moduły':>15}: {json.dumps(view['modules'], ensure_ascii=False)}")
    kinds: dict[str, int] = {}
    for k, _, _ in rec.keys:
        kinds[k.split(':')[0] if not k.startswith('osint:') else k] = kinds.get(k, 0) + 1
    print(f"{'klucze':>15}: {kinds}")
    return 0


def cmd_candidates(a: argparse.Namespace) -> int:
    from . import find_candidates
    cands = find_candidates(a.id, limit=a.limit)
    if a.json:
        print(json.dumps(cands, ensure_ascii=False, indent=1, default=str))
    else:
        _print_candidates(cands, top=a.limit, indent="")
    return 0


def cmd_delete(a: argparse.Namespace) -> int:
    from . import delete_material
    print("usunięto" if delete_material(a.id) else "nie znaleziono")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fakecollector.record", description="Rekord materiału")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="przeanalizuj i zapisz materiały")
    p.add_argument("inputs", nargs="+")
    p.add_argument("--fetch", action="store_true", help="pobieraj strony z podanych adresów")
    p.add_argument("--enrich", action="store_true", help="OSINT z DNS/WHOIS/TLS (wolniej)")
    for flag in ("ocr", "text", "av", "asr", "osint", "image", "save"):
        p.add_argument(f"--no-{flag}", action="store_true")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("list")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("show")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("candidates")
    p.add_argument("id")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_candidates)
    p = sub.add_parser("delete")
    p.add_argument("id")
    p.set_defaults(fn=cmd_delete)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(message)s")
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
