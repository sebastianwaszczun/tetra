"""Lokalna ewaluacja orientacyjnych wyników FaceTorch."""
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from statistics import mean, median
from typing import Any

import numpy as np
from PIL import Image

from ..konfiguracja import wczytaj_konfiguracje
from ..model import Twarz
from ..opisy_twarzy import predyktor_facetorch
from ..przygotowanie import czytaj_bajty, przygotuj_obraz
from ..referencje_twarzy import wczytaj_probki
from ..twarze import analizuj_twarze
from ..wzorzec import otworz_obraz, plik_danych, wczytaj_kreacje


def _material(dane: Path, katalog_portretow: Path | None, maks_bajtow: int) -> Iterator[tuple[str, str, Image.Image | bytes]]:
    for k in wczytaj_kreacje(dane).kreacje:
        x, y, w, h = k.prostokat_kreacji
        obraz = otworz_obraz(plik_danych(dane, k.plik))
        yield "kreacje", k.id, obraz.crop((x, y, x + w, y + h))

    katalog = (dane / "reference_people").resolve()
    for indeks, (_, rel) in enumerate(sorted(wczytaj_probki("buffalo_l", katalog)), start=1):
        plik = (katalog / rel).resolve()
        if not plik.is_relative_to(katalog):
            raise ValueError("Ścieżka referencji poza katalogiem bazy")
        yield "referencje", str(indeks), czytaj_bajty(plik, maks_bajtow)

    if katalog_portretow is not None:
        katalog_portretow = katalog_portretow.resolve()
        if not katalog_portretow.is_relative_to((dane / "nask/DANE").resolve()):
            raise ValueError("Katalog portretów musi znajdować się w lokalnych danych NASK")
        pliki = sorted(plik for wzorzec in ("M*.png", "W*.png") for plik in katalog_portretow.glob(wzorzec))
        for plik in pliki:
            if plik.is_file() and plik.resolve().is_relative_to(katalog_portretow):
                yield "portrety", plik.name, czytaj_bajty(plik, maks_bajtow)


def _opis(twarz: Twarz) -> dict[str, Any]:
    return {"prostokat": twarz.prostokat, "deepfake": twarz.deepfake,
            "emocja": twarz.emocja.model_dump() if twarz.emocja is not None else None,
            "uwaga": twarz.uwaga_facetorch, "brak": twarz.brak_facetorch, "jakosc": twarz.jakosc,
            "przechodzi_prog": twarz.brak_facetorch != "niska_jakosc",
            "ma_wektor": twarz.wektor is not None or twarz.wektor_drugi is not None,
            "ma_oba_wektory": twarz.wektor is not None and twarz.wektor_drugi is not None}


def _podsumuj(material: list[dict[str, Any]]) -> dict[str, Any]:
    twarze = [t for w in material for t in w["twarze"]]
    wyniki = [t["deepfake"] for t in twarze if t["deepfake"] is not None]
    emocje = Counter(t["emocja"]["etykieta"] for t in twarze if t["emocja"] is not None)
    braki = Counter(t["brak"] for t in twarze if t["brak"] is not None)
    czasy = [w["facetorch_ms"] for w in material]
    czasy_z_opisem = [w["facetorch_ms"] for w in material if any(t["deepfake"] is not None for t in w["twarze"])]
    rozklad = np.percentile(wyniki, (0, 25, 50, 75, 100)).tolist() if wyniki else None
    return {"obrazy": len(material), "twarze": len(twarze), "wyniki": len(wyniki), "emocje": dict(emocje),
            "przechodzi_prog": sum(t["przechodzi_prog"] for t in twarze),
            "ma_wektor": sum(t["ma_wektor"] for t in twarze), "ma_oba_wektory": sum(t["ma_oba_wektory"] for t in twarze),
            "rozklad_deepfake": rozklad, "braki": dict(braki),
            "powtarzalne": sum(w["powtarzalny"] for w in material),
            "powtarzalne_z_wynikiem": sum(w["powtarzalny"] and any(t["deepfake"] is not None for t in w["twarze"])
                                           for w in material),
            "ms_na_twarz": sum(czasy) / len(wyniki) if wyniki else None,
            "ms_na_obraz_srednia": mean(czasy) if czasy else None, "ms_na_obraz_mediana": median(czasy) if czasy else None,
            "ms_na_obraz_z_opisem_mediana": median(czasy_z_opisem) if czasy_z_opisem else None,
            "obrazy_bez_twarzy": sum(not w["twarze"] for w in material), "pomiary": material}


def ewaluuj_facetorch(dane: Path, katalog_portretow: Path | None = None) -> dict[str, Any]:
    """Mierzy oba predyktory na danych lokalnych i sprawdza powtarzalność."""
    konfiguracja = wczytaj_konfiguracje()
    braki = []
    try:
        predyktor = predyktor_facetorch(Path(konfiguracja["facetorch"]["manifest"]))
    except (OSError, ValueError, RuntimeError, ImportError, KeyError) as blad:
        return {"uwaga": "orientacyjnie", "braki": [f"brak_wag: {blad}"], "zbiory": {}}
    sztuczny = np.zeros((100, 100, 3), dtype=np.uint8)
    for _ in range(2):
        predyktor(sztuczny)
    pusty, cechy = przygotuj_obraz(Image.new("RGB", (256, 128), "white"))
    if pusty is not None:
        analizuj_twarze(pusty, cechy, opisy=False)

    grupy: dict[str, list[dict[str, Any]]] = {"kreacje": [], "referencje": [], "portrety": []}
    for grupa, nazwa, material in _material(dane, katalog_portretow, konfiguracja["wczytywanie"]["maks_bajtow"]):
        pomiary = []
        for _ in range(2):
            obraz, cechy = przygotuj_obraz(material)
            if obraz is None:
                braki.append(f"{grupa}/{nazwa}: nieczytelny obraz")
                break
            analizuj_twarze(obraz, cechy)
            pomiary.append(([_opis(t) for t in cechy.twarze], cechy.czasy_ms.get("facetorch", 0.0)))
        if len(pomiary) == 2:
            twarze, czas = pomiary[0]
            grupy[grupa].append({"id": nazwa, "twarze": twarze, "facetorch_ms": czas,
                                 "powtarzalny": twarze == pomiary[1][0]})
    if katalog_portretow is not None and not grupy["portrety"]:
        braki.append("Brak pasujących portretów w podanym katalogu")
    return {"uwaga": "orientacyjnie", "braki": braki, "zbiory": {k: _podsumuj(w) for k, w in grupy.items()}}
