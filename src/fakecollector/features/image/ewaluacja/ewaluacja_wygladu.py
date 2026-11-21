"""Porównanie modeli wyglądu na identycznym materiale i w tych samych trybach."""
from collections import defaultdict
from itertools import combinations
from math import ceil
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..elementy import ocen_elementy, wycinki
from .kalibracja_wygladu import Ocena, jakosc, podsumuj
from ..konfiguracja import wczytaj_konfiguracje
from ..logo import ocen_logo, wybierz_logo
from ..przygotowanie import ROZSZERZENIA, czytaj_bajty, przygotuj_obraz
from ..warianty import generuj_warianty
from ..wektor import Kadrowanie, ModelObrazu, pobierz_model
from ..wzorzec import Kreacja, ParaKreacji, otworz_obraz, plik_danych, wczytaj_kreacje

_WARIANTY = {"jpeg_q30", "przyciecie_10", "odbicie", "pasek_15"}


def _probka(dane: Path) -> list[Image.Image]:
    """Bierze do dwóch niepowtarzających się rastrów z każdego zgłoszenia."""
    korzen = dane / "nask/DANE"
    maks = wczytaj_konfiguracje()["wczytywanie"]["maks_bajtow"]
    widziane = set()
    baza = []
    for zgloszenie in sorted(path for path in korzen.glob("*/*") if path.is_dir()):
        liczba = 0
        for plik in sorted(zgloszenie.rglob("*")):
            if (not plik.is_file() or plik.suffix.lower() not in ROZSZERZENIA - {".svg"}
                    or not any(x.endswith("_files") for x in plik.relative_to(zgloszenie).parts[:-1])):
                continue
            plik = plik_danych(dane, plik.relative_to(dane).as_posix())
            try:
                obraz, cechy = przygotuj_obraz(czytaj_bajty(plik, maks))
            except (OSError, ValueError):
                continue
            if obraz is None or cechy.ubogi_w_tresc or cechy.za_maly or cechy.sha256 in widziane:
                continue
            baza.append(obraz)
            widziane.add(cechy.sha256)
            liczba += 1
            if liczba == 2:
                break
    return baza


def _plakietka() -> Image.Image:
    obraz = Image.new("RGB", (704, 352), "#334964")
    rys = ImageDraw.Draw(obraz)
    rys.rectangle((0, 0, 190, 352), fill="#667951")
    rys.ellipse((24, 41, 280, 275), fill="#cbaf79")
    rys.rectangle((467, 4, 703, 94), fill="#c61f38")
    rys.text((491, 16), "LIVE", fill="white", font_size=58)
    rys.rectangle((0, 280, 703, 351), fill="#243148")
    rys.text((24, 294), "BREAKING NEWS", fill="white", font_size=38)
    return obraz


def _zatwierdzone(dane: Path, uwzglednij_szkice: bool) -> tuple[list[tuple[Kreacja, Image.Image | None]], list[ParaKreacji]]:
    if not (dane / "wzorzec/kreacje.json").is_file():
        return [], []
    zbior = wczytaj_kreacje(dane)
    probki = []
    for kreacja in zbior.kreacje:
        if kreacja.status == "zatwierdzone" or uwzglednij_szkice:
            x, y, w, h = kreacja.prostokat_kreacji
            obraz = otworz_obraz(plik_danych(dane, kreacja.plik)).crop((x, y, x + w, y + h))
            raster, cechy = przygotuj_obraz(obraz)
            probki.append((kreacja, None if cechy.ubogi_w_tresc or cechy.za_maly else raster))
    return probki, zbior.pary


def _ranking_zatwierdzonych(wektory: dict[str, np.ndarray], pary: list[ParaKreacji],
                            zgloszenia: dict[str, str] | None = None, zapas: float = 0) -> dict[str, Any]:
    cosinusy = {a: {b: float(np.clip(wa @ wb, -1, 1)) for b, wb in wektory.items() if a != b}
                for a, wa in wektory.items()}
    wiersze = []
    relacje = [(para.a, para.b, para.relacja, True) for para in pary]
    if zgloszenia is not None:
        etykiety = {frozenset((para.a, para.b)) for para in pary}
        relacje.extend((a, b, "rozne", False) for a, b in combinations(zgloszenia, 2)
                       if zgloszenia[a] != zgloszenia[b] and frozenset((a, b)) not in etykiety)
    for a, b, relacja, adnotacja in relacje:
        wynik = cosinusy.get(a, {}).get(b)
        rangi = []
        if wynik is not None and relacja != "rozne":
            # Remis dzieli pozycję w rankingu.
            rangi = [1 + sum(w > wynik for w in cosinusy[k].values()) for k in (a, b)]
        wiersze.append({"a": a, "b": b, "relacja": relacja, "cosinus": wynik, "rangi": rangi, "adnotacja": adnotacja})
    rozne = [w for w in wiersze if w["relacja"] == "rozne"
             and (zgloszenia is None or zgloszenia.get(w["a"]) != zgloszenia.get(w["b"]))]
    obce = [w["cosinus"] for w in rozne]
    szablon = [w["cosinus"] for w in wiersze if w["relacja"] == "ten_sam_szablon"]
    max_obcych = max((w for w in obce if w is not None), default=None)
    min_szablonu = min((w for w in szablon if w is not None), default=None)
    prog = None
    if max_obcych is not None and min_szablonu is not None and None not in szablon:
        if zgloszenia is None and None not in obce and max_obcych < min_szablonu:
            prog = (max_obcych + min_szablonu) / 2
        if zgloszenia is not None:
            kandydat = ceil((max_obcych + zapas) * 100) / 100
            if max_obcych < kandydat <= min_szablonu:
                prog = kandydat
    podsumowanie = {}
    for relacja in ("ta_sama_reklama", "ten_sam_szablon"):
        wyniki = [w for w in wiersze if w["relacja"] == relacja]
        rangi = [ranga for w in wyniki for ranga in w["rangi"]]
        podsumowanie[relacja] = {"top1": rangi.count(1), "zapytania": len(rangi), "pary": len(wyniki)}
    return {"pary": wiersze, "ranking": podsumowanie, "T_w": prog,
            "maks_cosinus_rozne": max_obcych, "min_cosinus_szablon": min_szablonu,
            "najblizsza_para_rozna": next((w for w in rozne if max_obcych is not None
                                          and w["cosinus"] == max_obcych), None),
            "pary_rozne_z_wektorem": sum(w is not None for w in obce),
            "pary_rozne_bez_wektora": sum(w is None for w in obce)}


def _pomiar(model: ModelObrazu, baza: list[Image.Image], warianty: list[tuple[int, str, Image.Image]],
            zatwierdzone: list[tuple[Kreacja, Image.Image | None]], pary: list[ParaKreacji],
            dane: Path, kadrowanie: Kadrowanie) -> dict[str, Any]:
    cfg = wczytaj_konfiguracje()
    ustawienia = cfg["wyglad"]
    progi = ustawienia[model.sigiel]
    opisy = {id_: ustawienia["opisy_elementow"][id_] for id_ in cfg["szablon"]["elementy"]}
    marki = {id_: ustawienia["opisy_logo"][id_] for id_ in cfg["szablon"]["marki"]}
    progi_logo = progi.get("logo", dict.fromkeys(marki, progi.get("prog_logo", 1.0)))
    model.koduj_obrazy([baza[0]], kadrowanie)
    wektory_bazy = model.koduj_obrazy(baza, kadrowanie)
    ranking: dict[str, dict[str, int]] = defaultdict(lambda: {"top1": 0, "liczba": 0})
    for i in range(0, len(warianty), 8):
        porcja = warianty[i:i + 8]
        wektory = model.koduj_obrazy([w[2] for w in porcja], kadrowanie)
        najlepsze = (wektory @ wektory_bazy.T).argmax(axis=1)
        for (indeks, nazwa, _), najlepszy in zip(porcja, najlepsze, strict=True):
            ranking[nazwa]["top1"] += int(najlepszy == indeks)
            ranking[nazwa]["liczba"] += 1

    elementy = defaultdict(list)
    loga = defaultdict(list)
    pomiary_logo = {}
    wektory_kreacji = {}
    czas_pelnego = []
    czas_wycinkow = []
    kontrola = {}
    kontrola_logo = None
    pominiete = []
    czasy_rzeczywiste = []
    # Powtarzamy syntetyczną próbkę, żeby zmierzyć ciepły model i opisy z cache.
    pomiary = [(None, baza[-1])] * 3 + zatwierdzone
    for k, obraz in pomiary:
        if obraz is None:
            pominiete.append(k.id)
            if k.oczekiwane.elementy is not None:
                for id_ in opisy:
                    elementy[id_].append(Ocena(k.zgloszenie, None, id_ in k.oczekiwane.elementy))
            if k.oczekiwane.logo is not None:
                for marka in marki:
                    loga[marka].append(Ocena(k.zgloszenie, None, marka in k.oczekiwane.logo))
                pomiary_logo[k.id] = (k.oczekiwane.logo, None)
            continue
        start = perf_counter()
        pelny = model.koduj_obrazy([obraz], kadrowanie)
        czas_pelnego.append((perf_counter() - start) * 1000)
        regiony = wycinki(obraz, ustawienia["szerokosc_rogu"], ustawienia["wysokosc_rogu"],
                         ustawienia["wysokosc_pasa"])
        regiony_logo = wycinki(obraz, **ustawienia["wycinki_logo"])[1:]
        wektory, wektory_logo = np.split(
            model.koduj_obrazy([r.obraz for r in regiony[1:] + regiony_logo], kadrowanie), [len(regiony) - 1],
        )
        oceny = ocen_elementy(model, np.vstack((pelny, wektory)), opisy)
        kandydaci, _ = ocen_logo(model, wektory_logo, marki, progi["prog_negatywu_tvn24"], dane / "logo_wzorce")
        wykryte = {l.marka for l in wybierz_logo(kandydaci, regiony_logo, progi_logo, obraz.size, (0, 0), 1)}
        czas_wycinkow.append((perf_counter() - start) * 1000)
        if k is None:
            kontrola = {"pasek_tekst_dol": oceny["pasek_tekst_dol"], "plakietka_na_zywo": oceny["plakietka_na_zywo"],
                        "marki": sorted(wykryte)}
            kontrola_logo = (kandydaci, regiony_logo, obraz.size)
        else:
            czasy_rzeczywiste.append((czas_pelnego[-1], czas_wycinkow[-1]))
            wektory_kreacji[k.id] = pelny[0]
            if k.oczekiwane.elementy is not None:
                for id_, wynik in oceny.items():
                    elementy[id_].append(Ocena(k.zgloszenie, wynik, id_ in k.oczekiwane.elementy))
            if k.oczekiwane.logo is not None:
                pomiary_logo[k.id] = (k.oczekiwane.logo, (kandydaci, regiony_logo, obraz.size))
                for marka in marki:
                    wynik = max((l.wynik for l in kandydaci if l.marka == marka), default=None)
                    loga[marka].append(Ocena(k.zgloszenie, wynik, marka in k.oczekiwane.logo))

    pomiar_elementow = {k: podsumuj(v, progi["elementy"].get(k)) for k, v in elementy.items()}
    pomiar_logo = {k: podsumuj(v, progi_logo.get(k)) for k, v in loga.items()}
    kalibracja_logo = {k: v["prog"] for k, v in pomiar_logo.items() if v["prog"] is not None}

    def wykryte_marki(pomiar: Any, progi_marek: dict[str, float]) -> list[str]:
        if pomiar is None:
            return []
        kandydaci, regiony, rozmiar = pomiar
        return sorted(l.marka for l in wybierz_logo(kandydaci, regiony, progi_marek, rozmiar, (0, 0), 1))

    wyniki_logo = [{"id": id_, "oczekiwane": prawda, "biezace": wykryte_marki(pomiar, progi_logo),
                    "skalibrowane": wykryte_marki(pomiar, kalibracja_logo)}
                   for id_, (prawda, pomiar) in pomiary_logo.items()]
    kontrola["marki_skalibrowane"] = wykryte_marki(kontrola_logo, kalibracja_logo)
    czasy = czasy_rzeczywiste or list(zip(czas_pelnego[1:], czas_wycinkow[1:], strict=True))
    return {
        "wersja": model.wersja, "ranking_wariantow": dict(ranking),
        "ranking_zatwierdzonych": _ranking_zatwierdzonych(
            wektory_kreacji, pary, {k.id: k.zgloszenie for k, _ in zatwierdzone}, ustawienia["zapas_T_w"],
        ),
        "elementy": pomiar_elementow, "logo": pomiar_logo, "logo_kreacje": wyniki_logo,
        "logo_lacznie": {tryb: jakosc([(k in w[tryb], k in w["oczekiwane"])
                                      for w in wyniki_logo for k in marki]) for tryb in ("biezace", "skalibrowane")},
        "kontrola_syntetyczna": kontrola, "pominiete_kreacje": pominiete,
        "czas_wektora_ms": round(mean(c[0] for c in czasy), 2),
        "czas_z_wycinkami_ms": round(mean(c[1] for c in czasy), 2),
    }


def ewaluuj_wyglad(dane: Path, uwzglednij_szkice: bool = False) -> dict[str, Any]:
    """Ocenia oba modele na tej samej próbce. Braki etykiet pozostają jawne."""
    baza = _probka(dane)
    liczba_nask = len(baza)
    baza.append(_plakietka())
    warianty = [(i, w.nazwa, w.obraz) for i, obraz in enumerate(baza)
                for w in generuj_warianty(obraz) if w.nazwa in _WARIANTY]
    zatwierdzone, pary = _zatwierdzone(dane, uwzglednij_szkice)
    wyniki = {}
    braki = []
    for sigiel in ("clip", "siglip2"):
        try:
            model = pobierz_model(sigiel)
            wyniki[sigiel] = {tryb: _pomiar(model, baza, warianty, zatwierdzone, pary, dane, tryb)
                             for tryb in ("dopelnienie", "srodek")}
        except (OSError, ValueError, RuntimeError, ImportError, KeyError) as blad:
            braki.append(f"{sigiel}: {blad}")
    if not zatwierdzone:
        braki.append("Brak zatwierdzonych kreacji do oceny elementów, logo i progu T_w.")
    if not (dane / "logo_wzorce").is_dir():
        braki.append("Brak ręcznie dostarczonych wzorców logo.")
    return {"modele": wyniki, "braki": braki, "obrazy_nask": liczba_nask,
            "kreacje": len(zatwierdzone), "pary": len(pary),
            "uwzglednij_szkice": uwzglednij_szkice, "warianty": len(warianty)}
