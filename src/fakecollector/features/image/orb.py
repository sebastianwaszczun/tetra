"""Punkty ORB i weryfikacja wspólnego kadru dla par kandydatów."""
from dataclasses import dataclass
from math import ceil, floor
from threading import Lock
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from .elementy import na_wejscie
from .model import CechyObrazu, Dowod, Prostokat

Macierz = NDArray[np.generic]
_BLOKADA_RANSAC = Lock()


@dataclass(frozen=True)
class PunktyORB:
    polozenia: Macierz
    deskryptory: Macierz
    rozmiar: tuple[int, int]


def zachowaj_raster_orb(cechy: CechyObrazu, raster: Image.Image, bok: int) -> None:
    """Zachowuje mały raster bez ekstrakcji deskryptorów."""
    szary = raster.convert("L")
    szary.thumbnail((bok, bok), Image.Resampling.LANCZOS)
    cechy._raster_orb = szary


def _punkty(cechy: CechyObrazu, cfg: dict[str, Any]) -> PunktyORB | None:
    raster = cechy._raster_orb
    if raster is None:
        return None
    klucz = (cfg["bok_px"], cfg["maks_punktow"])
    if klucz not in cechy._cechy_orb:
        detektor = cv2.ORB_create(nfeatures=cfg["maks_punktow"])
        punkty, opis = detektor.detectAndCompute(np.asarray(raster), None)
        cechy._cechy_orb[klucz] = (PunktyORB(np.array([p.pt for p in punkty], np.float32), opis, raster.size)
                                  if opis is not None else None)
    return cechy._cechy_orb[klucz]


def _dopasowania(a: Macierz, b: Macierz, iloraz: float) -> dict[int, int]:
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pary = matcher.knnMatch(a, b, k=2)
    return {m.queryIdx: m.trainIdx for para in pary if len(para) == 2
            for m, n in [para] if m.distance < iloraz * n.distance}


def _rogi(w: int, h: int) -> Macierz:
    return np.array(((0, 0), (w, 0), (w, h), (0, h)), dtype=np.float32)


def _wspolny_obszar(H: Macierz, zrodlo: PunktyORB, cel: PunktyORB,
                    cfg: dict[str, Any]) -> tuple[float, Macierz, float, float] | None:
    rogi = _rogi(*zrodlo.rozmiar)
    mianownik = np.column_stack((rogi, np.ones(len(rogi)))) @ H[2]
    if not np.isfinite(mianownik).all() or np.min(mianownik) <= 0 <= np.max(mianownik):
        return None
    obrys = cv2.perspectiveTransform(rogi[None], H)[0]
    if not np.isfinite(obrys).all() or not cv2.isContourConvex(obrys):
        return None
    pole = cv2.contourArea(obrys, oriented=True)
    if pole <= 0:
        return None
    skala = (pole / np.prod(zrodlo.rozmiar)) ** 0.5
    if not cfg["min_skala"] <= skala <= cfg["maks_skala"]:
        return None
    krawedzie = np.linalg.norm(np.roll(obrys, -1, axis=0) - obrys, axis=1)
    wzgledne = krawedzie / (*zrodlo.rozmiar, *zrodlo.rozmiar)
    if min(wzgledne) <= 0:
        return None
    # Stosunek pola do sąsiednich krawędzi odrzuca silne ścinanie i spłaszczone czworokąty.
    znieksztalcenie = max(max(wzgledne) / min(wzgledne), max(krawedzie * np.roll(krawedzie, -1)) / pole)
    if znieksztalcenie > cfg["maks_znieksztalcenie"]:
        return None
    pole, wspolne = cv2.intersectConvexConvex(obrys, _rogi(*cel.rozmiar))
    if wspolne is None or pole <= 0:
        return None
    return float(pole), wspolne.reshape(-1, 2), float(skala), float(znieksztalcenie)


def _ramka(punkty: Macierz, rozmiar_orb: tuple[int, int], cechy: CechyObrazu) -> Prostokat:
    w, h = cechy.rozmiar
    xy = np.clip(punkty * (w / rozmiar_orb[0], h / rozmiar_orb[1]), (0, 0), (w, h))
    x, y = map(floor, xy.min(axis=0))
    p, d = map(ceil, xy.max(axis=0))
    przygotowanie = cechy.przygotowanie
    return na_wejscie((x, y, max(1, p - x), max(1, d - y)), przygotowanie.rozmiar_oryginalny,
                      przygotowanie.odciete_ramki[:2], przygotowanie.orientacja_exif)


def porownaj_orb(a: CechyObrazu, b: CechyObrazu, cfg: dict[str, Any]) -> tuple[dict[str, Any], Dowod | None]:
    """Dopasowuje wzajemnych sąsiadów i sprawdza homografię w obie strony."""
    lewy, prawy = _punkty(a, cfg), _punkty(b, cfg)
    m: dict[str, Any] = {"dopasowania": 0, "zgodne": 0}
    if lewy is None or prawy is None:
        return {**m, "brak": "brak punktów ORB lub rastra"}, None
    ab = _dopasowania(lewy.deskryptory, prawy.deskryptory, cfg["iloraz_hamminga"])
    ba = _dopasowania(prawy.deskryptory, lewy.deskryptory, cfg["iloraz_hamminga"])
    wspolne = [(i, j) for i, j in sorted(ab.items()) if ba.get(j) == i]
    m["dopasowania"] = len(wspolne)
    if len(wspolne) < 4:
        return m, None
    i, j = zip(*wspolne, strict=True)
    x, y = lewy.polozenia[list(i)], prawy.polozenia[list(j)]
    with _BLOKADA_RANSAC:
        cv2.setRNGSeed(0)
        H, zgodne = cv2.findHomography(x, y, cv2.RANSAC, cfg["blad_ransac_px"])
    if H is None or zgodne is None or not np.isfinite(H).all():
        return m, None
    inlier = zgodne.ravel().astype(bool)
    liczba = int(inlier.sum())
    m.update(zgodne=liczba, udzial_zgodnych=liczba / len(wspolne))
    if liczba < 4:
        return m, None
    try:
        odwrotna = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return m, None
    do_a = _wspolny_obszar(odwrotna, prawy, lewy, cfg)
    do_b = _wspolny_obszar(H, lewy, prawy, cfg)
    if do_a is None or do_b is None:
        return {**m, "brak": "niespójna geometria homografii"}, None
    pole_a, ksztalt_a, skala_a, ksztalt_miara_a = do_a
    pole_b, ksztalt_b, skala_b, ksztalt_miara_b = do_b
    pokrycia = (float(pole_a / np.prod(lewy.rozmiar)), float(pole_b / np.prod(prawy.rozmiar)))
    rozrzuty = (cv2.contourArea(cv2.convexHull(x[inlier])) / pole_a,
                cv2.contourArea(cv2.convexHull(y[inlier])) / pole_b)
    m.update(pokrycie_a=pokrycia[0], pokrycie_b=pokrycia[1], rozrzut_min=min(rozrzuty),
             skala_min=min(skala_a, skala_b), skala_max=max(skala_a, skala_b),
             znieksztalcenie_max=max(ksztalt_miara_a, ksztalt_miara_b))
    if (liczba < cfg["min_punkty"] or m["udzial_zgodnych"] < cfg["min_udzial_punktow"]
            or min(pokrycia) < cfg["min_pokrycie"] or min(rozrzuty) < cfg["min_rozrzut"]):
        return m, None
    opis = f"Wspólny kadr, {liczba} zgodnych punktów. Pokrycie obrazów {min(pokrycia):.0%} i {max(pokrycia):.0%}"
    return m, Dowod(rodzaj="ten_sam_kadr", wynik=m["udzial_zgodnych"], uzasadnienie=opis,
                    miejsca_a=[_ramka(ksztalt_a, lewy.rozmiar, a)], miejsca_b=[_ramka(ksztalt_b, prawy.rozmiar, b)],
                    szczegoly={"zgodne": liczba, "pokrycie_min": min(pokrycia)})
