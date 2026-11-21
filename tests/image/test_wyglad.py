"""Kontrakt wyglądu i lokalne modele sprawdzane na sztucznych obrazach."""
from itertools import combinations
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

import fakecollector.features.image as modul
from fakecollector.features.image.elementy import na_wejscie, wycinki
from fakecollector.features.image.ewaluacja.ewaluacja_wygladu import _ranking_zatwierdzonych
from fakecollector.features.image.ewaluacja.kalibracja_wygladu import Ocena, podsumuj, prog_bez_falszywych
from fakecollector.features.image.konfiguracja import wczytaj_konfiguracje
from fakecollector.features.image.logo import ocen_logo, wybierz_logo
from fakecollector.features.image.model import CechyObrazu, Kontekst, Wektor
from fakecollector.features.image.wektor import KATALOG_MODELI, Macierz, cosinus, dopelnij_do_kwadratu, pobierz_model
from fakecollector.features.image.wzorzec import ParaKreacji, otworz_obraz, plik_danych, wczytaj_kreacje

CTX = Kontekst(zrodlo="test", rola="kreacja")


def _obraz() -> Image.Image:
    obraz = Image.new("RGB", (251, 120), "#c33462")
    rys = ImageDraw.Draw(obraz)
    rys.rectangle((0, 0, 83, 80), fill="blue")
    rys.rectangle((70, 70, 250, 119), fill="green")
    return obraz


class ModelTestowy:
    """Stabilne odpowiedzi do testowania integracji i reguły logo."""

    wersja = "1"

    def koduj_obrazy(self, obrazy: list[Image.Image]) -> Macierz:
        return np.tile(np.array([1, 0], dtype=np.float32), (len(obrazy), 1))

    def ocen_opisy(self, obrazy: Macierz, grupy: list[list[str]]) -> Macierz:
        return np.tile(np.linspace(0.4, 0.05, len(grupy), dtype=np.float32), (len(obrazy), 1))

    def rekord(self, wektor: Macierz) -> Wektor:
        return Wektor(model="test", wersja="1", wymiar=len(wektor), wartosci=wektor.tolist())


def test_interfejs_wektora_i_elementow(monkeypatch: pytest.MonkeyPatch) -> None:
    wywolania = []

    def model(sigiel: str) -> ModelTestowy:
        wywolania.append(sigiel)
        return ModelTestowy()

    monkeypatch.setattr(modul, "pobierz_model", model)
    obraz = _obraz()
    rekord = modul.analizuj_obraz(obraz, CTX)
    assert wywolania == ["clip", "siglip2"]
    assert rekord.wektor.wartosci == [1, 0]
    assert rekord.wektor.wersja == "1"
    assert set(rekord.elementy_szablonu) == set(wczytaj_konfiguracje()["szablon"]["elementy"])
    assert not rekord.logo_obraz
    assert "wektor" not in rekord.dla_frontu()
    assert {"wektor", "wektory_wycinkow", "elementy_szablonu", "logo_obraz"} <= rekord.czasy_ms.keys()
    wynik = modul.klasyfikuj(obraz, ["banner", "portrait", "banner"])
    assert wynik["banner"] > wynik["portrait"] and not wynik.braki and len(wynik) == 2
    assert modul.porownaj(rekord, rekord).metryki.cos_wyglad == 1
    assert cosinus(rekord.wektor, rekord.wektor.model_copy(update={"wersja": "2"})) is None


def test_brak_wag_zachowuje_hashe(monkeypatch: pytest.MonkeyPatch) -> None:
    def brak(_: str) -> ModelTestowy:
        raise FileNotFoundError("brak wag testowych")

    monkeypatch.setattr(modul, "pobierz_model", brak)
    rekord = modul.analizuj_obraz(_obraz(), CTX)
    assert rekord.hashe.phash and rekord.wektor is None
    assert any(b.skladnik == "wektor" and b.powod == "brak wag testowych" for b in rekord.braki)
    assert modul.klasyfikuj(_obraz(), ["banner"]).braki
    assert modul.porownaj(rekord, CechyObrazu()).metryki.cos_wyglad is None


def test_wycinki_i_render_kwadratowy() -> None:
    obraz = _obraz()
    kwadrat = dopelnij_do_kwadratu(obraz, 224)
    assert kwadrat.size == (224, 224)
    assert kwadrat.getpixel((0, 0)) == (255, 255, 255)
    assert len(wycinki(obraz, 0.4, 0.4, 0.3)) == 6
    assert na_wejscie((2, 3, 5, 10), (80, 40), (1, 2), 6) == (5, 32, 10, 5)


def test_negatyw_tvn24_wygrywa_nawet_z_wzorcem(tmp_path: Path) -> None:
    class ModelNegatywu(ModelTestowy):
        def ocen_opisy(self, obrazy: Macierz, grupy: list[list[str]]) -> Macierz:
            return np.tile(np.array([0.92, 0.99, 0.1], dtype=np.float32), (len(obrazy), 1))

    katalog = tmp_path / "TVN24"
    katalog.mkdir()
    _obraz().save(katalog / "wzorzec.png")
    wektory = np.array([[1, 0]], dtype=np.float32)
    model = ModelNegatywu()
    opisy = {"TVN24": ["testowy opis marki"]}
    kandydaci, braki = ocen_logo(model, wektory, opisy, 0.50, tmp_path)
    assert not kandydaci and not braki
    kandydaci, _ = ocen_logo(model, wektory, opisy, 1.0, tmp_path)
    assert kandydaci[0].zrodlo == "wzorzec" and kandydaci[0].wynik == 1
    obraz = _obraz()
    region = wycinki(obraz, 0.4, 0.4, 0.3)[1:2]
    logo, = wybierz_logo(kandydaci, region, {"TVN24": 0.9}, (260, 140), (2, 3), 1)
    assert logo.prostokat[:2] == (2, 3)


def test_prog_miedzy_negatywem_i_trafieniem() -> None:
    oceny = [Ocena("a", 0.7, True), Ocena("a", 0.2, True), Ocena("b", 0.3, False),
             Ocena("c", None, False)]
    wynik = podsumuj(oceny, 0.5)
    assert wynik["prog"] == pytest.approx(0.5)
    assert wynik["kalibracja"]["precyzja"] == 1
    assert wynik["kalibracja"]["czulosc"] == 0.5
    assert wynik["poza_proba"]["czulosc"] == 0
    assert prog_bez_falszywych([Ocena("a", 0.4, True), Ocena("b", 0.4, False)]) is None
    assert prog_bez_falszywych([Ocena("a", 0.7, False)]) is None


def test_prog_wygladu_dotyczy_par_szablonu() -> None:
    wektory = {"a": np.array([1.0, 0]), "b": np.array([0.8, 0.6]),
               "c": np.array([0.0, 1.0]), "d": np.array([0.96, 0.28]), "e": np.array([0.6, -0.8])}
    pary = [ParaKreacji(a=a, b=b, relacja=relacja, pewnosc="pewne", uzasadnienie="test")
            for a, b, relacja in (("a", "b", "ten_sam_szablon"),
                                  ("a", "c", "ta_sama_reklama"), ("a", "e", "rozne"))]
    wynik = _ranking_zatwierdzonych(wektory, pary)
    assert wynik["T_w"] == pytest.approx(0.7)
    assert wynik["pary"][0]["rangi"][0] == 2
    pary.append(ParaKreacji(a="a", b="d", relacja="rozne", pewnosc="pewne", uzasadnienie="test"))
    assert _ranking_zatwierdzonych(wektory, pary)["T_w"] is None
    del wektory["d"]
    assert _ranking_zatwierdzonych(wektory, pary)["T_w"] is None


def test_nieopisana_para_miedzy_zgloszeniami_ustala_prog() -> None:
    wektory = {"a": np.array([1., 0]), "b": np.array([.8, .6]), "c": np.array([.75, -(1 - .75**2)**.5])}
    zgloszenia = {"a": "1", "b": "2", "c": "3", "d": "4"}
    pary = [ParaKreacji(a="a", b="b", relacja="ten_sam_szablon", pewnosc="pewne", uzasadnienie="test")]
    wynik = _ranking_zatwierdzonych(wektory, pary, zgloszenia, zapas=.02)
    assert wynik["T_w"] == .77
    assert wynik["maks_cosinus_rozne"] == .75
    assert wynik["pary_rozne_z_wektorem"] == 2
    assert wynik["pary_rozne_bez_wektora"] == 3


def test_dowod_wygladu_wymaga_zgodnej_kalibracji(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = wczytaj_konfiguracje()
    cfg["wyglad"]["clip"].update(T_w=0.7, wersja_T_w="test")
    monkeypatch.setattr(modul, "wczytaj_konfiguracje", lambda: cfg)
    a = CechyObrazu(wektor=Wektor(model="ViT-B-32", wersja="test", wymiar=2, wartosci=[1, 0]))
    b = CechyObrazu(wektor=Wektor(model="ViT-B-32", wersja="test", wymiar=2, wartosci=[0.8, 0.6]))
    wynik = modul.porownaj(a, b)
    assert wynik.metryki.cos_wyglad == pytest.approx(0.8)
    assert wynik.dowody[0].rodzaj == "podobny_wyglad"
    a.sha256 = b.sha256 = "a" * 64
    assert [d.rodzaj for d in modul.porownaj(a, b).dowody] == ["identyczny_plik"]
    a.sha256, b.sha256 = "a" * 64, "b" * 64
    a.wektor.wersja = b.wektor.wersja = "inna"
    assert modul.porownaj(a, b).metryki.cos_wyglad == pytest.approx(0.8)
    assert not modul.porownaj(a, b).dowody


@pytest.mark.wolne
@pytest.mark.parametrize(("sigiel", "wymiar"), [("clip", 512), ("siglip2", 768)])
def test_lokalny_model_siglip_i_clip(sigiel: str, wymiar: int) -> None:
    if not (KATALOG_MODELI / f"{sigiel}.json").exists():
        pytest.skip(f"Wymagane lokalne wagi {sigiel}")
    model = pobierz_model(sigiel)
    obraz = _obraz()
    wektor = model.koduj_obrazy([obraz])
    assert wektor.shape == (1, wymiar)
    assert np.linalg.norm(wektor[0]) == pytest.approx(1.0)
    grupy = [["a red and green picture"], ["a tree in a snowy field"]]
    wynik = model.ocen_opisy(wektor, grupy)
    assert wynik.shape == (1, 2) and ((0 <= wynik) & (wynik <= 1)).all()
    assert np.allclose(wynik, model.ocen_opisy(wektor, grupy))
    assert pobierz_model(sigiel) is model


@pytest.mark.wolne
@pytest.mark.dane_nask
def test_skalibrowane_logo_i_szablon_na_kreacjach() -> None:
    dane = Path(__file__).resolve().parents[2] / "data"
    if not (dane / "nask/DANE").is_dir() or not (dane / "wzorzec/kreacje.json").is_file():
        pytest.skip("Potrzebne lokalne obrazy i adnotacje kreacji")
    cfg = wczytaj_konfiguracje()["wyglad"]
    if not all((KATALOG_MODELI / f"{cfg[klucz]}.json").is_file() for klucz in ("model", "model_logo")):
        pytest.skip("Potrzebne lokalne modele wyglądu i logo")
    zbior = wczytaj_kreacje(dane)
    wyniki = {}
    for k in zbior.kreacje:
        x, y, w, h = k.prostokat_kreacji
        obraz = otworz_obraz(plik_danych(dane, k.plik)).crop((x, y, x + w, y + h))
        wynik = modul.analizuj_obraz(obraz, CTX)
        wyniki[k.id] = wynik
        if k.oczekiwane.logo is not None:
            assert {logo.marka for logo in wynik.logo_obraz} == set(k.oczekiwane.logo), k.id
    for para in zbior.pary:
        if para.relacja in {"ten_sam_szablon", "rozne"}:
            wynik = modul.porownaj(wyniki[para.a], wyniki[para.b])
            assert wynik.metryki.cos_wyglad is not None
            assert any(d.rodzaj == "podobny_wyglad" for d in wynik.dowody) == (para.relacja == "ten_sam_szablon")
            assert all(d.rodzaj != "wspolne_elementy_szablonu" for d in wynik.dowody)
    etykiety = {frozenset((p.a, p.b)) for p in zbior.pary}
    for a, b in combinations(zbior.kreacje, 2):
        if a.zgloszenie != b.zgloszenie and frozenset((a.id, b.id)) not in etykiety:
            wynik = modul.porownaj(wyniki[a.id], wyniki[b.id])
            assert all(d.rodzaj != "podobny_wyglad" for d in wynik.dowody), (a.id, b.id)
