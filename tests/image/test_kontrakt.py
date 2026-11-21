"""Kontrakt modułu na sztucznym obrazie i syntetycznych wektorach."""
from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError

from fakecollector.features.image import (
    analizuj_obraz, analizuj_obrazy, klasyfikuj, klucze_wyszukiwania, porownaj, zbierz,
)
from fakecollector.features.image.konfiguracja import wczytaj_konfiguracje
from fakecollector.features.image.model import (
    CechyObrazu, CechyZbiorcze, Hashe, KluczeWyszukiwania, Kontekst, Logo, Twarz, Wektor,
)


def test_wyniki_analizy_i_ubogi_obraz() -> None:
    obraz = Image.new("RGB", (31, 17), "blue")
    ctx = Kontekst(zrodlo="test", rola="kreacja")
    with BytesIO() as plik:
        obraz.save(plik, "PNG")
        para = [(obraz, ctx), (plik.getvalue(), ctx)]
    wyniki = analizuj_obrazy(para)
    assert isinstance(wyniki, list) and wyniki.wywolania_ciezkich_modeli == 0
    assert len(wyniki) == len(para)
    assert wyniki[0] is not wyniki[1]
    for cechy, (wejscie, _) in zip(wyniki, para, strict=True):
        powtorzone = analizuj_obraz(wejscie, ctx)
        assert cechy.braki
        assert cechy.model_dump(exclude={"czasy_ms"}) == powtorzone.model_dump(exclude={"czasy_ms"})
        assert CechyObrazu.model_validate_json(cechy.model_dump_json()) == cechy
        assert cechy.wektor is None and not cechy.hashe.phash
    klasy = klasyfikuj(obraz, ["etykieta", "braki"])
    assert isinstance(klasy, dict) and klasy == {} and klasy.braki
    puste = analizuj_obrazy([])
    assert puste == [] and puste.braki
    porownanie = porownaj(*wyniki)
    assert porownanie.braki and not porownanie.dowody and porownanie.metryki.phash_min is None
    klucze = klucze_wyszukiwania(wyniki[0])
    assert klucze.braki and not klucze.phash and klucze.wektor is None
    suma = zbierz(wyniki)
    assert suma.braki and not suma.wektory and len(suma.zrodla) == 2


def test_wektory_tylko_w_pelnym_rekordzie() -> None:
    wektor = Wektor(model="test", wersja="1", wymiar=2, wartosci=[0.0, 1.0])
    twarz = Twarz(prostokat=(0, 0, 8, 9), pewnosc_wykrycia=1, punkty=((1, 1),) * 5,
                  wielkosc_px=8, wektor=wektor, jakosc=0.5)
    for rekord in (CechyObrazu(wektor=wektor, twarze=[twarz]),
                   CechyZbiorcze(wektor_sredni=wektor, wektory=[wektor], twarze=[twarz])):
        assert rekord.model_dump()["twarze"][0]["wektor"]["wartosci"] == [0, 1]
        widok = rekord.dla_frontu()
        assert not {"wektor", "wektory", "wektor_sredni"} & widok.keys()
        assert "wektor" not in widok["twarze"][0]


def test_ograniczenia_schematu() -> None:
    with pytest.raises(ValidationError):
        Wektor(model="test", wersja="1", wymiar=2, wartosci=[1])
    with pytest.raises(ValidationError):
        Hashe(phash=["0" * 15] * 3)
    with pytest.raises(ValidationError):
        KluczeWyszukiwania(dhash=[2**64])
    with pytest.raises(ValidationError):
        Logo(marka="test", rog="dol", prostokat=(0, 0, -1, 4), pewnosc=2, zrodlo="opis")
    with pytest.raises(ValidationError):
        CechyObrazu(elementy_szablonu={"przyklad": 1.1})


def test_konfiguracja() -> None:
    config = wczytaj_konfiguracje()
    assert all(0 <= config["hashe"][p] <= 64 for p in ("T_p", "T_d"))
    slownik = config["szablon"]
    assert len(set(slownik["elementy"])) == 13
    assert set(slownik["pospolite"]) < set(slownik["elementy"])
    assert slownik["marki"]
