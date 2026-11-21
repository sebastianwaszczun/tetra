"""Sprawdziany QR na kodach generowanych w OpenCV i pomiar lokalny."""
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import cv2
import numpy as np
from PIL import Image

from ..przygotowanie import przygotuj_z_pelnym_rastrem
from ..qr import Raster, odczytaj_qr
from ..wzorzec import otworz_obraz, plik_danych, wczytaj_kreacje


@dataclass(frozen=True)
class ProbaQR:
    nazwa: str
    raster: Raster
    oczekiwane: tuple[str, ...]
    modul_px: int | None = None


def _kod(tekst: str, modul: int) -> Raster:
    qr = cv2.QRCodeEncoder_create().encode(tekst)
    qr = np.pad(qr, 4, constant_values=255)
    return np.repeat(np.repeat(qr, modul, axis=0), modul, axis=1)


def _jpeg(raster: Raster, jakosc: int) -> Raster:
    ok, bufor = cv2.imencode(".jpg", raster, [cv2.IMWRITE_JPEG_QUALITY, jakosc])
    if not ok:
        raise ValueError("Nie można zakodować sztucznego obrazu testowego do JPEG")
    return cv2.imdecode(bufor, cv2.IMREAD_GRAYSCALE)


def proby_qr() -> list[ProbaQR]:
    """Stałe próbki do raportu i testów. Każda dozwolona treść jest znana z góry."""
    tekst, drugi = ("https://example.com/test-qr-1", "https://example.com/test-qr-2")
    wyniki = [ProbaQR(f"modul_{i}px", _kod(tekst, i), (tekst,), i) for i in range(1, 5)]
    kod = _kod(tekst, 4)
    h, w = kod.shape
    gradient = np.broadcast_to(np.linspace(70, 235, w + 72, dtype=np.uint8), (h + 72, w + 72)).copy()
    los = np.random.default_rng(41)
    szum = los.integers(55, 240, size=gradient.shape, dtype=np.uint8)
    for nazwa, tlo in (("gradient", gradient.copy()), ("szum", szum.copy())):
        tlo[36:36 + h, 36:36 + w] = kod
        wyniki.append(ProbaQR(nazwa, tlo, (tekst,)))
    duzy = np.pad(kod, 50, constant_values=255)
    hh, ww = duzy.shape
    for kat in (0, 15, 45):
        transformacja = cv2.getRotationMatrix2D((ww / 2, hh / 2), kat, 1)
        obrot = cv2.warpAffine(duzy, transformacja, (ww, hh), borderValue=255)
        wyniki.append(ProbaQR(f"obrot_{kat}", obrot, (tekst,)))
    wejscie = np.float32([[0, 0], [ww - 1, 0], [ww - 1, hh - 1], [0, hh - 1]])
    wyjscie = np.float32([[18, 7], [ww - 20, 23], [ww - 6, hh - 13], [5, hh - 38]])
    perspektywa = cv2.warpPerspective(duzy, cv2.getPerspectiveTransform(wejscie, wyjscie), (ww, hh), borderValue=255)
    wyniki.extend([
        ProbaQR("perspektywa", perspektywa, (tekst,)),
        ProbaQR("jpeg_q70", _jpeg(kod, 70), (tekst,)),
        ProbaQR("jpeg_q30", _jpeg(kod, 30), (tekst,)),
        ProbaQR("odwrocone_kolory", cv2.bitwise_not(kod), (tekst,)),
        ProbaQR("dwa_kody", np.hstack((kod, np.full((h, 32), 255, np.uint8), _kod(drugi, 4))), (tekst, drugi)),
        ProbaQR("bez_kodu_gradient", gradient, ()),
        ProbaQR("bez_kodu_szum", szum, ()),
    ])
    return wyniki


def ewaluuj_qr(dane: Path) -> dict[str, Any]:
    """Mierzy skuteczność na sztucznych danych i wykonuje lokalny sprawdzian NASK."""
    proby = []
    for p in proby_qr():
        obraz = Image.fromarray(p.raster).convert("RGB")
        start = perf_counter()
        odczyty = odczytaj_qr(obraz)
        czas = (perf_counter() - start) * 1000
        oczekiwane = set(p.oczekiwane)
        odczytane = {w.tekst for w in odczyty}
        proby.append({"nazwa": p.nazwa, "modul_px": p.modul_px, "odczytano": len(odczytane & oczekiwane),
                      "oczekiwano": len(oczekiwane), "falszywe": len(odczytane - oczekiwane),
                      "metody": [w.metoda for w in odczyty], "czas_ms": round(czas, 2)})
    minimum = min((p["modul_px"] for p in proby if p["modul_px"] is not None
                   and p["odczytano"] == p["oczekiwano"] and not p["falszywe"]), default=None)
    nask = []
    plik = dane / "wzorzec/kreacje.json"
    if plik.is_file():
        for k in wczytaj_kreacje(dane).kreacje:
            x, y, w, h = k.prostokat_kreacji
            wycinek = otworz_obraz(plik_danych(dane, k.plik)).crop((x, y, x + w, y + h))
            _, cechy, pelny = przygotuj_z_pelnym_rastrem(wycinek)
            odczyty = odczytaj_qr(pelny, cechy.przygotowanie.rozmiar_oryginalny) if pelny is not None else None
            nask.append({"id": k.id, "odczyty": [k.model_dump(mode="json") for k in odczyty]
                         if odczyty is not None else None})
    return {"proby": proby, "minimalny_modul_px": minimum, "falszywe": sum(p["falszywe"] for p in proby),
            "nask": nask}
