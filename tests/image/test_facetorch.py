"""Opis FaceTorch na sztucznym rastrze, bez pobierania modeli."""
import socket
from pathlib import Path

import numpy as np
import pytest

from fakecollector.features.image import na_rekord, opisy_twarzy
from fakecollector.features.image.konfiguracja import wczytaj_konfiguracje
from fakecollector.features.image.model import CechyObrazu, Emocja, Prostokat, Twarz, Wektor


def _twarz(pole: Prostokat) -> Twarz:
    wektor = Wektor(model="test", wersja="test", wymiar=2, wartosci=[1, 0])
    return Twarz(prostokat=pole, wielkosc_px=min(pole[2:]), punkty=((10, 10),) * 5,
                 pewnosc_wykrycia=1, jakosc=0.8, wektor=wektor)


def test_opisy_wiekszych_twarzy_i_rekord(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wycinki = []

    def predyktor(wycinek: np.ndarray) -> tuple[float, Emocja]:
        wycinki.append(wycinek.shape)
        return 0.7, Emocja(etykieta="Neutral", pewnosc=0.6)

    monkeypatch.setattr(opisy_twarzy, "predyktor_facetorch", lambda _: predyktor)
    raster = np.zeros((100, 100, 3), dtype=np.uint8)
    mala, duza, odrzucona = (_twarz(p) for p in ((0, 0, 25, 25), (5, 5, 50, 40), (10, 10, 60, 40)))
    cechy = CechyObrazu(twarze=[mala, duza, odrzucona])
    kandydaci = [(mala, mala.prostokat, True), (duza, duza.prostokat, True), (odrzucona, odrzucona.prostokat, False)]
    opisy_twarzy.opisz_twarze(raster, kandydaci, cechy, tmp_path / "model.json", 1)
    assert wycinki == [(40, 50, 3)]
    assert duza.deepfake == 0.7 and duza.emocja == Emocja(etykieta="Neutral", pewnosc=0.6)
    assert odrzucona.deepfake is None and odrzucona.emocja is None and odrzucona.brak_facetorch == "niska_jakosc"
    assert mala.deepfake is None and mala.brak_facetorch == "limit_twarzy"
    assert na_rekord(cechy)["deepfake_score"] == 0.7
    front = cechy.dla_frontu()["twarze"][1]
    assert front["uwaga_facetorch"] == "orientacyjnie"
    assert "wektor" not in front and "wektor_drugi" not in front


def test_brak_wag_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def siec(*args: object, **kwargs: object) -> None:
        pytest.fail("Predyktor próbował użyć sieci")

    monkeypatch.setattr(socket.socket, "connect", siec)
    monkeypatch.setattr(socket, "getaddrinfo", siec)
    twarz = _twarz((0, 0, 25, 25))
    cechy = CechyObrazu(twarze=[twarz])
    raster = np.zeros((25, 25, 3), dtype=np.uint8)
    opisy_twarzy.opisz_twarze(raster, [(twarz, twarz.prostokat, True)], cechy, tmp_path / "brak.json", 1)
    assert twarz.deepfake is None and twarz.emocja is None and twarz.brak_facetorch == "brak_wag"
    assert cechy.braki[0].powod.startswith("brak_wag")
    assert na_rekord(cechy)["deepfake_score"] is None


def test_odrzucenie_wyniku_spoza_skali(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opisy_twarzy, "predyktor_facetorch", lambda _: lambda obraz: (1.1, None))
    twarz = _twarz((0, 0, 25, 25))
    cechy = CechyObrazu(twarze=[twarz])
    raster = np.zeros((25, 25, 3), dtype=np.uint8)
    opisy_twarzy.opisz_twarze(raster, [(twarz, twarz.prostokat, True)], cechy, tmp_path / "model.json", 1)
    assert twarz.deepfake is None and twarz.emocja is None and twarz.brak_facetorch == "blad_predykcji"


@pytest.mark.wolne
def test_oficjalne_wagi_facetorch_bez_sieci(monkeypatch: pytest.MonkeyPatch) -> None:
    plik = Path(wczytaj_konfiguracje()["facetorch"]["manifest"])
    if not plik.is_file():
        pytest.skip("Brak lokalnego manifestu modeli FaceTorch")
    proby_polaczenia = []

    def siec(*args: object, **kwargs: object) -> None:
        proby_polaczenia.append(args)
        raise AssertionError("Predyktor próbował użyć sieci")

    monkeypatch.setattr(socket.socket, "connect", siec)
    monkeypatch.setattr(socket.socket, "connect_ex", siec)
    monkeypatch.setattr(socket, "getaddrinfo", siec)
    opisy_twarzy._wczytaj_predyktor.cache_clear()
    predyktor = opisy_twarzy.predyktor_facetorch(plik)
    raster = np.random.default_rng(0).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    wynik, emocja = predyktor(raster)
    assert 0 <= wynik <= 1 and 0 <= emocja.pewnosc <= 1
    assert (wynik, emocja) == predyktor(raster)
    assert not proby_polaczenia
