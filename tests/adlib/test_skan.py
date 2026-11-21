"""Pakiet dla /analyze i pełny skan na bazie z atrapami API, przeglądarki i potoku."""
from __future__ import annotations

import json
from contextlib import nullcontext

import httpx
import pytest

from conftest import rekord
from fakecollector.adlib.model import ReklamaMeta, Zapytanie
from fakecollector.adlib.przechwyt import pakiet, podsumuj, wyslij
from fakecollector.adlib.skan import Skaner
from test_filtr_i_reguly import SCAM, ZWYKLA


def test_pakiet_rozpoznawany_przez_osint():
    load_json = pytest.importorskip("osint.capture", reason="brak modułu OSINT (modules/) w PYTHONPATH").load_json

    surowy = rekord("55", page_id="900", page_name="Ekspert Finansów", tresc=SCAM, tytul="Vitaflex Max",
                    podpis="zly-sklep.pl")
    tekst, pliki = pakiet(ReklamaMeta.z_api(surowy), surowy, b"PNG")
    nazwy = [n for n, _ in pliki]
    assert nazwy[0] == "reklama_55.png" and "https://zly-sklep.pl" in tekst and "Ekspert Finansów" in tekst
    cap = load_json(dict(pliki)["reklama_55.info.json"], "reklama_55.info.json")
    assert cap.ad_library[0]["ad_archive_id"] == "55" and cap.ad_library[0]["page_id"] == "900"
    assert all(b"SEKRET123" not in d for _, d in pliki)


def test_wyslij_bez_pobierania_strony():
    zapytania: list[httpx.Request] = []

    def obsluz(req: httpx.Request) -> httpx.Response:
        zapytania.append(req)
        return httpx.Response(200, json={"incident": {"id": "abc"}})

    wynik = wyslij("tekst", [("a.png", b"x")], "http://api/analyze", 5, transport=httpx.MockTransport(obsluz))
    tresc = zapytania[0].content.decode("utf-8", "replace")
    assert wynik["incident"]["id"] == "abc"
    assert 'name="fetch"\r\n\r\nfalse' in tresc and 'name="av"\r\n\r\nfalse' in tresc


def test_podsumowanie_analizy():
    odp = {"incident": {"id": "z1"}, "image_features": {"persons_visual": ["Rafał Brzoska"], "deepfake_score": 0.1},
           "text_features": {"narrative": {"label": "fałszywy artykuł o inwestycji"}, "persuasion": {"profit": 2,
                                                                                                    "urgency": 0}},
           "osint": {"pivots": [{}, {}]}, "osint_error": None}
    s = podsumuj(odp)
    assert s["id_zgloszenia"] == "z1" and s["osoby_na_obrazie"] == ["Rafał Brzoska"]
    assert s["presja"] == {"profit": 2} and s["slady_osint"] == 2 and s["bledy"] == []


class AtrapaKlienta:
    tryb = "odtwarzanie"
    dostepny = True

    def __init__(self, odpowiedzi: dict[str, list[dict]]):
        self.odpowiedzi = odpowiedzi
        self.wywolan = 0
        self.budzet_wyczerpany = False
        self.zapytania: list[Zapytanie] = []

    def szukaj(self, z: Zapytanie, data_od=None, aktywne=True, stron=None):
        self.wywolan += 1
        self.zapytania.append(z)
        return self.odpowiedzi.get(z.opis, [])


class AtrapaPrzechwytu:
    def zrzut(self, reklama):
        return b"PNG"


def skaner(cfg, magazyn, klient, tmp_path, analizy):
    def analizator(tekst, pliki):
        analizy.append((tekst, [n for n, _ in pliki]))
        return {"incident": {"id": f"zgl{len(analizy)}"}, "text_features": {"narrative": {"label": "x"}}}

    cfg = dict(cfg, reguly=dict(cfg["reguly"], frazy=["pasywny dochód"]))
    return Skaner(cfg, magazyn, klient, analizator, lambda: nullcontext(AtrapaPrzechwytu()), tmp_path)


@pytest.mark.baza
def test_pelny_skan_tropy_i_deduplikacja(cfg, magazyn, tmp_path):
    scam = rekord("A", page_id="P1", page_name="Ekspert", tresc=SCAM, platnik="组织", tytul="Vitaflex Max")
    zwykla = rekord("B", page_id="P2", tresc=ZWYKLA)
    klient = AtrapaKlienta({"fraza:pasywny dochód": [scam, zwykla]})
    analizy: list = []
    s = skaner(cfg, magazyn, klient, tmp_path, analizy)

    w1 = s.skanuj("test")
    assert w1["status"] == "zakonczony" and w1["nowych"] == 2 and w1["przeanalizowanych"] == 1
    assert len(analizy) == 1 and "reklama_A.png" in analizy[0][1]
    statusy = {r["id"]: r for r in magazyn.reklamy()}
    assert statusy["A"]["status"] == "przeanalizowana" and statusy["A"]["id_zgloszenia"] == "zgl1"
    assert statusy["B"]["status"] == "pominieta" and statusy["B"]["powody"] is not None
    assert magazyn.strony_tropow() == ["P1"]
    assert "Vitaflex Max" in magazyn.frazy_z_wynikow(7, 10)

    # Drugi skan: znana reklama A nie idzie drugi raz do analizy, nowa reklama strony P1 idzie jako trop.
    nowa = rekord("C", page_id="P1", tresc=ZWYKLA)
    klient.odpowiedzi = {"strony:P1": [nowa, scam], "fraza:pasywny dochód": [scam, zwykla]}
    w2 = s.skanuj("test")
    assert w2["nowych"] == 1 and w2["przeanalizowanych"] == 1 and len(analizy) == 2
    assert klient.zapytania[len(klient.zapytania) - 3].zrodlo == "trop"
    c = {r["id"]: r for r in magazyn.reklamy()}["C"]
    assert c["status"] == "przeanalizowana" and c["powody"][0]["sygnal"] == "trop_strony"


@pytest.mark.baza
def test_skan_zajety_gdy_trwa_inny(cfg, magazyn, tmp_path):
    s = skaner(cfg, magazyn, AtrapaKlienta({}), tmp_path, [])
    with magazyn.blokada() as wolne:
        assert wolne
        assert s.skanuj("test")["status"] == "zajety"


@pytest.mark.baza
def test_brak_tokenu_konczy_skan_bez_wyjatku(cfg, magazyn, tmp_path):
    klient = AtrapaKlienta({})
    klient.dostepny = False
    assert skaner(cfg, magazyn, klient, tmp_path, []).skanuj("test")["status"] == "brak_tokenu"


@pytest.mark.baza
def test_sprawdzenie_zdjetych_reklam(cfg, magazyn, tmp_path):
    scam = rekord("A", page_id="P1", tresc=SCAM, platnik="组织")
    klient = AtrapaKlienta({"fraza:pasywny dochód": [scam]})
    s = skaner(cfg, magazyn, klient, tmp_path, [])
    s.skanuj("test")
    zdjeta = rekord("A", page_id="P1", tresc="This content was removed because it didn't follow our "
                                              "Advertising Standards.")
    klient.odpowiedzi = {"strony:P1": [zdjeta]}
    wynik = s.sprawdz_zdjete()
    assert wynik["zdjete"] == {"standardy": 1}
    assert {r["id"]: r for r in magazyn.reklamy()}["A"]["zdjecie"] == "standardy"


@pytest.mark.baza
def test_zasiewanie_tropow_z_info_json(magazyn, tmp_path):
    from fakecollector.adlib.skan import zasiej_tropy

    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.info.json").write_text(json.dumps(
        {"extractor_key": "FacebookAds", "id": "1_3", "playlist_uploader_id": "4242"}), encoding="utf-8")
    (tmp_path / "b.info.json").write_text(json.dumps({"extractor_key": "Youtube", "uploader_id": "9"}),
                                          encoding="utf-8")
    assert zasiej_tropy(magazyn, tmp_path) == ["4242"]
    assert magazyn.strony_tropow() == ["4242"]


def test_lista_cert_z_pliku(cfg, tmp_path):
    (tmp_path / "cert_domeny.txt").write_text("zly-sklep.pl\nwww.inny.pl\n# komentarz\n", encoding="utf-8")
    s = Skaner(cfg, None, AtrapaKlienta({}), None, nullcontext, tmp_path)
    assert s.domeny_cert() == {"zly-sklep.pl", "inny.pl"}
    assert json.dumps(sorted(s.domeny_cert()))
