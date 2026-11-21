"""Odczyt QR w pamięci i porównanie dokładnej treści."""
from collections.abc import Iterator, Sequence
from math import ceil, floor
from urllib.parse import SplitResult, urlsplit, urlunsplit

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from .elementy import na_wejscie
from .konfiguracja import wczytaj_konfiguracje
from .model import Dowod, KodQR, Prostokat

Raster = NDArray[np.uint8]


def _adres(tekst: str) -> SplitResult | None:
    if any(znak.isspace() or ord(znak) < 32 for znak in tekst):
        return None
    try:
        adres = urlsplit(tekst)
        _ = adres.port
        return adres if adres.scheme in {"http", "https"} and adres.hostname else None
    except ValueError:
        return None


def normalizuj_qr(tekst: str) -> str:
    """Ujednolica host i końcowy ukośnik ścieżki w adresie HTTP(S)."""
    adres = _adres(tekst)
    if adres is None:
        return tekst
    dane, separator, host = adres.netloc.rpartition("@")
    return urlunsplit((adres.scheme, dane + separator + host.lower(), adres.path.removesuffix("/"),
                       adres.query, adres.fragment))


def _warianty(raster: Raster, skale: Sequence[int], maks_pikseli: int) -> Iterator[tuple[str, Raster, int]]:
    yield "oryginal", raster, 1
    szary = cv2.cvtColor(raster, cv2.COLOR_BGR2GRAY)
    yield "szarosc", szary, 1
    _, binarny = cv2.threshold(szary, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    yield "binaryzacja", binarny, 1
    h, w = szary.shape
    for skala in skale:
        if h * w * skala**2 <= maks_pikseli:
            wiekszy = cv2.resize(szary, (w * skala, h * skala), interpolation=cv2.INTER_CUBIC)
            yield f"powiekszenie_{skala}x", wiekszy, skala
    yield "odwrocone_kolory", cv2.bitwise_not(szary), 1


def _odczyty(czytnik: cv2.QRCodeDetector, raster: Raster) -> list[tuple[str, NDArray[np.float32]]]:
    try:
        _, teksty, narozniki, _ = czytnik.detectAndDecodeMulti(raster)
    except (cv2.error, UnicodeDecodeError):
        teksty, narozniki = (), None
    if narozniki is not None:
        wyniki = [(t, n) for t, n in zip(teksty, narozniki) if t]
        if wyniki:
            return wyniki
    try:
        tekst, naroznik, _ = czytnik.detectAndDecode(raster)
    except (cv2.error, UnicodeDecodeError):
        return []
    return [(tekst, naroznik)] if tekst and naroznik is not None else []


def _ramka(punkty: NDArray[np.float32], skala: int, w: int, h: int) -> Prostokat | None:
    x, y = np.asarray(punkty).reshape(-1, 2).T / skala
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        return None
    l, g = max(0, floor(x.min())), max(0, floor(y.min()))
    p, d = min(w, ceil(x.max())), min(h, ceil(y.max()))
    return (l, g, p - l, d - g) if l < p and g < d else None


def _pokrycie(a: Prostokat, b: Prostokat) -> float:
    x, y, w, h = a
    u, v, szer, wys = b
    czesc = max(0, min(x + w, u + szer) - max(x, u)) * max(0, min(y + h, v + wys) - max(y, v))
    return czesc / (w * h + szer * wys - czesc)


def odczytaj_qr(obraz: Image.Image, rozmiar_oryginalny: tuple[int, int] | None = None) -> list[KodQR]:
    """Próbuje obu czytników w kolejnych wariantach. Zapisuje pierwszą skuteczną próbę."""
    cfg = wczytaj_konfiguracje()
    qr = cfg["qr"]
    w, h = obraz.size
    raster = cv2.cvtColor(np.asarray(obraz), cv2.COLOR_RGB2BGR)
    czytniki = (cv2.QRCodeDetector(), cv2.QRCodeDetectorAruco())
    for proba, wariant, skala in _warianty(raster, qr["powiekszenia"], cfg["wczytywanie"]["maks_pikseli"]):
        wyniki = []
        for czytnik in czytniki:
            nazwa = type(czytnik).__name__
            for tekst, punkty in _odczyty(czytnik, wariant):
                ramka = _ramka(punkty, skala, w, h)
                if ramka is None:
                    continue
                pole = na_wejscie(ramka, rozmiar_oryginalny or (w, h), (0, 0),
                                 obraz.info.get("orientacja_exif", 1))
                if any(k.tekst == tekst and _pokrycie(k.prostokat, pole) >= qr["prog_iou_duplikatu"] for k in wyniki):
                    continue
                adres = _adres(tekst)
                wyniki.append(KodQR(tekst=tekst, prostokat=pole, metoda=f"{nazwa}/{proba}",
                                    czytnik=nazwa, proba=proba, czy_adres=adres is not None,
                                    domena=adres.hostname if adres else None))
        if wyniki:
            return wyniki
    return []


def porownaj_qr(a: Sequence[KodQR], b: Sequence[KodQR]) -> list[Dowod]:
    """Buduje dowody wspólnej treści z ramkami dla obu obrazów."""
    lewa, prawa = {}, {}
    for kody, wynik in ((a, lewa), (b, prawa)):
        for kod in kody:
            wynik.setdefault(normalizuj_qr(kod.tekst), []).append(kod.prostokat)
    return [Dowod(rodzaj="wspolny_qr", wynik=1, uzasadnienie="Identyczna treść kodu QR (1 zgodny ciąg)",
                  miejsca_a=lewa[klucz], miejsca_b=prawa[klucz])
            for klucz in sorted(lewa.keys() & prawa.keys())]
