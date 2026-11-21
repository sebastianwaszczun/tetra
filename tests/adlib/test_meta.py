"""Klient Graph API na atrapie transportu: stronicowanie, limity, błędy, nagrania bez tokenu."""
from __future__ import annotations

import json

import httpx
import pytest

from conftest import rekord
from fakecollector.adlib.meta import BladMeta, KlientMeta
from fakecollector.adlib.model import Zapytanie

FRAZA = Zapytanie(rodzaj="fraza", fraza="pasywny dochód", zrodlo="konfiguracja")


def atrapa(odpowiedzi: list[tuple[int, dict, dict]]):
    wywolania: list[httpx.Request] = []

    def obsluz(req: httpx.Request) -> httpx.Response:
        wywolania.append(req)
        status, tresc, naglowki = odpowiedzi.pop(0)
        return httpx.Response(status, json=tresc, headers=naglowki)

    return httpx.MockTransport(obsluz), wywolania


def strona(ids: list[str], after: str | None = None) -> dict:
    d: dict = {"data": [rekord(i) for i in ids]}
    if after:
        d["paging"] = {"cursors": {"after": after},
                       "next": f"https://graph.facebook.com/v27.0/ads_archive?access_token=SEKRET123&after={after}"}
    return d


def test_stronicowanie_i_nagrania_bez_tokenu(cfg, tmp_path):
    transport, wywolania = atrapa([(200, strona(["1", "2"], after="A1"), {}), (200, strona(["3"]), {})])
    klient = KlientMeta("SEKRET123", cfg["meta"], katalog_nagran=tmp_path, transport=transport)
    wynik = klient.szukaj(FRAZA, data_od="2026-11-20")
    assert [r["id"] for r in wynik] == ["1", "2", "3"]
    assert len(wywolania) == 2 and wywolania[1].url.params["after"] == "A1"
    p = wywolania[0].url.params
    assert p["search_terms"] == "pasywny dochód" and p["ad_reached_countries"] == '["PL"]'
    assert p["ad_active_status"] == "ACTIVE" and p["ad_delivery_date_min"] == "2026-11-20"
    pliki = sorted(tmp_path.glob("*.json"))
    assert len(pliki) == 2
    for plik in pliki:
        assert "SEKRET123" not in plik.read_text(encoding="utf-8")


def test_odtwarzanie_daje_to_samo_bez_tokenu(cfg, tmp_path):
    transport, _ = atrapa([(200, strona(["1", "2"]), {})])
    KlientMeta("SEKRET123", cfg["meta"], katalog_nagran=tmp_path, transport=transport).szukaj(FRAZA)
    odtworzone = KlientMeta(None, cfg["meta"], tryb="odtwarzanie", katalog_nagran=tmp_path).szukaj(FRAZA)
    assert [r["id"] for r in odtworzone] == ["1", "2"]
    assert "SEKRET123" not in json.dumps(odtworzone)


def test_limit_stron_na_zapytanie(cfg, tmp_path):
    cfg_meta = dict(cfg["meta"], stron_na_zapytanie=1)
    transport, wywolania = atrapa([(200, strona(["1"], after="A1"), {})])
    assert len(KlientMeta("T", cfg_meta, transport=transport).szukaj(FRAZA)) == 1
    assert len(wywolania) == 1


@pytest.mark.parametrize(("kod", "podkod", "rodzaj"), [
    (190, None, "token_wygasl"), (10, 2332002, "brak_dostepu"), (100, None, "zle_zapytanie"), (1, None, "inny"),
])
def test_bledy_api(cfg, kod, podkod, rodzaj):
    blad = {"error": {"code": kod, "error_subcode": podkod, "message": "opis błędu"}}
    transport, _ = atrapa([(400, blad, {})])
    with pytest.raises(BladMeta) as exc:
        KlientMeta("T", cfg["meta"], transport=transport).szukaj(FRAZA)
    assert exc.value.rodzaj == rodzaj


def test_limit_api_czeka_i_ponawia(cfg):
    naglowki = {"x-business-use-case-usage": json.dumps(
        {"1": [{"type": "ads_archive", "call_count": 100, "total_cputime": 5, "total_time": 5,
                "estimated_time_to_regain_access": 2}]})}
    transport, wywolania = atrapa([(400, {"error": {"code": 80000, "message": "limit"}}, naglowki),
                                   (200, strona(["1"]), {})])
    czekania: list[float] = []
    wynik = KlientMeta("T", cfg["meta"], transport=transport, czekaj=czekania.append).szukaj(FRAZA)
    assert [r["id"] for r in wynik] == ["1"] and len(wywolania) == 2
    assert czekania == [120.0]


def test_wysokie_zuzycie_robi_pauze(cfg):
    naglowki = {"x-business-use-case-usage": json.dumps({"1": [{"call_count": 95}]})}
    transport, _ = atrapa([(200, strona(["1"]), naglowki)])
    czekania: list[float] = []
    KlientMeta("T", cfg["meta"], transport=transport, czekaj=czekania.append).szukaj(FRAZA)
    assert czekania == [cfg["meta"]["pauza_przy_zuzyciu_s"]]


def test_budzet_wywolan(cfg):
    cfg_meta = dict(cfg["meta"], budzet_wywolan=1)
    transport, _ = atrapa([(200, strona(["1"], after="A1"), {})])
    klient = KlientMeta("T", cfg_meta, transport=transport)
    assert len(klient.szukaj(FRAZA)) == 1 and klient.budzet_wyczerpany


def test_brak_tokenu(cfg):
    klient = KlientMeta(None, cfg["meta"])
    assert not klient.dostepny
    with pytest.raises(BladMeta) as exc:
        klient.szukaj(FRAZA)
    assert exc.value.rodzaj == "brak_tokenu"


def test_strony_tropow_w_zapytaniu(cfg):
    transport, wywolania = atrapa([(200, strona([]), {})])
    z = Zapytanie(rodzaj="strony", strony=[str(i) for i in range(12)], zrodlo="trop")
    KlientMeta("T", cfg["meta"], transport=transport).szukaj(z, aktywne=False)
    p = wywolania[0].url.params
    assert json.loads(p["search_page_ids"]) == [str(i) for i in range(10)]
    assert p["ad_active_status"] == "ALL" and "search_terms" not in p
