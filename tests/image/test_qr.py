"""Testy odczytu QR wyłącznie na kodach wygenerowanych w OpenCV."""
import numpy as np
import pytest
from PIL import Image

import fakecollector.features.image as obraz
from fakecollector.features.image.konfiguracja import wczytaj_konfiguracje
from fakecollector.features.image.ewaluacja.ewaluacja_qr import ProbaQR, proby_qr
from fakecollector.features.image.model import CechyObrazu, KodQR, Kontekst
from fakecollector.features.image.qr import normalizuj_qr, odczytaj_qr


@pytest.mark.parametrize("proba", proby_qr(), ids=lambda p: p.nazwa)
def test_odczyty_na_sztucznych_kodach(proba: ProbaQR) -> None:
    kody = odczytaj_qr(Image.fromarray(proba.raster).convert("RGB"))
    odczytane = {k.tekst for k in kody}
    assert odczytane <= set(proba.oczekiwane)
    if proba.modul_px is None or proba.modul_px >= wczytaj_konfiguracje()["qr"]["minimalny_modul_px"]:
        assert odczytane == set(proba.oczekiwane)
    assert len(kody) == len(odczytane)
    for k in kody:
        assert k.domena == "example.com" and k.czy_adres
        assert k.czytnik in {"QRCodeDetector", "QRCodeDetectorAruco"}
        assert k.proba and k.metoda == f"{k.czytnik}/{k.proba}"
        x, y, w, h = k.prostokat
        assert x + w <= proba.raster.shape[1] and y + h <= proba.raster.shape[0]
    if proba.nazwa == "odwrocone_kolory":
        assert all(k.proba == "odwrocone_kolory" for k in kody)


def test_qr_na_ubogim_obrazie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(obraz, "analizuj_twarze", lambda *_: None)
    maly = proby_qr()[2]
    tlo = np.pad(maly.raster, 130, constant_values=255)
    wynik = obraz.analizuj_obraz(Image.fromarray(tlo).convert("RGB"),
                                 Kontekst(zrodlo="syntetyczny QR", rola="kreacja"))
    assert wynik.ubogi_w_tresc
    assert wynik.qr[0].tekst == maly.oczekiwane[0]
    assert wynik.czasy_ms["qr"] >= 0


def test_normalizacja_i_dowod_bez_laczenia_po_domenie() -> None:
    a = KodQR(tekst="https://EXAMPLE.COM/test-qr-1/?Q=Ab", prostokat=(1, 2, 16, 16),
              metoda="test", czy_adres=True)
    b = a.model_copy(update={"tekst": "https://example.com/test-qr-1?Q=Ab",
                             "prostokat": (20, 30, 20, 20)})
    c = b.model_copy(update={"tekst": "https://example.com/test-qr-2?Q=Ab"})
    assert normalizuj_qr(a.tekst) == b.tekst
    assert normalizuj_qr("https://[niepoprawny") == "https://[niepoprawny"
    assert normalizuj_qr("zwykła treść/") == "zwykła treść/"
    zapis_a, zapis_b = CechyObrazu(qr=[a]), CechyObrazu(qr=[b])
    dowod, = obraz.porownaj(zapis_a, zapis_b).dowody
    assert dowod.rodzaj == "wspolny_qr" and dowod.miejsca_a == [a.prostokat] and dowod.miejsca_b == [b.prostokat]
    assert not obraz.porownaj(zapis_a, CechyObrazu(qr=[c])).dowody
    assert obraz.klucze_wyszukiwania(zapis_a).qr == [b.tekst]
