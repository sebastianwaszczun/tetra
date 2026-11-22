"""CLI ewaluacji.

    python -m fakecollector.eval labels            # szkic data/labels.csv z obecnych kampanii (do poprawienia!)
    python -m fakecollector.eval run               # metryki, przypisywanie, ablacja, progi -> data/eval/raport.md
    python -m fakecollector.eval run --fresh       # porównaj pary od nowa (po zmianie modułów lub bazy)
    python -m fakecollector.eval show              # ostatni raport
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fakecollector.eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("labels", help="szkic etykiet z obecnych kampanii")
    p.add_argument("--force", action="store_true", help="nadpisz istniejący plik")
    p.add_argument("--path", default=None)
    p = sub.add_parser("run", help="uruchom ewaluację")
    p.add_argument("--fresh", action="store_true", help="porównaj wszystkie pary od nowa")
    p.add_argument("--threshold", type=float, default=None, help="próg kampanii (domyślnie ASSIGN)")
    p.add_argument("--labels", default=None)
    sub.add_parser("show", help="pokaż ostatni raport")
    a = ap.parse_args(argv)

    if a.cmd == "labels":
        from .labels import LABELS_FILE, draft_labels
        try:
            r = draft_labels(a.path or LABELS_FILE, force=a.force)
        except FileExistsError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Zapisano szkic {r['path']} ({r['rows']} materiałów, {r['hints']} z podpowiedzią).")
        print("Teraz przejrzyj plik: popraw kolumnę „kampania” tam, gdzie system się mylił, "
              "i wpisz „tak” w kolumnie „sprawdzone”.")
        return 0
    if a.cmd == "run":
        from .labels import LABELS_FILE
        from .run import EVAL_DIR, run, to_markdown
        report = run(threshold=a.threshold, fresh=a.fresh, labels_path=a.labels or LABELS_FILE,
                     progress=lambda n, t: print(f"\r  porównuję pary: {n}/{t}", end="", flush=True))
        print("\n")
        print(to_markdown(report))
        print(f"Raport: {EVAL_DIR / 'raport.md'} (JSON: {EVAL_DIR / 'raport.json'})")
        return 0
    from .run import last_report, to_markdown
    r = last_report()
    if not r:
        print("Brak raportu – uruchom: python -m fakecollector.eval run", file=sys.stderr)
        return 1
    print(to_markdown(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
