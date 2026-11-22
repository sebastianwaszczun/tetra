"""Testy twarzy na sztucznych rastrach i wektorach."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Lock

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from fakecollector.features.image import analizuj_obraz, magface, na_rekord, twarze as mod_twarze
from fakecollector.features.image.konfiguracja import wczytaj_konfiguracje
from fakecollector.features.image.model import CechyObrazu, Hashe, Kontekst, Twarz, Wektor
from fakecollector.features.image.przygotowanie import przygotuj_obraz
from fakecollector.features.image.referencje_twarzy import dopasuj_osobe, kalibruj, proby_bazy, wczytaj_baze, zbuduj_baze
from fakecollector.features.image.twarze import (
    KATALOG_MODELI, MODEL_TWARZY, Detekcja, analizuj_twarze, jakosc_twarzy, porownaj_twarze, wykryj_twarze,
    ostrosc_twarzy, wyrownaj_twarz,
)
from fakecollector.features.image.wzorzec import otworz_obraz, plik_danych, wczytaj_kreacje


def _wektor(wartosci: list[float]) -> Wektor:
    return Wektor(model=MODEL_TWARZY["yunet_sface"], wersja="test", wymiar=len(wartosci), wartosci=wartosci)


def _twarz(wektor: Wektor | None) -> Twarz:
    return Twarz(prostokat=(0, 0, 30, 30), pewnosc_wykrycia=1, punkty=((1, 1),) * 5,
                 wielkosc_px=30, jakosc=0.8, wektor=wektor)


class SilnikTestowy:
    wersja = "test"

    def __init__(self, wiele: bool = False) -> None:
        self.blokada = Lock()
        self.skale = []
        self.wiele = wiele

    def wykryj(self, raster: np.ndarray) -> list[Detekcja]:
        h, w = raster.shape[:2]
        self.skale.append(w)
        if self.wiele:
            return [self._detekcja(20 + i % 6 * 90, 20 + i // 6 * 90, 33 + i, 0.95)
                    for i in range(24)]
        if len(self.skale) == 1:
            return [self._detekcja(20, 30, 15, 0.8)]
        return [self._detekcja(40, 60, 52, 0.95)]

    @staticmethod
    def _detekcja(x: int, y: int, bok: int, pewnosc: float) -> Detekcja:
        punkty = np.array(((0.3, 0.3), (0.7, 0.3), (0.5, 0.5), (0.35, 0.7), (0.65, 0.7)))
        return Detekcja((x, y, bok, bok), punkty * bok + (x, y), pewnosc)

    def koduj(self, raster: np.ndarray, det: Detekcja, wyrownana: np.ndarray) -> np.ndarray:
        return np.array([8.0, 0.0], dtype=np.float32)


def test_drugi_przebieg_i_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    konfiguracja = wczytaj_konfiguracje()
    konfiguracja["twarze"]["jakosc"] = "zastepcza"
    monkeypatch.setattr(mod_twarze, "wczytaj_konfiguracje", lambda: konfiguracja)
    raster, cechy = przygotuj_obraz(Image.new("RGB", (640, 640), "white"))
    silnik = SilnikTestowy()
    twarz, = wykryj_twarze(raster, cechy, "yunet_sface", silnik)
    assert silnik.skale == [640, 1280]
    assert twarz.prostokat == (20, 30, 26, 26)
    silnik = SilnikTestowy(wiele=True)
    wyniki = wykryj_twarze(raster, cechy, "yunet_sface", silnik)
    assert len(wyniki) == 20
    assert wyniki[0].wielkosc_px == 56
    assert wyniki[-1].wielkosc_px == 37


def test_jakosc_rosnie_z_ostroscia_i_rozmiarem(monkeypatch: pytest.MonkeyPatch) -> None:
    konfiguracja = wczytaj_konfiguracje()
    cfg = konfiguracja["twarze"]
    cfg["jakosc"] = "zastepcza"
    monkeypatch.setattr(mod_twarze, "wczytaj_konfiguracje", lambda: konfiguracja)
    ocena = cfg["yunet_sface"]
    mala = jakosc_twarzy(2, 24, .45, 6, ocena, cfg)
    duza = jakosc_twarzy(8, 90, .95, 180, ocena, cfg)
    assert mala < ocena.get("prog_jakosci_zastepcza", ocena["prog_jakosci"]) < duza <= 1
    obraz = Image.new("RGB", (640, 640), "white")
    rys = ImageDraw.Draw(obraz)
    for x in range(0, 60, 2):
        rys.line((x, 0, x, 639), fill="black")
    ostry, o_1 = przygotuj_obraz(obraz)
    rozmyty, o_2 = przygotuj_obraz(obraz.filter(ImageFilter.GaussianBlur(6)))
    silnik = SilnikTestowy()
    silnik.koduj = lambda *_: np.array([3.0, 0])
    dobry, = wykryj_twarze(ostry, o_1, "yunet_sface", silnik)
    silnik = SilnikTestowy()
    silnik.koduj = lambda *_: np.array([3.0, 0])
    slaby, = wykryj_twarze(rozmyty, o_2, "yunet_sface", silnik)
    assert dobry.jakosc > slaby.jakosc
    assert slaby.wektor is None


def test_ostrosc_wyrownanego_wzoru_jest_stabilna_w_roznych_rozmiarach() -> None:
    from insightface.utils.face_align import arcface_dst

    ostre, rozmyte = [], []
    for bok in (120, 400, 800):
        u = np.arange(bok) / bok
        fale = 128 + 50 * np.sin(20 * np.pi * u)[None, :] + 50 * np.sin(16 * np.pi * u)[:, None]
        raster = cv2.cvtColor(np.rint(fale).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        punkty = arcface_dst * (bok / 112)
        rozmyty = cv2.GaussianBlur(raster, (0, 0), 2 * bok / 112)
        wyrownana = wyrownaj_twarz(raster, punkty)
        assert wyrownana.shape == (112, 112, 3)
        ostre.append(ostrosc_twarzy(wyrownana))
        rozmyte.append(ostrosc_twarzy(wyrownaj_twarz(rozmyty, punkty)))

    assert max(ostre) <= min(ostre) * 1.10
    assert all(slaba < 0.5 * ostra for ostra, slaba in zip(ostre, rozmyte, strict=True))


def test_skala_magface_jest_ograniczona_do_zera_i_jedynki() -> None:
    granice = wczytaj_konfiguracje()["twarze"]["magface"]
    dol, gora = granice["norma_min"], granice["norma_max"]
    assert magface.skaluj_norme(dol - 1, granice) == 0
    assert magface.skaluj_norme((dol + gora) / 2, granice) == 0.5
    assert magface.skaluj_norme(gora + 1, granice) == 1


def test_przelacznik_magface_kontroluje_ocene_i_filtr(monkeypatch: pytest.MonkeyPatch) -> None:
    konfiguracja = wczytaj_konfiguracje()
    cfg = konfiguracja["twarze"]
    cfg["jakosc"] = "zastepcza"
    wywolania = []

    def ocen(wyrownana: np.ndarray, granice: dict[str, float]) -> float:
        wywolania.append(wyrownana.shape)
        return 0.1

    monkeypatch.setattr(mod_twarze, "wczytaj_konfiguracje", lambda: konfiguracja)
    monkeypatch.setattr(mod_twarze, "ocena_magface", ocen)
    raster, cechy = przygotuj_obraz(Image.new("RGB", (300, 200), "white"))
    zwykla, = wykryj_twarze(raster, cechy, "yunet_sface", SilnikTestowy())
    assert not wywolania and zwykla.wektor.model == MODEL_TWARZY["yunet_sface"]

    cfg.update(jakosc="magface", magface_tylko_norma=False)
    cala, = wykryj_twarze(raster, cechy, "yunet_sface", SilnikTestowy())
    assert wywolania == [(112, 112, 3)] and cala.jakosc == 0.1 and cala.wektor is None
    assert cala.osoba is None and porownaj_twarze([cala], [cala], cfg) == (None, None)

    cfg["magface_tylko_norma"] = True
    skladowa, = wykryj_twarze(raster, cechy, "yunet_sface", SilnikTestowy())
    assert cala.jakosc < skladowa.jakosc < zwykla.jakosc


def test_brak_wag_magface_wlacza_miare_zastepcza(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    konfiguracja = wczytaj_konfiguracje()
    cfg = konfiguracja["twarze"]
    cfg["jakosc"] = "magface"
    magface._wczytaj_model.cache_clear()
    monkeypatch.setattr(magface, "MANIFEST", tmp_path / "brak.json")
    monkeypatch.setattr(mod_twarze, "wczytaj_konfiguracje", lambda: konfiguracja)

    raster, cechy = przygotuj_obraz(Image.new("RGB", (300, 200), "white"))
    awaryjna, = wykryj_twarze(raster, cechy, "yunet_sface", SilnikTestowy())
    assert awaryjna.wektor is not None
    assert [b.skladnik for b in cechy.braki] == ["magface"]

    cfg["jakosc"] = "zastepcza"
    raster, cechy = przygotuj_obraz(Image.new("RGB", (300, 200), "white"))
    zwykla, = wykryj_twarze(raster, cechy, "yunet_sface", SilnikTestowy())
    assert awaryjna == zwykla


def test_dopasowanie_i_walidacja_wyjmij_jedno() -> None:
    baza = {"A": [_wektor([1, 0, 0]), _wektor([1, .05, 0])],
            "B": [_wektor([0, 1, 0]), _wektor([0, 1, .05])],
            "C": [_wektor([0, 0, 1]), _wektor([.05, 0, 1])]}
    raport = kalibruj(baza)
    assert raport["trafne"] == 6 and raport["falszywe_dopasowania"] == 0
    assert raport["proby_nieznanych"] == 6
    assert raport["T_f"] > .1
    szukana = _twarz(_wektor([1, .05, 0]))
    dopasuj_osobe(szukana, baza, 0.8, 0.2)
    assert szukana.osoba == "A" and szukana.drugi_kandydat.osoba == "B"
    dopasuj_osobe(szukana, baza, 0.8, 1.0)
    assert szukana.osoba is None
    obca = _twarz(_wektor([-.5, -.5, -.5]))
    dopasuj_osobe(obca, baza, 0.8, 0.2)
    assert obca.osoba is None
    assert kalibruj({"A": baza["A"]}) is None
    z_singletonem = {**baza, "D": [_wektor([-.5, -.5, -.5])]}
    assert sum(p.osoba is not None for p in proby_bazy(z_singletonem)) == 6
    assert sum(p.osoba is None for p in proby_bazy(z_singletonem)) == 7


def test_dowod_twarzy_i_wewnetrzny_rekord() -> None:
    zapis = _wektor([1, 0, 0])
    a, b = _twarz(zapis), _twarz(_wektor([1, .03, 0]))
    cfg = {"yunet_sface": {"wersja_kalibracji": "test", "T_f": 0.9}}
    metryka, dowod = porownaj_twarze([a], [b], cfg)
    assert metryka > 0.9 and dowod.rodzaj == "ta_sama_twarz" and dowod.miejsca_a == [a.prostokat]
    a.osoba = b.osoba = "Osoba testowa"
    assert porownaj_twarze([a], [b], cfg)[1].rodzaj == "ta_sama_osoba"
    cechy = CechyObrazu(twarze=[a, b, _twarz(None)], hashe=Hashe(phash=("1" * 16,) * 3))
    rekord = na_rekord(cechy)
    assert rekord["screen_phash"] == "1" * 16 and rekord["persons_visual"] == ["Osoba testowa"]
    assert rekord["clip_emb"] is None and rekord["deepfake_score"] is None and len(rekord["faces"]) == 3
    assert all("wektor" not in t for t in cechy.dla_frontu()["twarze"])


def test_dwa_glosy_i_brak_wag(monkeypatch: pytest.MonkeyPatch) -> None:
    from fakecollector.features.image import referencje_twarzy as ref, twarze as mod

    cfg = wczytaj_konfiguracje()["twarze"]
    cfg["jakosc"] = "zastepcza"
    przyjete = {"buffalo_l": "A", "yunet_sface": "B"}
    nieobecne = set()

    def testowy_silnik(model: str) -> SilnikTestowy:
        if model in nieobecne:
            raise FileNotFoundError("brak wag testowych")
        silnik = SilnikTestowy()
        silnik.wersja = cfg[model]["wersja_kalibracji"]
        silnik.koduj = lambda *_: np.array([cfg[model]["norma_dobrej"], 0, 0])
        return silnik

    def glos(twarz, *_):
        sigiel = next(k for k, nazwa in MODEL_TWARZY.items() if nazwa == twarz.wektor.model)
        twarz.osoba = przyjete[sigiel]

    monkeypatch.setattr(mod, "silnik_twarzy", testowy_silnik)
    monkeypatch.setattr(ref, "wczytaj_baze", lambda _: {"A": [_wektor([1, 0, 0])]})
    monkeypatch.setattr(ref, "dopasuj_osobe", glos)
    raster = Image.new("RGB", (100, 100), "white")
    cechy = CechyObrazu()
    analizuj_twarze(raster, cechy, cfg=cfg, opisy=False)
    t, = cechy.twarze
    assert t.status_osoby == "niepewne" and t.osoba is None
    assert t.wektor.model == MODEL_TWARZY["buffalo_l"] and t.wektor_drugi.model == MODEL_TWARZY["yunet_sface"]
    assert "wektor_drugi" not in cechy.dla_frontu()["twarze"][0]
    dowod = porownaj_twarze([t], [t], cfg)[1]
    assert dowod.rodzaj == "ta_sama_twarz"
    assert set(dowod.szczegoly["cosinusy"]) == set(MODEL_TWARZY)

    przyjete["yunet_sface"] = "A"
    para = CechyObrazu()
    analizuj_twarze(raster, para, cfg=cfg, opisy=False)
    assert para.twarze[0].osoba == "A" and para.twarze[0].status_osoby == "rozpoznana"

    nieobecne.add("buffalo_l")
    awaryjne = CechyObrazu()
    analizuj_twarze(raster, awaryjne, cfg=cfg, opisy=False)
    t, = awaryjne.twarze
    assert t.wektor.model == MODEL_TWARZY["yunet_sface"] and t.osoba is None
    assert any("buffalo_l" in b.powod for b in awaryjne.braki)
    assert porownaj_twarze([t], [t], cfg) == (1, None)


def test_magface_raz_na_twarz_po_wspolnym_limicie(monkeypatch: pytest.MonkeyPatch) -> None:
    from fakecollector.features.image import referencje_twarzy as ref

    cfg = wczytaj_konfiguracje()["twarze"]
    cfg.update(jakosc="magface", magface_tylko_norma=True, maks_liczba=2)
    wywolania = []
    wycinki_modeli = {s: [] for s in MODEL_TWARZY}

    def testowy_silnik(sigiel: str) -> SilnikTestowy:
        silnik = SilnikTestowy(wiele=True)
        koduj = silnik.koduj

        def zapisz(raster: np.ndarray, det: Detekcja, wyrownana: np.ndarray) -> np.ndarray:
            wycinki_modeli[sigiel].append(wyrownana)
            return koduj(raster, det, wyrownana)

        silnik.koduj = zapisz
        return silnik

    def ocen(wyrownana: np.ndarray, granice: dict[str, float]) -> float:
        wywolania.append(wyrownana)
        return 1.0

    monkeypatch.setattr(mod_twarze, "silnik_twarzy", testowy_silnik)
    monkeypatch.setattr(mod_twarze, "ocena_magface", ocen)
    monkeypatch.setattr(ref, "wczytaj_baze", lambda _: {})
    raster, cechy = przygotuj_obraz(Image.new("RGB", (640, 640), "white"))
    analizuj_twarze(raster, cechy, cfg=cfg, opisy=False)
    assert len(cechy.twarze) == len(wywolania) == cfg["maks_liczba"]
    for arcface, sface, magface_wycinek in zip(*wycinki_modeli.values(), wywolania, strict=True):
        assert arcface is sface is magface_wycinek


def test_baza_i_odrzucone_zdjecia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fakecollector.features.image.referencje_twarzy as ref

    folder = tmp_path / "nazwa_katalogu"
    folder.mkdir()
    zdjecia = []
    for i in range(3):
        plik = f"{i}.png"
        Image.new("RGB", (128, 128), "white").save(folder / plik)
        zdjecia.append({"plik": plik, "url": "https://example.org/test", "autor": "Test", "licencja": "CC0",
                        "tytul_pliku": "Opcjonalne pole"})
    (folder / "zrodla.json").write_text(json.dumps({"osoba": "Test", "zdjecia": zdjecia}), encoding="utf-8")
    a = _twarz(_wektor([1, 0, 0]))
    wykrycia = iter([[], [a, a], [a]])
    monkeypatch.setattr(ref, "wykryj_twarze", lambda *_: next(wykrycia))
    raport = zbuduj_baze(tmp_path, "yunet_sface")
    assert raport["zdjecia"] == 1 and raport["kalibracja"] is None
    assert [w["twarze"] for w in raport["braki"]] == [0, 2]
    assert wczytaj_baze("yunet_sface", tmp_path) == {"Test": [a.wektor]}
    with closing(sqlite3.connect(tmp_path / "wektory_yunet_sface.sqlite3")) as baza:
        assert baza.execute("SELECT COUNT(*) FROM wektory").fetchone() == (2,)


@pytest.mark.wolne
@pytest.mark.parametrize("sigiel", ["buffalo_l", "yunet_sface"])
def test_lokalne_wagi_na_obrazie_bez_twarzy(sigiel: str) -> None:
    if not (KATALOG_MODELI / f"{sigiel}.json").is_file():
        pytest.skip("Brak pobranych wag twarzy")
    obraz, cechy = przygotuj_obraz(Image.new("RGB", (190, 90), "white"))
    assert wykryj_twarze(obraz, cechy, sigiel) == []


@pytest.mark.wolne
@pytest.mark.dane_nask
def test_twarze_w_publicznej_analizie_i_rekordzie() -> None:
    dane = Path(__file__).resolve().parents[2] / "data"
    if not (dane / "nask/DANE").is_dir() or not (dane / "wzorzec/kreacje.json").is_file():
        pytest.skip("Brak lokalnych kreacji i adnotacji")
    konfiguracja = wczytaj_konfiguracje()
    cfg = konfiguracja["twarze"]
    sigiel = "buffalo_l"
    if not all((KATALOG_MODELI / f"{s}.json").is_file() for s in MODEL_TWARZY):
        pytest.skip("Brak lokalnych wag trybu łączonego")
    probki = [k for k in wczytaj_kreacje(dane).kreacje if k.oczekiwane.liczba_twarzy == 1]
    if not probki:
        pytest.skip("Brak przypadku z jedną oczekiwaną twarzą")
    k = probki[0]
    x, y, w, h = k.prostokat_kreacji
    wycinek = otworz_obraz(plik_danych(dane, k.plik)).crop((x, y, x + w, y + h))
    cechy = analizuj_obraz(wycinek, Kontekst(zrodlo="test twarzy", rola="kreacja"))
    twarz, = cechy.twarze
    assert twarz.wektor.wymiar == 512 and twarz.wektor_drugi.wymiar == 128
    assert twarz.wektor.model == MODEL_TWARZY[sigiel]
    assert na_rekord(cechy)["deepfake_score"] == twarz.deepfake
    front = cechy.dla_frontu()["twarze"][0]
    assert "wektor" not in front and "wektor_drugi" not in front
    if Path(konfiguracja["facetorch"]["manifest"]).is_file():
        assert twarz.deepfake is not None and 0 <= twarz.deepfake <= 1
        assert twarz.emocja is not None and front["uwaga_facetorch"] == "orientacyjnie"
    else:
        assert twarz.deepfake is None and twarz.brak_facetorch == "brak_wag"
    if "T_osoba" not in cfg[sigiel]:
        assert twarz.osoba is None
        assert na_rekord(cechy)["persons_visual"] == []
