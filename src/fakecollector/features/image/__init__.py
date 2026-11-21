"""Publiczny interfejs analizy obrazów."""
from collections.abc import Sequence
from time import perf_counter
from typing import Any, TypedDict

import numpy as np
from PIL import Image

from .elementy import ocen_elementy, wycinki
from .hashe import policz_hashe
from .kandydaci import klucze_wyszukiwania, szukaj_kandydatow
from .konfiguracja import wczytaj_konfiguracje
from .logo import ocen_logo, wybierz_logo
from .model import Brak, CechyObrazu, Kontekst, WynikPorownania
from .orb import zachowaj_raster_orb
from .partie import Przygotowany, WynikiPartii, dziedzicz, grupuj
from .porownaj import porownaj_cechy
from .przygotowanie import Obraz, przygotuj_obraz, przygotuj_z_pelnym_rastrem
from .qr import odczytaj_qr
from .twarze import analizuj_twarze
from .wektor import NAZWY_MODELI, pobierz_model
from .zbierz import zbierz

_ETAPY_WYGLADU = ("wektor", "elementy_szablonu", "logo_obraz")
_BLEDY_MODELU = (OSError, ValueError, RuntimeError, ImportError, KeyError)


class PolaRekordu(TypedDict):
    screen_phash: str | None
    clip_emb: list[float] | None
    image_hashes: dict[str, Any]
    faces: list[dict[str, Any]]
    persons_visual: list[str]
    deepfake_score: float | None


def na_rekord(cechy: CechyObrazu) -> PolaRekordu:
    """Mapuje cechy do wewnętrznego rekordu drużyny. Front korzysta z dla_frontu()."""
    wektor = cechy.wektor
    return {
        "screen_phash": cechy.hashe.phash[0] if cechy.hashe.phash else None,
        "clip_emb": wektor.wartosci if wektor is not None and wektor.model == NAZWY_MODELI["clip"] else None,
        "image_hashes": {"sha256": cechy.sha256 or None, **cechy.hashe.model_dump(mode="json")},
        "faces": [t.model_dump(mode="json") for t in cechy.twarze],
        "persons_visual": sorted({t.osoba for t in cechy.twarze if t.osoba}),
        "deepfake_score": max((t.deepfake for t in cechy.twarze if t.deepfake is not None), default=None),
    }


class WynikiEtykiet(dict[str, float]):
    """Słownik wyników etykiet z diagnostyką poza przestrzenią etykiet."""

    def __init__(self, wyniki: dict[str, float] | None = None, powod: str | None = None) -> None:
        super().__init__(wyniki or {})
        self.braki = [Brak(skladnik="klasyfikacja", powod=powod)] if powod else []


def analizuj_podstawy(obraz: Obraz) -> tuple[Image.Image | None, CechyObrazu]:
    """Przygotowuje obraz i liczy hashe bez ładowania modeli."""
    raster, cechy, _ = _podstawy_z_pelnym_rastrem(obraz)
    return raster, cechy


def _podstawy_z_pelnym_rastrem(obraz: Obraz) -> tuple[Image.Image | None, CechyObrazu, Image.Image | None]:
    raster, cechy, pelny = przygotuj_z_pelnym_rastrem(obraz)
    if raster is None:
        return None, cechy, None
    if cechy.ubogi_w_tresc or cechy.za_maly:
        powod = "Obraz poniżej minimalnego rozmiaru" if cechy.za_maly else "Obraz ubogi w treść"
        cechy.braki.append(Brak(skladnik="hashe", powod=powod))
        return raster, cechy, pelny
    start = perf_counter()
    try:
        cfg = wczytaj_konfiguracje()
        cechy.hashe = policz_hashe(raster, cfg["hashe"]["srodek"])
        zachowaj_raster_orb(cechy, raster, cfg["orb"]["bok_px"])
    except (OSError, ValueError) as blad:
        cechy.braki.append(Brak(skladnik="hashe", powod=str(blad)))
    finally:
        cechy.czasy_ms["hashe"] = (perf_counter() - start) * 1000
    return raster, cechy, pelny


def analizuj_obraz(obraz: Obraz, kontekst: Kontekst) -> CechyObrazu:
    """Zwraca podstawowe cechy, wygląd, logo i twarze."""
    wpis, _ = _przygotuj_z_qr(obraz)
    if wpis.raster is not None and not (wpis.cechy.ubogi_w_tresc or wpis.cechy.za_maly):
        _analizuj_ciezkie([(wpis.raster, wpis.cechy)], wczytaj_konfiguracje())
    return wpis.cechy


def _przygotuj_z_qr(obraz: Obraz) -> tuple[Przygotowany, float]:
    raster, cechy, pelny = _podstawy_z_pelnym_rastrem(obraz)
    qr_ms = 0.0
    if pelny is None:
        cechy.braki.append(Brak(skladnik="qr", powod="Brak przygotowanego obrazu"))
    else:
        start_qr = perf_counter()
        cechy.qr = odczytaj_qr(pelny, cechy.przygotowanie.rozmiar_oryginalny)
        qr_ms = (perf_counter() - start_qr) * 1000
        cechy.czasy_ms["qr"] = qr_ms
    if raster is None:
        cechy.braki.append(Brak(skladnik="twarze", powod="Brak przygotowanego obrazu"))
    if raster is None or cechy.ubogi_w_tresc or cechy.za_maly:
        powod = next((b.powod for b in cechy.braki if b.skladnik in {"hashe", "wczytywanie"}),
                     "Brak obrazu po przygotowaniu")
        cechy.braki.extend(Brak(skladnik=etap, powod=powod) for etap in _ETAPY_WYGLADU)
    return Przygotowany(raster, cechy), qr_ms


def _analizuj_ciezkie(partia: list[tuple[Image.Image, CechyObrazu]], konfiguracja: dict[str, Any]) -> None:
    for raster, cechy in partia:
        analizuj_twarze(raster, cechy)
    _analizuj_wyglad(partia, konfiguracja)
    _analizuj_logo(partia, konfiguracja)


def _analizuj_wyglad(partia: list[tuple[Image.Image, CechyObrazu]], konfiguracja: dict[str, Any]) -> None:
    ustawienia = konfiguracja["wyglad"]
    start = perf_counter()
    try:
        model = pobierz_model(ustawienia["model"])
        pelne = model.koduj_obrazy([raster for raster, _ in partia])
        for (_, cechy), wektor in zip(partia, pelne, strict=True):
            cechy.wektor = model.rekord(wektor)
    except _BLEDY_MODELU as blad:
        for _, cechy in partia:
            cechy.braki.extend(Brak(skladnik=etap, powod=str(blad)) for etap in ("wektor", "elementy_szablonu"))
        return
    finally:
        czas = (perf_counter() - start) * 1000 / len(partia)
        for _, cechy in partia:
            cechy.czasy_ms["wektor"] = czas

    start = perf_counter()
    regiony = [wycinki(r, ustawienia["szerokosc_rogu"], ustawienia["wysokosc_rogu"], ustawienia["wysokosc_pasa"])[1:]
               for r, _ in partia]
    try:
        wektory = model.koduj_obrazy([w.obraz for obszary in regiony for w in obszary])
    except _BLEDY_MODELU as blad:
        for _, cechy in partia:
            cechy.braki.append(Brak(skladnik="elementy_szablonu", powod=str(blad)))
        return
    finally:
        czas = (perf_counter() - start) * 1000 / len(partia)
        for _, cechy in partia:
            cechy.czasy_ms["wektory_wycinkow"] = czas

    start = perf_counter()
    try:
        opisy = {klucz: ustawienia["opisy_elementow"][klucz] for klucz in konfiguracja["szablon"]["elementy"]}
        for (_, cechy), pelny, wyciete in zip(partia, pelne, np.split(wektory, len(partia)), strict=True):
            cechy.elementy_szablonu = ocen_elementy(model, np.vstack((pelny, wyciete)), opisy)
    except _BLEDY_MODELU as blad:
        for _, cechy in partia:
            cechy.braki.append(Brak(skladnik="elementy_szablonu", powod=str(blad)))
    finally:
        czas = (perf_counter() - start) * 1000 / len(partia)
        for _, cechy in partia:
            cechy.czasy_ms["elementy_szablonu"] = czas


def _analizuj_logo(partia: list[tuple[Image.Image, CechyObrazu]], konfiguracja: dict[str, Any]) -> None:
    ustawienia = konfiguracja["wyglad"]
    start = perf_counter()
    try:
        sekcja = ustawienia["model_logo"]
        model = pobierz_model(sekcja)
        regiony_logo = [wycinki(r, **ustawienia["wycinki_logo"])[1:] for r, _ in partia]
        wektory_logo = model.koduj_obrazy([w.obraz for obszary in regiony_logo for w in obszary])
        marki = {marka: ustawienia["opisy_logo"][marka] for marka in konfiguracja["szablon"]["marki"]}
        progi = ustawienia[sekcja]
        progi_logo = progi.get("logo", dict.fromkeys(marki, progi.get("prog_logo", 1.0)))
        for (raster, cechy), regiony, wektory in zip(partia, regiony_logo, np.split(wektory_logo, len(partia)), strict=True):
            kandydaci, braki = ocen_logo(model, wektory, marki, progi["prog_negatywu_tvn24"])
            cechy._surowe_logo = (kandydaci, regiony)
            cechy.logo_obraz = wybierz_logo(kandydaci, regiony, progi_logo, cechy.przygotowanie.rozmiar_oryginalny,
                                           cechy.przygotowanie.odciete_ramki[:2], raster.info["orientacja_exif"])
            cechy.wersja_logo = model.wersja
            cechy.braki.extend(Brak(skladnik="logo_obraz", powod=powod) for powod in braki)
    except _BLEDY_MODELU as blad:
        for _, cechy in partia:
            cechy.braki.append(Brak(skladnik="logo_obraz", powod=str(blad)))
    finally:
        czas = (perf_counter() - start) * 1000 / len(partia)
        for _, cechy in partia:
            cechy.czasy_ms["logo_obraz"] = czas


def analizuj_obrazy(lista: Sequence[tuple[Obraz, Kontekst]]) -> WynikiPartii:
    """Deduplikuje wejście, grupuje klatki i koduje tylko przedstawicieli."""
    if not lista:
        return WynikiPartii([])
    cfg = wczytaj_konfiguracje()
    start = perf_counter()
    qr_ms = 0.0
    indeks: dict[bytes | int, int] = {}
    elementy: list[Przygotowany] = []
    kolejnosc = []
    for obraz, _ in lista:
        klucz = obraz if isinstance(obraz, bytes) else id(obraz)
        if klucz not in indeks:
            wpis, czas_qr = _przygotuj_z_qr(obraz)
            indeks[klucz] = len(elementy)
            elementy.append(wpis)
            qr_ms += czas_qr
        kolejnosc.append(indeks[klucz])
    czasy = {"tani": max(0.0, (perf_counter() - start) * 1000 - qr_ms), "qr": qr_ms}

    start = perf_counter()
    ustawienia = cfg["partie"]
    grupy = grupuj(elementy, cfg["hashe"], ustawienia["maks_stosunek_proporcji"], cfg["przygotowanie"]["bok_probki_px"])
    czasy["grupowanie"] = (perf_counter() - start) * 1000
    start = perf_counter()
    przedstawiciele = [elementy[g[0]] for g in grupy]
    rozmiar_partii = ustawienia["obrazy_na_partie"]
    for i in range(0, len(przedstawiciele), rozmiar_partii):
        porcja = [(w.raster, w.cechy) for w in przedstawiciele[i:i + rozmiar_partii] if w.raster is not None]
        _analizuj_ciezkie(porcja, cfg)
    czasy["ciezkie"] = (perf_counter() - start) * 1000

    start = perf_counter()
    for grupa in grupy:
        reprezentant = elementy[grupa[0]].cechy
        if len(grupa) > 1:
            reprezentant.grupa_podobnych = reprezentant.sha256 or f"grupa-{grupa[0]}"
        for i in grupa[1:]:
            cel = elementy[i].cechy
            cel.grupa_podobnych = reprezentant.grupa_podobnych
            dziedzicz(cel, reprezentant)
    wyniki = []
    widziane = set()
    for i in kolejnosc:
        c = elementy[i].cechy
        if i in widziane:
            kopia = CechyObrazu.model_validate(c.model_dump())
            kopia._raster_orb = c._raster_orb
            kopia.jak_obraz = c.jak_obraz or c.sha256 or None
            wyniki.append(kopia)
        else:
            wyniki.append(c)
            widziane.add(i)
    czasy["dziedziczenie"] = (perf_counter() - start) * 1000
    return WynikiPartii(wyniki, len(przedstawiciele), czasy)


def porownaj(a: CechyObrazu, b: CechyObrazu) -> WynikPorownania:
    """Zwraca metryki i dowody z progami wersjonowanej konfiguracji."""
    return porownaj_cechy(a, b, wczytaj_konfiguracje())


def klasyfikuj(obraz: Obraz, etykiety: Sequence[str]) -> WynikiEtykiet:
    """Ocenia etykiety jako opisy wejściowe modelu, bez trenowania."""
    if not etykiety:
        return WynikiEtykiet()
    raster, cechy = przygotuj_obraz(obraz)
    if raster is None:
        return WynikiEtykiet(powod=cechy.braki[0].powod)
    if cechy.ubogi_w_tresc or cechy.za_maly:
        return WynikiEtykiet(powod="Obraz ubogi w treść lub zbyt mały")
    unikalne = list(dict.fromkeys(etykiety))
    try:
        model = pobierz_model(wczytaj_konfiguracje()["wyglad"]["model"])
        wektor = model.koduj_obrazy([raster])
        oceny = model.ocen_opisy(wektor, [[etykieta] for etykieta in unikalne]
                                 + [["none of the listed visual categories"]])[0][:-1]
        return WynikiEtykiet(dict(zip(unikalne, map(float, oceny), strict=True)))
    except _BLEDY_MODELU as blad:
        return WynikiEtykiet(powod=str(blad))


