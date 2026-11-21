"""Filtr podejrzliwości, reguły zapytań i model reklamy na sztucznych reklamach."""
from __future__ import annotations

from collections import Counter
from datetime import date

from conftest import rekord
from fakecollector.adlib.filters import KontekstFiltra, ocen
from fakecollector.adlib.model import ReklamaMeta, bez_tokenu, rodzaj_zdjecia
from fakecollector.adlib.reguly import fraza_produktu, zapytania_na_skan

SCAM = ("Rafał Brzoska ujawnia sposób na pasywny dochód. Zarabiaj 5000 zł miesięcznie bez ryzyka, "
        "inwestycja na platformie, tylko dziś. Lekarze i eksperci nie chcą, żebyś to wiedział.")
ZWYKLA = "Prowadzisz restaurację? System zamówień online na Twojej stronie, bez prowizji od zamówień."
DZIS = date(2026, 11, 21)


def reklama(**kw) -> ReklamaMeta:
    return ReklamaMeta.z_api(rekord(**kw))


def test_model_z_api_i_domena():
    r = reklama(id_reklamy="9", tresc="Treść", tytul="Tytuł", podpis="WWW.Przyklad-Sklep.PL/oferta")
    assert r.domena == "przyklad-sklep.pl"
    assert r.tekst == "Treść\nTytuł"
    assert r.wiek == ["40", "65"] and r.lokalizacje == ["Poland"]
    assert r.adres_biblioteki.endswith("?id=9")
    assert reklama(id_reklamy="8", podpis="Dowiedz się więcej").domena is None


def test_bez_tokenu():
    assert bez_tokenu("https://x/?id=1&access_token=EAAB12_x-Y&b=2") == "https://x/?id=1&access_token=<usunięty>&b=2"


def test_scam_ma_wysoki_wynik_z_powodami(cfg):
    o = ocen(reklama(id_reklamy="1", tresc=SCAM), KontekstFiltra(strony_znane={"999"}, dzis=DZIS), cfg["filtr"])
    sygnaly = {p.sygnal for p in o.powody}
    assert {"osoba", "osoba_i_narracja", "presja_zysk", "presja_inne", "narracja", "nowa_strona"} <= sygnaly
    assert o.wynik >= cfg["filtr"]["prog"]
    assert any("Rafał Brzoska" in p.opis for p in o.powody)


def test_nowa_strona_dopiero_przy_znanej_historii(cfg):
    o = ocen(reklama(id_reklamy="1", tresc=SCAM), KontekstFiltra(dzis=DZIS), cfg["filtr"])
    assert "nowa_strona" not in {p.sygnal for p in o.powody}


def test_strona_udajaca_media(cfg):
    k = KontekstFiltra(strony_znane={"100"}, dzis=DZIS)
    o = ocen(reklama(id_reklamy="13", page_name="Informacje 24/7", tresc=SCAM), k, cfg["filtr"])
    assert "strona_jak_media" in {p.sygnal for p in o.powody}
    o2 = ocen(reklama(id_reklamy="14", page_name="Informacje 24/7", tresc=ZWYKLA), k, cfg["filtr"])
    assert "strona_jak_media" not in {p.sygnal for p in o2.powody}


def test_zwykla_reklama_ponizej_progu(cfg):
    k = KontekstFiltra(strony_znane={"100"}, dzis=DZIS)
    assert ocen(reklama(id_reklamy="2", tresc=ZWYKLA), k, cfg["filtr"]).wynik < cfg["filtr"]["prog"]


def test_strona_z_tropu_zawsze_idzie_dalej(cfg):
    o = ocen(reklama(id_reklamy="3", page_id="777", tresc=ZWYKLA), KontekstFiltra(strony_tropow={"777"}),
             cfg["filtr"])
    assert o.z_tropu and o.wynik == 1.0


def test_lista_cert_obejmuje_poddomeny(cfg):
    k = KontekstFiltra(domeny_cert={"zly-sklep.pl"}, strony_znane={"100"}, dzis=DZIS)
    o = ocen(reklama(id_reklamy="4", tresc=ZWYKLA, podpis="promo.zly-sklep.pl"), k, cfg["filtr"])
    assert any(p.sygnal == "lista_cert" for p in o.powody)


def test_platnik_i_spalanie_strony(cfg):
    k = KontekstFiltra(reklam_strony=Counter({"100": cfg["filtr"]["reklam_strony"]}), strony_znane={"100"},
                       dzis=DZIS)
    o = ocen(reklama(id_reklamy="5", tresc=ZWYKLA, platnik="组织"), k, cfg["filtr"])
    assert {"platnik", "spalanie_strony"} <= {p.sygnal for p in o.powody}
    o2 = ocen(reklama(id_reklamy="6", tresc=ZWYKLA, platnik="Zażółć Gęślą Jaźń"), KontekstFiltra(
        strony_znane={"100"}, dzis=DZIS), cfg["filtr"])
    assert "platnik" not in {p.sygnal for p in o2.powody}


def test_kolejnosc_zapytan_i_limit_stron(cfg):
    strony = [str(i) for i in range(13)]
    z = zapytania_na_skan(cfg["reguly"], strony, ["Produkt X", cfg["reguly"]["frazy"][0]])
    assert [q.zrodlo for q in z[:2]] == ["trop", "trop"] and len(z[0].strony) == 10 and len(z[1].strony) == 3
    frazy = [q.fraza for q in z if q.rodzaj == "fraza"]
    assert frazy.count(cfg["reguly"]["frazy"][0]) == 1 and "Produkt X" in frazy
    reczne = zapytania_na_skan(cfg["reguly"], strony, [], fraza="  test  ")
    assert len(reczne) == 1 and reczne[0].fraza == "test" and reczne[0].zrodlo == "reczne"


def test_fraza_produktu(cfg):
    assert fraza_produktu(reklama(id_reklamy="7", tytul="Vitaflex Max 🔥!"), cfg["reguly"]) == "Vitaflex Max"
    assert fraza_produktu(reklama(id_reklamy="8", tytul="Dowiedz się więcej"), cfg["reguly"]) is None
    assert fraza_produktu(reklama(id_reklamy="9", tytul="To jest bardzo długi tytuł reklamy"), cfg["reguly"]) is None


def test_rozpoznanie_zdjecia_przez_mete(cfg):
    r = reklama(id_reklamy="10", tresc="This content was removed because it didn't follow our Advertising Standards.")
    assert rodzaj_zdjecia(r, cfg["zdjecie"]) == "standardy"
    r2 = reklama(id_reklamy="11", tresc="This ad was run by an account or Page we later disabled for not following")
    assert rodzaj_zdjecia(r2, cfg["zdjecie"]) == "konto_wylaczone"
    assert rodzaj_zdjecia(reklama(id_reklamy="12", tresc=ZWYKLA), cfg["zdjecie"]) is None
