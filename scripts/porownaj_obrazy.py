"""Porównuje lokalne obrazy, wypisuje JSON i zapisuje raport HTML."""
import argparse
import json
from itertools import combinations
from pathlib import Path
from uuid import uuid4

from fakecollector.features.image import analizuj_obraz, porownaj
from fakecollector.features.image.konfiguracja import wczytaj_konfiguracje
from fakecollector.features.image.model import Kontekst
from fakecollector.features.image.przygotowanie import czytaj_bajty, otworz_raster
from fakecollector.features.image.raport_html import MaterialHTML, ParaHTML, zapisz_raport_html


def wczytaj_material(plik: Path, limity: dict[str, int]) -> MaterialHTML:
    """Używa tych samych bajtów do analizy i podglądu w raporcie."""
    if not plik.is_file():
        raise FileNotFoundError(f"Brak lokalnego pliku {plik}")
    dane = czytaj_bajty(plik, limity["maks_bajtow"])
    cechy = analizuj_obraz(dane, Kontekst(zrodlo=plik.name, rola="kreacja"))
    obraz = None
    if all(cechy.rozmiar):
        with otworz_raster(dane, limity["maks_pikseli"]) as raster:
            raster.thumbnail((540, 400))
            obraz = raster.convert("RGB")
    return MaterialHTML(plik.name, obraz, cechy)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("obrazy", type=Path, nargs="+", metavar="obraz")
    args = parser.parse_args()
    if len(args.obrazy) < 2:
        parser.error("Podaj co najmniej dwa lokalne obrazy")

    limity = wczytaj_konfiguracje()["wczytywanie"]
    materialy = {}
    try:
        for plik in args.obrazy:
            klucz = plik.resolve()
            if klucz not in materialy:
                materialy[klucz] = wczytaj_material(plik, limity)
    except (OSError, ValueError) as blad:
        parser.error(str(blad))
    wejscia = [materialy[plik.resolve()] for plik in args.obrazy]
    pary = [ParaHTML(a, b, "demo", porownaj(a.cechy, b.cechy)) for a, b in combinations(wejscia, 2)]

    katalog = Path("data/raporty")
    katalog.mkdir(parents=True, exist_ok=True)
    raport = katalog / f"porownanie-{uuid4().hex}.html"
    zapisz_raport_html(raport, pary, f"Porównane pary: {len(pary)}")
    wynik = {"raport_html": raport.as_posix(), "pary": [
        {"a": p.a.nazwa, "b": p.b.nazwa, **p.wynik.model_dump(mode="json")} for p in pary
    ]}
    print(json.dumps(wynik, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
