"""Rysuje prostokąty kreacji w pikselach oryginalnych zrzutów."""
import argparse
from pathlib import Path

from PIL import ImageDraw

from fakecollector.features.image.wzorzec import otworz_obraz, plik_danych, wczytaj_kreacje


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dane", type=Path, default=Path("data"))
    args = parser.parse_args()
    if not (args.dane / "wzorzec/kreacje.json").is_file():
        parser.exit(0, "Brak kreacje.json. Podgląd wymaga adnotacji przygotowanych przez człowieka.\n")
    zbior = wczytaj_kreacje(args.dane)
    cel = args.dane / "podglad/kreacje"
    cel.mkdir(parents=True, exist_ok=True)
    for i, kreacja in enumerate(zbior.kreacje, start=1):
        obraz = otworz_obraz(plik_danych(args.dane, kreacja.plik))
        x, y, w, h = kreacja.prostokat_kreacji
        ImageDraw.Draw(obraz).rectangle((x, y, x + w - 1, y + h - 1), outline="red", width=2)
        plik = cel / f"{i:04d}.png"
        obraz.save(plik)
        print(f"{kreacja.id} ({kreacja.status}): {plik}")


if __name__ == "__main__":
    main()
