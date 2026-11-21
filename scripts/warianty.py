"""Zapisuje warianty jednego obrazu i manifest oczekiwań do data/podglad/."""
import argparse
import json
from pathlib import Path

from fakecollector.features.image.warianty import generuj_warianty
from fakecollector.features.image.wzorzec import otworz_obraz


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plik", type=Path)
    parser.add_argument("--wyjscie", type=Path, default=Path("data/podglad/warianty"))
    parser.add_argument("--ziarno", type=int, default=0)
    args = parser.parse_args()
    obraz = otworz_obraz(args.plik)
    args.wyjscie.mkdir(parents=True, exist_ok=True)
    manifest = []
    for wariant in generuj_warianty(obraz, args.ziarno):
        nazwa = f"{wariant.nazwa}.png"
        wariant.obraz.save(args.wyjscie / nazwa)
        manifest.append({"plik": nazwa, "oczekiwanie": wariant.oczekiwanie})
    (args.wyjscie / "manifest.json").write_text(
        json.dumps({"ziarno": args.ziarno, "warianty": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Zapisano {len(manifest)} wariantów w {args.wyjscie}")


if __name__ == "__main__":
    main()
