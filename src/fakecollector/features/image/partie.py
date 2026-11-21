"""Grupowanie przygotowanych klatek i dziedziczenie cech przedstawiciela."""
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from .elementy import na_wejscie
from .hashe import odleglosci, pasujaca_para
from .model import Brak, CechyObrazu, Prostokat


@dataclass(frozen=True)
class Przygotowany:
    raster: Image.Image | None
    cechy: CechyObrazu


class WynikiPartii(list[CechyObrazu]):
    """Rekordy w kolejności wejścia oraz pomiary dla całej partii."""

    def __init__(self, wyniki: list[CechyObrazu], wywolania: int = 0, czasy_ms: dict[str, float] | None = None) -> None:
        super().__init__(wyniki)
        self.braki = [] if wyniki else [Brak(skladnik="partie", powod="Brak obrazów")]
        self.wywolania_ciezkich_modeli = wywolania
        self.czasy_ms = czasy_ms or {}


def grupuj(elementy: list[Przygotowany], progi_hash: dict, proporcje: float, bok_probki: int) -> list[list[int]]:
    """Wymaga zgodności hashy pełnego rastra między każdą parą grupy."""
    grupy: list[list[int]] = []
    ostrosci = {}
    for i, wpis in enumerate(elementy):
        c = wpis.cechy
        if wpis.raster is None or c.ubogi_w_tresc or c.za_maly:
            continue
        w, h = c.rozmiar
        rozmiar = w / h
        for grupa in grupy:
            if not c.hashe.phash:
                break
            czlonkowie = [elementy[j].cechy for j in grupa]
            if all(max(rozmiar / (d.rozmiar[0] / d.rozmiar[1]), (d.rozmiar[0] / d.rozmiar[1]) / rozmiar) <= proporcje
                   and pasujaca_para(tuple(p for p in odleglosci(c.hashe, d.hashe) if p.wariant == "caly"),
                                     progi_hash["T_p"], progi_hash["T_d"]) for d in czlonkowie):
                grupa.append(i)
                break
        else:
            grupy.append([i])
            continue
        if not c.hashe.phash:
            grupy.append([i])

    def ranking(i: int) -> tuple[float, float, int]:
        wpis = elementy[i]
        raster = wpis.cechy._raster_orb or wpis.raster.convert("L")
        w, h = raster.size
        skala = min(1, bok_probki / max(w, h))
        probka = raster.resize((max(1, round(w * skala)), max(1, round(h * skala))), Image.Resampling.LANCZOS)
        if i not in ostrosci:
            ostrosci[i] = float(cv2.Laplacian(np.asarray(probka), cv2.CV_64F).var())
        return ostrosci[i], wpis.cechy.miara_tresci.krawedzie or 0.0, -i

    for grupa in grupy:
        if len(grupa) > 1:
            grupa.sort(key=ranking, reverse=True)
    return grupy


def _przenies(pole: Prostokat, zrodlo: CechyObrazu, cel: CechyObrazu) -> Prostokat:
    a, b = zrodlo.przygotowanie, cel.przygotowanie
    orientacja = a.orientacja_exif
    odwrotna = {6: 8, 8: 6}.get(orientacja, orientacja)
    rozmiar = a.rozmiar_oryginalny[::-1] if 5 <= orientacja <= 8 else a.rozmiar_oryginalny
    x, y, w, h = na_wejscie(pole, rozmiar, (0, 0), odwrotna)
    x, y = x - a.odciete_ramki[0], y - a.odciete_ramki[1]
    szer, wys = cel.rozmiar
    sx, sy = szer / zrodlo.rozmiar[0], wys / zrodlo.rozmiar[1]
    lewy = max(0, min(szer - 1, round(x * sx)))
    gora = max(0, min(wys - 1, round(y * sy)))
    prawy = min(szer, max(lewy + 1, round((x + w) * sx)))
    dol = min(wys, max(gora + 1, round((y + h) * sy)))
    return na_wejscie((lewy, gora, prawy - lewy, dol - gora), b.rozmiar_oryginalny,
                      b.odciete_ramki[:2], b.orientacja_exif)


def dziedzicz(cel: CechyObrazu, zrodlo: CechyObrazu) -> None:
    """Zachowuje własne hashe i QR, a współrzędne cech ciężkich przelicza na cel."""
    cel.wektor = zrodlo.wektor.model_copy(deep=True) if zrodlo.wektor is not None else None
    cel.elementy_szablonu = zrodlo.elementy_szablonu.copy()
    cel.wersja_logo = zrodlo.wersja_logo
    cel.logo_obraz = [l.model_copy(update={"prostokat": _przenies(l.prostokat, zrodlo, cel)}) for l in zrodlo.logo_obraz]
    for t in zrodlo.twarze:
        pole = _przenies(t.prostokat, zrodlo, cel)
        punkty = tuple(_przenies((round(x), round(y), 1, 1), zrodlo, cel)[:2] for x, y in t.punkty)
        cel.twarze.append(t.model_copy(deep=True, update={"prostokat": pole, "punkty": punkty, "wielkosc_px": min(pole[2:])}))
    cel.niedostepne_modele_twarzy = zrodlo.niedostepne_modele_twarzy.copy()
    cel.braki.extend(b.model_copy() for b in zrodlo.braki if b.skladnik not in {"wczytywanie", "hashe", "qr"})
    cel.jak_obraz = zrodlo.sha256 or zrodlo.grupa_podobnych
