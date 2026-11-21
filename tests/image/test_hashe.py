"""Testy przygotowania, miar treści i kalibracji na sztucznych obrazach."""
import json
import struct
import zlib
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from random import Random

import pytest
from PIL import Image, ImageDraw, ImageOps

from fakecollector.features.image import Obraz, analizuj_podstawy
from fakecollector.features.image.ewaluacja.ewaluacja_hashe import ewaluuj_hashe
from fakecollector.features.image.hashe import Odleglosc, duplikat, odleglosci, pasujaca_para
from fakecollector.features.image.ewaluacja.kalibracja_hashy import dobierz_progi, siatka_trafien
from fakecollector.features.image.konfiguracja import wczytaj_konfiguracje
from fakecollector.features.image.model import CechyObrazu
from fakecollector.features.image.przygotowanie import przygotuj_obraz
from fakecollector.features.image.warianty import generuj_warianty
from fakecollector.features.image.wzorzec import wczytaj_pary

def analizuj_obraz(obraz: Obraz) -> CechyObrazu:
    return analizuj_podstawy(obraz)[1]


def _obraz() -> Image.Image:
    obraz = Image.new("RGB", (384, 320), "#3f496b")
    rys = ImageDraw.Draw(obraz)
    rys.rectangle((0, 0, 155, 180), fill="orange")
    rys.ellipse((190, 24, 340, 280), fill="teal")
    rys.polygon(((4, 291), (153, 155), (299, 306)), fill="red")
    rys.line((0, obraz.height - 1, obraz.width - 1, 0), fill="white", width=12)
    return obraz


def _plik(obraz: Image.Image, format_pliku: str, **opcje: object) -> bytes:
    with BytesIO() as strumien:
        obraz.save(strumien, format_pliku, **opcje)
        return strumien.getvalue()


@pytest.mark.parametrize("format_pliku", ["PNG", "JPEG", "WEBP", "GIF", "BMP", "HEIF", "AVIF"])
def test_formaty(format_pliku: str) -> None:
    plik = _plik(_obraz(), format_pliku)
    wynik = analizuj_obraz(plik)
    assert wynik.format == format_pliku
    assert wynik.sha256 == sha256(plik).hexdigest()
    for hashe_wariantu in (wynik.hashe.phash, wynik.hashe.dhash):
        assert len(hashe_wariantu) == 3 and all(len(h) == 16 for h in hashe_wariantu)


def test_svg_uszkodzony_raster_i_limity() -> None:
    # Nagłówek PNG deklaruje duży raster, a zapis nie zawiera pikseli.
    tresc = b"IHDR" + struct.pack(">IIBBBBB", 10000, 6000, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + tresc + struct.pack(">I", zlib.crc32(tresc))
    for wejscie, powod in (
        (png + b"\x00\x00\x00\x00IDAT", "limit pikseli"),
        (b"x" * (wczytaj_konfiguracje()["wczytywanie"]["maks_bajtow"] + 1), "limit bajtów"),
        (b'<?xml version="1.0"?>\n<svg width="100" height="100"></svg>', "SVG pominięty"),
        (b"zepsuty", "Nierozpoznany"),
    ):
        wynik = analizuj_obraz(wejscie)
        assert powod in wynik.braki[0].powod
        assert bool(wynik.sha256) == (powod == "SVG pominięty") and not wynik.hashe.phash


def test_exif_i_pierwsza_klatka() -> None:
    obraz = _obraz()
    exif = Image.Exif()
    exif[274] = 6
    cechy = analizuj_obraz(_plik(obraz, "JPEG", exif=exif))
    exif[274] = 1
    zwykle = analizuj_obraz(_plik(obraz, "JPEG", exif=exif))
    assert cechy.rozmiar == zwykle.rozmiar[::-1]
    assert cechy.przygotowanie.rozmiar_oryginalny == obraz.size
    gif = _plik(obraz, "GIF", save_all=True, append_images=[Image.new("RGB", obraz.size, "blue")])
    assert not analizuj_obraz(gif).ubogi_w_tresc
    with Image.open(BytesIO(gif)) as wejscie:
        wejscie.seek(1)
        assert not analizuj_obraz(wejscie).ubogi_w_tresc
        assert wejscie.tell() == 1


def test_przezroczystosc_i_ramki() -> None:
    przezroczysty = Image.new("RGBA", (128, 128), (255, 0, 0, 0))
    przezroczysty.paste((0, 0, 0, 128), (50, 50, 70, 70))
    raster, _ = przygotuj_obraz(_plik(przezroczysty, "PNG"))
    assert raster is not None and raster.getpixel((0, 0)) == (255, 255, 255)
    assert raster.getpixel((60, 60)) == (127, 127, 127)
    oprawa = ImageOps.expand(_obraz(), border=(20, 30, 25, 40), fill="white")
    wynik = analizuj_obraz(oprawa)
    assert wynik.rozmiar == _obraz().size
    assert wynik.przygotowanie.odciete_ramki == (20, 30, 25, 40)
    szeroka = ImageOps.expand(_obraz(), border=(180, 0, 0, 0), fill="white")
    assert analizuj_obraz(szeroka).przygotowanie.odciete_ramki[0] <= szeroka.width * 0.25


def test_filtr_tresci() -> None:
    bialy = Image.new("RGB", (400, 320), "white")
    assert analizuj_obraz(bialy).ubogi_w_tresc
    ImageDraw.Draw(bialy).polygon(((195, 143), (208, 155), (195, 167), (198, 155)), fill="gray")
    strzalka = analizuj_obraz(bialy)
    assert strzalka.ubogi_w_tresc and not strzalka.hashe.phash
    assert duplikat(strzalka, analizuj_obraz(bialy), 0, 0)
    los = Random(9)
    szum = Image.frombytes("L", (128, 128), los.randbytes(128 ** 2))
    assert not analizuj_obraz(szum).ubogi_w_tresc
    lagodny_szum = szum.point(lambda x: 118 + x // 16)
    lagodny = analizuj_obraz(lagodny_szum)
    assert lagodny.miara_tresci.dominujacy_kolor < 0.85 and lagodny.ubogi_w_tresc
    assert not analizuj_obraz(_obraz()).ubogi_w_tresc
    assert analizuj_obraz(_obraz().resize((63, 64))).za_maly


def test_hashe_i_obiecane_transformacje() -> None:
    obraz = _obraz()
    baza = analizuj_obraz(obraz)
    assert baza.hashe == analizuj_obraz(obraz).hashe
    odwrocone = analizuj_obraz(ImageOps.mirror(obraz))
    assert pasujaca_para(odleglosci(baza.hashe, odwrocone.hashe), 0, 0).wariant == "odbicie"
    assert pasujaca_para(odleglosci(odwrocone.hashe, baza.hashe), 0, 0).wariant == "odbicie"
    assert not duplikat(baza, analizuj_obraz(ImageOps.invert(obraz)), 8, 10)
    for wariant in generuj_warianty(obraz):
        if wariant.nazwa in {"skala_25", "jpeg_q30", "ramka", "jasnosc_80", "odbicie"}:
            assert duplikat(baza, analizuj_obraz(wariant.obraz), 8, 10), wariant.nazwa


def test_wspolna_para_i_zapas() -> None:
    konflikt = (Odleglosc("caly", 0, 11), Odleglosc("srodek", 9, 0))
    assert pasujaca_para(konflikt, 8, 10) is None
    pozytyw = (Odleglosc("caly", 4, 5), Odleglosc("srodek", 6, 2))
    negatyw = (Odleglosc("caly", 7, 3),)
    assert siatka_trafien([pozytyw])[6, 5] == 1
    assert dobierz_progi([pozytyw], [negatyw], 1) == (4, 5)
    assert dobierz_progi([pozytyw], [(Odleglosc("odbicie", 0, 1),)], 1) is None


def test_ewaluacja_i_maska(tmp_path: Path) -> None:
    katalog = tmp_path / "wzorzec"
    katalog.mkdir()
    obrazy = [_obraz(), ImageOps.invert(_obraz())]
    kreacje = []
    for i, obraz in enumerate(obrazy):
        ImageDraw.Draw(obraz).ellipse((100, 110, 155, 165), fill="white")
        nazwa = f"{i}.png"
        obraz.save(tmp_path / nazwa)
        kreacje.append({"id": str(i), "plik": nazwa, "zgloszenie": "test", "status": "zatwierdzone",
                        "prostokat_kreacji": [0, 0, *obraz.size],
                        "oczekiwane": {"prostokat_odtwarzania": [100, 110, 56, 56]}})
    (katalog / "kreacje.json").write_text(json.dumps({"wersja": 1, "kreacje": kreacje, "pary": []}))
    raport = ewaluuj_hashe(tmp_path)
    assert raport["nakladki"] == {"pary": 1, "falszywe_bez_maski": 0, "falszywe_z_maska": 0}
    assert raport["liczba_wariantow"] == 22 and raport["skalibrowano"]


def test_zapis_wzorcow_zachowuje_reczne_adnotacje(tmp_path: Path) -> None:
    a, b = (tmp_path / f"nask/DANE/zgloszenia/{i}/strona_files" for i in (1, 2))
    for katalog in (a, b):
        katalog.mkdir(parents=True)
        (katalog / "ikona.svg").write_text("<svg></svg>")
        _obraz().save(katalog / "obraz.png")
    (b / "zmieniony.jpg").write_bytes(_plik(_obraz(), "JPEG", quality=95))
    wzorzec = tmp_path / "wzorzec"
    wzorzec.mkdir()
    reczna = {"a": (a / "obraz.png").relative_to(tmp_path).as_posix(),
              "b": (b / "zmieniony.jpg").relative_to(tmp_path).as_posix(),
              "relacja": "rozne", "zrodlo": "reczne", "uwagi": "adnotacja testowa"}
    (wzorzec / "pary_obrazow.json").write_text(json.dumps([reczna]))
    ewaluuj_hashe(tmp_path, zapisz_pary=True)
    pary = wczytaj_pary(tmp_path)
    assert sum(p.relacja == "identyczne" for p in pary) == 2
    assert any(p.model_dump() == reczna for p in pary)


@pytest.mark.dane_nask
def test_raport_nask() -> None:
    dane = Path(__file__).resolve().parents[2] / "data"
    if not (dane / "nask/DANE").is_dir() or not (dane / "wzorzec/kreacje.json").is_file():
        pytest.skip("Potrzebne data/nask/DANE i ręczne kreacje.json")
    raport = ewaluuj_hashe(dane)
    assert raport["identyczne"]["1↔2"] == 14
    assert raport["identyczne"]["48↔61"] == 7
    assert raport["falszywe"] == raport["falszywe_z_zapasem"] == 0
    zbior = json.loads((dane / "wzorzec/kreacje.json").read_text(encoding="utf-8"))
    puste = [k for k in zbior["kreacje"] if k["oczekiwane"].get("ubogi_w_tresc") and k["status"] == "zatwierdzone"]
    if not puste:
        pytest.skip("Potrzebna zatwierdzona adnotacja ubogiego kadru")
    for k in puste:
        x, y, w, h = k["prostokat_kreacji"]
        with Image.open(dane / k["plik"]) as obraz:
            assert analizuj_obraz(obraz.crop((x, y, x + w, y + h))).ubogi_w_tresc
    assert wczytaj_pary(dane)
