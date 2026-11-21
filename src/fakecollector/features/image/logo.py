"""Porównanie wycinków z opisami i lokalnymi wzorcami logo."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

import numpy as np

from .elementy import Wycinek, na_wejscie
from .konfiguracja import wczytaj_konfiguracje
from .model import Logo
from .przygotowanie import ROZSZERZENIA, czytaj_bajty, przygotuj_obraz
from .wektor import Macierz, ModelObrazu

KATALOG_LOGO = Path(__file__).resolve().parents[4] / "data/logo_wzorce"


@dataclass(frozen=True)
class KandydatLogo:
    indeks: int
    marka: str
    wynik: float
    zrodlo: Literal["opis", "wzorzec"]


@cache
def _wzorce(model: ModelObrazu, katalog: Path, marki: tuple[str, ...]) -> tuple[dict[str, Macierz], tuple[str, ...]]:
    limity = wczytaj_konfiguracje()["wczytywanie"]
    macierze = {}
    braki = []
    baza = katalog.resolve()
    for marka in marki:
        obrazy = []
        folder = katalog / marka
        if not folder.resolve().is_relative_to(baza) or not folder.is_dir():
            continue
        for plik in sorted(folder.iterdir()):
            if (not plik.is_file() or plik.suffix.lower() not in ROZSZERZENIA
                    or not plik.resolve().is_relative_to(baza)):
                continue
            try:
                obraz, _ = przygotuj_obraz(czytaj_bajty(plik, limity["maks_bajtow"]))
            except (OSError, ValueError):
                obraz = None
            if obraz is None:
                braki.append(f"Pominięto nieobsługiwany lub uszkodzony wzorzec marki {marka}")
            else:
                obrazy.append(obraz)
        if obrazy:
            macierze[marka] = model.koduj_obrazy(obrazy)
    return macierze, tuple(braki)


def ocen_logo(model: ModelObrazu, obrazy: Macierz, marki: dict[str, list[str]], prog_negatywu: float,
               katalog: Path = KATALOG_LOGO) -> tuple[list[KandydatLogo], list[str]]:
    """Łączy najlepszy wynik opisu i wzorca, respektując negatyw TVN24."""
    negatyw = ["a red live badge reading live or na zywo, without the TVN24 logo",
               "a generic red live broadcast tag, no TV station emblem"]
    grupy = list(marki.values()) + [negatyw, ["an unbranded picture, no television or company logo"]]
    oceny = model.ocen_opisy(obrazy, grupy)
    wzorce, bledy = _wzorce(model, katalog, tuple(marki))
    braki = list(dict.fromkeys(bledy))
    if len(wzorce) < len(marki):
        braki.append(f"Lokalne wzorce logo dla {len(wzorce)} z {len(marki)} marek")
    wyniki = []
    for i, marka in enumerate(marki):
        wzorzec = wzorce.get(marka)
        podobienstwa = np.clip((obrazy @ wzorzec.T).max(axis=1), 0, 1) if wzorzec is not None else np.zeros(len(obrazy))
        for j, (opis, podobienstwo) in enumerate(zip(oceny[:, i], podobienstwa, strict=True)):
            if marka == "TVN24" and oceny[j, len(marki)] >= prog_negatywu:
                continue
            if podobienstwo > opis:
                wyniki.append(KandydatLogo(j, marka, float(podobienstwo), "wzorzec"))
            else:
                wyniki.append(KandydatLogo(j, marka, float(opis), "opis"))
    return wyniki, braki


def wybierz_logo(kandydaci: Sequence[KandydatLogo], regiony: Sequence[Wycinek], progi: Mapping[str, float],
                 rozmiar: tuple[int, int], offset: tuple[int, int], orientacja: int) -> list[Logo]:
    """Wybiera najwyżej ocenioną markę w wycinku i najlepszy wycinek dla marki."""
    zajete = set()
    wynik: dict[str, Logo] = {}
    for k in sorted(kandydaci, key=lambda element: -element.wynik):
        region = regiony[k.indeks]
        prog = progi.get(k.marka)
        if prog is None or k.wynik < prog or k.indeks in zajete or region.rog is None:
            continue
        zajete.add(k.indeks)
        if k.marka not in wynik:
            wynik[k.marka] = Logo(marka=k.marka, rog=region.rog, pewnosc=k.wynik, zrodlo=k.zrodlo,
                                  prostokat=na_wejscie(region.prostokat, rozmiar, offset, orientacja))
    return list(wynik.values())
