"""Porównania na sztucznych obrazach oraz odrębne próby lokalne."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

import pytest
from PIL import Image, ImageEnhance

import fakecollector.features.image as modul
from fakecollector.features.image.ewaluacja.ewaluacja_porownania import wyniki_par
from fakecollector.features.image.konfiguracja import wczytaj_konfiguracje
from fakecollector.features.image.model import CechyObrazu, Hashe, KodQR, Kontekst, Logo, Twarz, Wektor
from fakecollector.features.image.ewaluacja.proba_generalna import analiza, wykonaj_probe, wycinek
from fakecollector.features.image.przygotowanie import czytaj_bajty
from fakecollector.features.image.raport_html import MaterialHTML, ParaHTML, zapisz_raport_html
from fakecollector.features.image.twarze import MODEL_TWARZY
from fakecollector.features.image.warianty import scena_syntetyczna, warianty_porownania
from fakecollector.features.image.wzorzec import plik_danych, wczytaj_kreacje, wczytaj_pary

CTX = Kontekst(zrodlo="test", rola="kreacja")


def _hash(n: int) -> Hashe:
    return Hashe(phash=(f"{n:016x}",) * 3, dhash=(f"{n:016x}",) * 3)


def _wyglad(wartosci: list[float], wersja: str | None = None) -> Wektor:
    wersja = wersja or wczytaj_konfiguracje()["wyglad"]["clip"]["wersja_T_w"]
    return Wektor(model="ViT-B-32", wersja=wersja, wymiar=len(wartosci), wartosci=wartosci)


def _twarz(v1: list[float], v2: list[float] | None = None, jakosc: float = 0.9, osoba: str | None = None) -> Twarz:
    a, b = (Wektor(model=MODEL_TWARZY[s], wersja=wczytaj_konfiguracje()["twarze"][s]["wersja_kalibracji"],
                   wymiar=len(v), wartosci=v) if v is not None else None
            for s, v in zip(MODEL_TWARZY, (v1, v2), strict=True))
    return Twarz(prostokat=(2, 4, 50, 60), punkty=((10, 10),) * 5, pewnosc_wykrycia=1, wielkosc_px=50,
                 jakosc=jakosc, wektor=a, wektor_drugi=b, osoba=osoba, status_osoby="rozpoznana" if osoba else "nieznana")


def _qr(x: int = 1) -> KodQR:
    return KodQR(tekst="https://example.com/test/", prostokat=(x, 2, 20, 20), metoda="test", czy_adres=True)


def _logo() -> Logo:
    return Logo(marka="Test", rog="dol", prostokat=(1, 5, 20, 10), pewnosc=0.9, zrodlo="opis")


def test_identycznosc_duplikat_i_ubogi() -> None:
    a = CechyObrazu(sha256="a" * 64, hashe=_hash(0), wektor=_wyglad([1, 0]), qr=[_qr()], logo_obraz=[_logo()])
    b = CechyObrazu(sha256="a" * 64, hashe=_hash(1), wektor=_wyglad([1, 0]), qr=[_qr(8)], logo_obraz=[_logo()])
    wynik = modul.porownaj(a, b)
    assert [(d.rodzaj, d.sila) for d in wynik.dowody] == [("identyczny_plik", "silny")]
    assert wynik.metryki.phash_min == 1 and wynik.metryki.cos_wyglad == 1 and wynik.metryki.wspolne_qr == 1
    b.sha256 = "b" * 64
    wynik = modul.porownaj(a, b)
    assert [d.rodzaj for d in wynik.dowody] == ["duplikat_kreacji"]
    assert wynik.metryki.orb is None
    a.ubogi_w_tresc = True
    wynik = modul.porownaj(a, b)
    assert [d.rodzaj for d in wynik.dowody] == ["wspolny_qr"]
    assert wynik.metryki.phash_min is None and wynik.metryki.cos_wyglad is None
    odwrotnie = modul.porownaj(b, a)
    assert odwrotnie.metryki == wynik.metryki
    assert odwrotnie.dowody[0].miejsca_a == wynik.dowody[0].miejsca_b


def test_wersje_i_pospolite_elementy() -> None:
    a = CechyObrazu(wektor=_wyglad([1, 0]), elementy_szablonu={"przycisk_odtwarzania": 1, "pasek_tekst_dol": 1})
    b = a.model_copy(deep=True, update={"wektor": _wyglad([0, 1])})
    wynik = modul.porownaj(a, b)
    assert len(wynik.metryki.wspolne_elementy) == 2 and not wynik.dowody
    for c in (a, b):
        c.elementy_szablonu.update(studio_prezenter=1, ulica_samochody_z_gory=1)
    assert [d.rodzaj for d in modul.porownaj(a, b).dowody] == ["wspolne_elementy_szablonu"]
    b.wektor.wersja = "inna"
    wynik = modul.porownaj(a, b)
    assert wynik.metryki.cos_wyglad is None and not wynik.dowody
    assert any("wersja" in brak.powod for brak in wynik.braki)


def test_dwa_glosy_twarzy_i_awaria() -> None:
    a = CechyObrazu(twarze=[_twarz([1, 0], [1, 0])])
    b = CechyObrazu(twarze=[_twarz([1, 0], [0, 1])])
    wynik = modul.porownaj(a, b)
    assert wynik.metryki.cos_twarzy == {"buffalo_l": 1, "yunet_sface": 0}
    assert not wynik.dowody
    b.twarze.append(_twarz([0, 1], [1, 0]))
    pomiar = modul.porownaj(a, b)
    assert not pomiar.dowody and pomiar.metryki.cos_twarzy == {"buffalo_l": 1, "yunet_sface": 1}
    assert [p.cosinusy for p in pomiar.metryki.cos_par_twarzy] == [
        {"buffalo_l": 1, "yunet_sface": 0}, {"buffalo_l": 0, "yunet_sface": 1}]
    assert [(p.indeks_a, p.indeks_b) for p in modul.porownaj(b, a).metryki.cos_par_twarzy] == [(0, 0), (1, 0)]
    b.twarze.pop()
    b.twarze[0].wektor_drugi = None
    assert not modul.porownaj(a, b).dowody
    b.niedostepne_modele_twarzy = ["yunet_sface"]
    wynik = modul.porownaj(a, b)
    dowod, = wynik.dowody
    assert dowod.sila == "silny" and dowod.szczegoly["tryb_awaryjny"] and any(w.skladnik == "twarze" for w in wynik.braki)
    b.twarze[0].jakosc = 0.1
    assert not modul.porownaj(a, b).dowody
    b.twarze = [_twarz([1, 0], [1, 0])]
    b.niedostepne_modele_twarzy = []
    b.twarze[0].wektor_drugi.wersja = "inna"
    wynik = modul.porownaj(a, b)
    assert not wynik.dowody and any(w.skladnik == "cos_twarzy" for w in wynik.braki)


def test_orb_warianty_i_symetria(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = wczytaj_konfiguracje()
    cfg["hashe"]["dowod"] = False
    monkeypatch.setattr(modul, "wczytaj_konfiguracje", lambda: cfg)
    obraz = scena_syntetyczna(127)
    _, a = modul.analizuj_podstawy(obraz)
    a.wektor = _wyglad([1, 0])
    wymagane = {"pasek_15", "przyciecie_10", "przyciecie_20", "przyciecie_30", "wstawka"}
    for w in warianty_porownania(obraz):
        if w.nazwa not in wymagane:
            continue
        _, b = modul.analizuj_podstawy(w.obraz)
        b.wektor = _wyglad([0.65, 0.7599342077])
        wynik = modul.porownaj(a, b)
        assert wynik == modul.porownaj(a, b)
        dowod, = wynik.dowody
        assert dowod.rodzaj == "ten_sam_kadr" and dowod.miejsca_a and dowod.miejsca_b, w.nazwa
        assert dowod.sila == "silny" and dowod.szczegoly["zgodne"] >= cfg["orb"]["min_punkty"]
        odwrotny, = modul.porownaj(b, a).dowody
        assert odwrotny.wynik == dowod.wynik and odwrotny.miejsca_a == dowod.miejsca_b
    _, inne = modul.analizuj_podstawy(scena_syntetyczna(325))
    assert modul.porownaj(a, inne).metryki.orb is None
    inne.wektor = b.wektor
    assert not modul.porownaj(a, inne).dowody


def test_partia_stu_klatek_i_deduplikacja(monkeypatch: pytest.MonkeyPatch) -> None:
    porcje = []

    def ciezkie(partia: list, konfiguracja: dict) -> None:
        porcje.append(len(partia))
        for obraz, c in partia:
            c.wektor = _wyglad([1, float(obraz.getpixel((10, 10))[0])])
            c.logo_obraz = [_logo()]

    monkeypatch.setattr(modul, "_analizuj_ciezkie", ciezkie)
    monkeypatch.setattr(modul, "odczytaj_qr", lambda *_: [])
    scena = [scena_syntetyczna(seed, (240, 180)) for seed in range(10)]
    wejscia = [(ImageEnhance.Brightness(obraz).enhance(1 + (i - 4) / 200), CTX)
               for obraz in scena for i in range(10)]
    wynik = modul.analizuj_obrazy(wejscia)
    assert len(wynik) == 100 and 10 <= wynik.wywolania_ciezkich_modeli <= 12
    assert sum(porcje) == wynik.wywolania_ciezkich_modeli and max(porcje) > 1
    assert {"tani", "qr", "grupowanie", "ciezkie", "dziedziczenie"} <= wynik.czasy_ms.keys()
    for c, (r, ctx) in zip(wynik, wejscia, strict=True):
        if c.jak_obraz is None:
            pojedynczy = modul.analizuj_obraz(r, ctx)
            assert c.wektor == pojedynczy.wektor and c.logo_obraz == pojedynczy.logo_obraz
    with BytesIO() as plik:
        scena[0].save(plik, "PNG")
        dane = plik.getvalue()
    dwie = modul.analizuj_obrazy([(dane, CTX), (dane, CTX)])
    assert dwie.wywolania_ciezkich_modeli == 1 and dwie[1].jak_obraz == dwie[0].sha256


def test_zbierz_i_szukaj() -> None:
    a = CechyObrazu(sha256="a" * 64, hashe=_hash(0), wektor=_wyglad([1, 0]), qr=[_qr()],
                   logo_obraz=[_logo()], twarze=[_twarz([1, 0], [1, 0], 0.81, "Osoba testowa")], elementy_szablonu={"a": 0.2})
    b = CechyObrazu(sha256="b" * 64, hashe=_hash(1), wektor=_wyglad([0, 1]), qr=[_qr(2)],
                   logo_obraz=[_logo()], twarze=[_twarz([1, 0], [1, 0], 0.92, "Osoba testowa")], elementy_szablonu={"a": 0.7, "b": 1})
    suma = modul.zbierz([a, b])
    assert suma.elementy == {"a": 0.7, "b": 1} and len(suma.hashe) == len(suma.wektory) == 2
    assert len(suma.logo) == len(suma.qr) == len(suma.twarze) == 1 and suma.twarze[0].jakosc == 0.92
    assert suma.wektor_sredni.wartosci == pytest.approx([2 ** -0.5] * 2)
    ka, kb = map(modul.klucze_wyszukiwania, (a, b))
    assert ka.phash == [0, 0, 0] and kb.dhash == [1, 1, 1] and len(ka.wektory_twarzy) == 2
    assert modul.szukaj_kandydatow(ka, {"bliski": kb, "obcy": modul.klucze_wyszukiwania(CechyObrazu(hashe=_hash(2**64 - 1)))}) == ["bliski"]
    a.twarze, b.twarze = [_twarz([1, 0], jakosc=0.81)], [_twarz([1, 0], jakosc=0.92)]
    assert len(modul.zbierz([a, b]).twarze) == 2
    b.niedostepne_modele_twarzy = ["yunet_sface"]
    assert [t.jakosc for t in modul.zbierz([a, b]).twarze] == [0.92]


def test_html_jest_lokalny_i_bez_wektorow(tmp_path: Path) -> None:
    c = CechyObrazu(twarze=[_twarz([0.86123456, 0], [1, 0])], qr=[_qr()])
    obraz = Image.new("RGB", (100, 100), "white")
    a = MaterialHTML("<script>", obraz, c)
    b = MaterialHTML("drugi", obraz, c)
    plik = tmp_path / "pary.html"
    zapisz_raport_html(plik, [ParaHTML(a, b, "wariant", modul.porownaj(c, c))], "pomiar")
    html = plik.read_text(encoding="utf-8")
    assert "data:image/jpeg;base64," in html and "&lt;script&gt;" in html and "<script>" not in html
    assert "0.86123456" not in html and "https://example.com" not in html


def test_proba_syntetyczna_odklada_cala_kampanie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fakecollector.features.image.ewaluacja import proba_generalna as proba

    for folder in ("wzorzec", "nask/DANE", "proba_generalna/niezwiazane"):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    kreacje = []
    for i in range(3):
        obraz = scena_syntetyczna(i)
        plik = f"nask/DANE/{i}.png"
        obraz.save(tmp_path / plik)
        kreacje.append({"id": f"k{i}", "plik": plik, "zgloszenie": str(i), "prostokat_kreacji": (0, 0, *obraz.size),
                        "status": "zatwierdzone", "oczekiwane": {}})
    (tmp_path / "wzorzec/kreacje.json").write_text(json.dumps({"wersja": 1, "kreacje": kreacje, "pary": []}), encoding="utf-8")
    wynik, pary = wykonaj_probe(tmp_path)
    assert wynik["status"] == "pominieta" and "brak negatywów" in wynik["powod"] and not pary
    scena_syntetyczna(400).save(tmp_path / "proba_generalna/niezwiazane/test.png")

    def lekka_analiza(nazwa: str, obraz: Image.Image) -> MaterialHTML:
        return MaterialHTML(nazwa, obraz, modul.analizuj_podstawy(obraz)[1])

    monkeypatch.setattr(proba, "analiza", lekka_analiza)
    zakresy = []
    kalibruj = proba._kalibruj_grupe

    def zmierz(trening: list, *args: object) -> dict:
        zakresy.append(sorted(k.zgloszenie for k in trening))
        return kalibruj(trening, *args)

    monkeypatch.setattr(proba, "_kalibruj_grupe", zmierz)
    wynik, pary = wykonaj_probe(tmp_path)
    assert wynik["status"] == "zaliczona" and wynik["falszywe_silne_srednie"] == 0
    assert zakresy == [["1", "2"], ["0", "2"], ["0", "1"]]
    assert sum(p.relacja == "wariant" for p in pary) == 3 * 8


def _dane() -> Path:
    dane = Path(__file__).resolve().parents[2] / "data"
    if not (dane / "nask/DANE").is_dir() or not (dane / "wzorzec/kreacje.json").is_file():
        pytest.skip("Brak lokalnych danych NASK lub adnotacji")
    return dane


@pytest.mark.dane_nask
@pytest.mark.wolne
def test_pary_plikow_nask() -> None:
    dane = _dane()
    if not (dane / "wzorzec/pary_obrazow.json").is_file():
        pytest.skip("Brak par obrazów")
    limit = wczytaj_konfiguracje()["wczytywanie"]["maks_bajtow"]
    for wpis in wczytaj_pary(dane):
        pliki = [czytaj_bajty(plik_danych(dane, s), limit) for s in (wpis.a, wpis.b)]
        if wpis.relacja == "identyczne":
            a, b = [modul.analizuj_podstawy(plik)[1] for plik in pliki]
            assert a.sha256 == b.sha256 == sha256(pliki[0]).hexdigest()
            assert [d.rodzaj for d in modul.porownaj(a, b).dowody] == ["identyczny_plik"]
        elif wpis.relacja == "rozne":
            a, b = [modul.analizuj_obraz(plik, CTX) for plik in pliki]
            assert all(d.sila == "slaby" for d in modul.porownaj(a, b).dowody)


@pytest.mark.dane_nask
@pytest.mark.wolne
def test_zatwierdzone_pary_kreacji() -> None:
    dane = _dane()
    zbior = wczytaj_kreacje(dane)
    zatwierdzone = {k.id: k for k in zbior.kreacje if k.status == "zatwierdzone"}
    pary = [p for p in zbior.pary if {p.a, p.b} <= zatwierdzone.keys()]
    if not pary:
        pytest.skip("Brak zatwierdzonych par kreacji")
    wyniki = {k: analiza(k, wycinek(dane, zatwierdzone[k])) for k in {i for p in pary for i in (p.a, p.b)}}
    kontrola = []
    for p in pary:
        a, b = wyniki[p.a], wyniki[p.b]
        wynik = modul.porownaj(a.cechy, b.cechy)
        kontrola.append(ParaHTML(a, b, p.relacja, wynik))
        if p.relacja == "rozne":
            assert all(d.sila == "slaby" for d in wynik.dowody), (p.a, p.b, wynik.dowody)
        elif p.relacja == "ten_sam_szablon":
            assert any(d.rodzaj in {"podobny_wyglad", "wspolne_elementy_szablonu"} for d in wynik.dowody)
        elif p.relacja == "duplikat":
            assert [d.rodzaj for d in wynik.dowody] in (["identyczny_plik"], ["duplikat_kreacji"], ["ten_sam_kadr"])
        elif not any(c.ubogi_w_tresc or c.za_maly for c in (a.cechy, b.cechy)):
            assert any(d.sila in {"silny", "sredni"} for d in wynik.dowody), (p.a, p.b)
    assert wyniki_par(kontrola, wczytaj_konfiguracje())["porownania"] == len(pary)


@pytest.mark.dane_nask
@pytest.mark.wolne
def test_proba_generalna() -> None:
    raport, pary = wykonaj_probe(_dane())
    if raport["status"] == "pominieta":
        pytest.skip(raport["powod"])
    assert pary and raport["falszywe_silne_srednie"] == 0
