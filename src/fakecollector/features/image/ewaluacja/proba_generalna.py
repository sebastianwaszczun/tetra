"""Próba generalna z odłożeniem pełnej kampanii i osobnym zbiorem negatywów."""
import os
from collections.abc import Iterator
from copy import deepcopy
from itertools import combinations
from pathlib import Path
from typing import Any

import cv2
from PIL import Image

from .. import analizuj_obraz, analizuj_podstawy
from ..hashe import odleglosci
from .kalibracja_hashy import dobierz_progi
from .kalibracja_wygladu import Ocena, prog_bez_falszywych
from ..konfiguracja import wczytaj_konfiguracje
from ..logo import wybierz_logo
from ..model import Kontekst, Twarz
from ..porownaj import porownaj_cechy
from ..przygotowanie import ROZSZERZENIA
from ..raport_html import MaterialHTML, ParaHTML
from ..referencje_twarzy import decyzja_osoby, kalibruj, ranking_osob, wczytaj_baze
from ..twarze import MODEL_TWARZY, wektory_twarzy, zgodnosc_glosow
from ..warianty import warianty_porownania
from ..wektor import NAZWY_MODELI, cosinus
from ..wzorzec import Kreacja, ParaKreacji, otworz_obraz, plik_danych, wczytaj_kreacje

_ZWIAZANE = {"duplikat", "ta_sama_reklama", "ten_sam_szablon"}


def analiza(nazwa: str, obraz: Image.Image) -> MaterialHTML:
    return MaterialHTML(nazwa, obraz, analizuj_obraz(obraz, Kontekst(zrodlo=nazwa, rola="kreacja")))


def wycinek(dane: Path, k: Kreacja) -> Image.Image:
    x, y, w, h = k.prostokat_kreacji
    return otworz_obraz(plik_danych(dane, k.plik)).crop((x, y, x + w, y + h))


def _pliki(katalog: Path, rozszerzenia: set[str]) -> list[Path]:
    baza = katalog.resolve()
    return sorted(p.resolve() for p in katalog.rglob("*") if p.suffix.lower() in rozszerzenia
                  and p.is_file() and p.resolve().is_relative_to(baza))


def klatki_nask(dane: Path, cfg: dict) -> Iterator[tuple[tuple[str, ...], Image.Image]]:
    """Pobiera równomierne próbki wyłącznie z lokalnych plików filmowych."""
    katalog = dane / "nask/DANE"
    limity = cfg["wczytywanie"]
    n = cfg["proba_generalna"]["klatki_na_film"]
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "protocol_whitelist;file"
    for plik in _pliki(katalog, {".mp4", ".mov", ".avi", ".webm", ".mkv"}):
        film = cv2.VideoCapture(str(plik), cv2.CAP_FFMPEG)
        try:
            w, h = int(film.get(cv2.CAP_PROP_FRAME_WIDTH)), int(film.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if not 0 < w * h <= limity["maks_pikseli"]:
                continue
            ile = int(film.get(cv2.CAP_PROP_FRAME_COUNT))
            if ile <= 0:
                continue
            pozycje = sorted({(2 * i + 1) * ile // (2 * n) for i in range(n)})
            for indeks in pozycje:
                if indeks and not film.set(cv2.CAP_PROP_POS_FRAMES, indeks):
                    continue
                ok, klatka = film.read()
                if ok and 0 < klatka.shape[0] * klatka.shape[1] <= limity["maks_pikseli"]:
                    yield plik.relative_to(katalog.resolve()).parts, Image.fromarray(cv2.cvtColor(klatka, cv2.COLOR_BGR2RGB))
        finally:
            film.release()


def _kampanie(kreacje: list[Kreacja], pary: list[ParaKreacji]) -> dict[str, str]:
    grupy = {k.id: k.zgloszenie for k in kreacje}
    for p in pary:
        if p.a not in grupy or p.b not in grupy or p.relacja not in {"duplikat", "ta_sama_reklama"}:
            continue
        a, b = sorted((grupy[p.a], grupy[p.b]))
        grupy = {k: a if v == b else v for k, v in grupy.items()}
    return grupy


def _prog(oceny: list[Ocena]) -> float | None:
    if {ocena.prawda for ocena in oceny} != {True, False}:
        return None
    return prog_bez_falszywych(oceny)


def _wyniki_logo(material: MaterialHTML) -> dict[str, float]:
    dane = material.cechy._surowe_logo
    najwyzsze: dict[str, float] = {}
    if dane is not None:
        for k in dane[0]:
            najwyzsze[k.marka] = max(najwyzsze.get(k.marka, 0.0), k.wynik)
    return najwyzsze


def _kalibruj_grupe(trening: list[Kreacja], materialy: dict[str, MaterialHTML], kampanie: dict[str, str],
                  relacje: dict[frozenset[str], str], cfg: dict) -> dict:
    fold = deepcopy(cfg)
    h = fold["hashe"]
    nie = []
    tak = []
    oceny_cos = []
    for a, b in combinations(trening, 2):
        relacja = relacje.get(frozenset((a.id, b.id)))
        negatyw = relacja == "rozne" or (relacja is None and kampanie[a.id] != kampanie[b.id])
        lewy, prawy = materialy[a.id].cechy, materialy[b.id].cechy
        pomiar = odleglosci(lewy.hashe, prawy.hashe)
        if negatyw and pomiar:
            nie.append(pomiar)
        if relacja == "duplikat" and pomiar:
            tak.append(pomiar)
        cos = cosinus(lewy.wektor, prawy.wektor)
        if cos is not None and (negatyw or relacja in _ZWIAZANE):
            oceny_cos.append(Ocena(a.zgloszenie, cos, not negatyw))
    for k in trening:
        wzor = materialy[k.id]
        for w in warianty_porownania(wzor.obraz):
            if w.nazwa not in {"jpeg_q30", "odbicie", "skala_25"}:
                continue
            _, probka = analizuj_podstawy(w.obraz)
            pomiar = odleglosci(wzor.cechy.hashe, probka.hashe)
            if pomiar:
                tak.append(pomiar)
    para = dobierz_progi(tak, nie, h["zapas_bit"]) if tak and nie else None
    h["dowod"] = para is not None
    if para is not None:
        h["T_p"], h["T_d"] = para

    wyglad, logo = fold["wyglad"]["model"], fold["wyglad"]["model_logo"]
    prog = _prog(oceny_cos)
    for model in NAZWY_MODELI:
        fold["wyglad"][model].pop("T_w", None)
        fold["wyglad"][model]["elementy"] = {}
        fold["wyglad"][model]["logo"] = {}
        fold["wyglad"][model]["elementy_do_usuniecia"] = []
        fold["wyglad"][model]["elementy_mala_proba"] = []
    if prog is not None:
        fold["wyglad"][wyglad]["T_w"] = prog
    min_wystapien = fold["proba_generalna"]["min_przykladow_elementu"]
    for etykieta in fold["szablon"]["elementy"]:
        w = [Ocena(k.zgloszenie, materialy[k.id].cechy.elementy_szablonu[etykieta], etykieta in k.oczekiwane.elementy)
             for k in trening if k.oczekiwane.elementy is not None and etykieta in materialy[k.id].cechy.elementy_szablonu]
        n_dodatnich = sum(o.prawda for o in w)
        if min(n_dodatnich, len(w) - n_dodatnich) >= min_wystapien:
            if (prog := _prog(w)) is not None:
                fold["wyglad"][wyglad]["elementy"][etykieta] = prog
    wyniki_logo = {k.id: _wyniki_logo(materialy[k.id]) for k in trening}
    for marka in fold["szablon"]["marki"]:
        w = [Ocena(k.zgloszenie, wyniki_logo[k.id].get(marka, 0.0), marka in k.oczekiwane.logo)
             for k in trening if k.oczekiwane.logo is not None and materialy[k.id].cechy._surowe_logo is not None]
        if (prog := _prog(w)) is not None:
            fold["wyglad"][logo]["logo"][marka] = prog
    return fold


def _progi_twarzy(dane: Path, cfg: dict) -> dict:
    niezalezne = deepcopy(cfg)
    bazy = {s: wczytaj_baze(s, dane / "reference_people") for s in MODEL_TWARZY}
    for s, baza in bazy.items():
        progi = niezalezne["twarze"][s]
        pomiar = kalibruj(baza)
        for klucz in ("T_f", "T_osoba", "margines"):
            progi.pop(klucz, None)
            if pomiar and pomiar[klucz] is not None:
                progi[klucz] = pomiar[klucz]
    return {"cfg": niezalezne, "bazy": bazy}


def _nazwij(twarz: Twarz, cfg: dict, bazy: dict) -> Twarz:
    wektory = wektory_twarzy(twarz, cfg)
    glosy = {}
    for s, w in wektory.items():
        model = cfg[s]
        if "T_osoba" in model and "margines" in model and w.wersja == model.get("wersja_kalibracji"):
            glosy[s] = decyzja_osoby(ranking_osob(w, bazy[s]), model["T_osoba"], model["margines"])
    osoba, status = zgodnosc_glosow(glosy.get("buffalo_l"), glosy.get("yunet_sface"))
    return twarz.model_copy(update={"osoba": osoba, "status_osoby": status})


def _na_fold(m: MaterialHTML, cfg: dict, bazy: dict) -> MaterialHTML:
    cechy = m.cechy.model_copy()
    cechy.twarze = [_nazwij(t, cfg["twarze"], bazy) for t in cechy.twarze]
    cechy.logo_obraz = []
    if cechy._surowe_logo is not None:
        kandydaci, regiony = cechy._surowe_logo
        p = cechy.przygotowanie
        cechy.logo_obraz = wybierz_logo(kandydaci, regiony, cfg["wyglad"][cfg["wyglad"]["model_logo"]]["logo"],
                                       p.rozmiar_oryginalny, p.odciete_ramki[:2], p.orientacja_exif)
    return MaterialHTML(m.nazwa, m.obraz, cechy)


def wykonaj_probe(dane: Path) -> tuple[dict[str, Any], list[ParaHTML]]:
    """Kalibruje na pozostałych kampaniach i mierzy każdą odłożoną kampanię z negatywami."""
    negatywy = _pliki(dane / "proba_generalna/niezwiazane", ROZSZERZENIA - {".svg"})
    if not (dane / "wzorzec/kreacje.json").is_file() or not (dane / "nask/DANE").is_dir():
        return {"status": "pominieta", "powod": "brak katalogu NASK lub ręcznych adnotacji"}, []
    zbior = wczytaj_kreacje(dane)
    kreacje = [k for k in zbior.kreacje if k.status == "zatwierdzone"]
    kampanie = _kampanie(kreacje, zbior.pary)
    powody = []
    if not negatywy:
        powody.append("brak negatywów")
    if len(set(kampanie.values())) < 2:
        powody.append("brak co najmniej dwóch zatwierdzonych kampanii")
    if powody:
        return {"status": "pominieta", "powod": ", ".join(powody), "zatwierdzone": len(kreacje),
                "negatywy": len(negatywy)}, []

    cfg = wczytaj_konfiguracje()
    relacje = {frozenset((p.a, p.b)): p.relacja for p in zbior.pary}
    niezalezne = _progi_twarzy(dane, cfg)
    materialy = {k.id: analiza(k.id, wycinek(dane, k)) for k in kreacje}
    obce = []
    braki = []
    for i, p in enumerate(negatywy, 1):
        try:
            obraz = otworz_obraz(p)
        except (OSError, ValueError) as blad:
            braki.append(f"Negatyw {i}: {type(blad).__name__}")
            continue
        obce.append(analiza(f"negatyw-{i}", obraz))
    if not obce:
        return {"status": "pominieta", "powod": "brak negatywów możliwych do odczytu", "braki": braki}, []

    filmy = [(czesci, analiza(f"klatka-{i}", klatka)) for i, (czesci, klatka) in enumerate(klatki_nask(dane, cfg), 1)]
    pary = []
    foldy = []
    for kampania in sorted(set(kampanie.values())):
        trening = [k for k in kreacje if kampanie[k.id] != kampania]
        test = [k for k in kreacje if kampanie[k.id] == kampania]
        kalibracja = _kalibruj_grupe(trening, materialy, kampanie, relacje, niezalezne["cfg"])
        baza_fold = niezalezne["bazy"]
        widoki = {k.id: _na_fold(materialy[k.id], kalibracja, baza_fold) for k in test}
        material_obcy = [_na_fold(n, kalibracja, baza_fold) for n in obce]

        def dodaj(a: MaterialHTML, b: MaterialHTML, relacja: str) -> None:
            pary.append(ParaHTML(a, b, relacja, porownaj_cechy(a.cechy, b.cechy, kalibracja)))

        for k in test:
            wzor = widoki[k.id]
            probki = [wzor]
            for w in warianty_porownania(wzor.obraz):
                probka = _na_fold(analiza(f"{k.id}/{w.nazwa}", w.obraz), kalibracja, baza_fold)
                probki.append(probka)
                dodaj(wzor, probka, "wariant")
            for probka in probki:
                for negatyw in material_obcy:
                    dodaj(probka, negatyw, "niezwiazane")
        for a, b in combinations(test, 2):
            if relacja := relacje.get(frozenset((a.id, b.id))):
                dodaj(widoki[a.id], widoki[b.id], relacja)
        zgloszenia = {k.zgloszenie for k in test}
        for czesci, klatka in filmy:
            if not zgloszenia.intersection(czesci[:-1]):
                continue
            widok = _na_fold(klatka, kalibracja, baza_fold)
            for wzor in widoki.values():
                dodaj(wzor, widok, "klatka_kampanii")
            for negatyw in material_obcy:
                dodaj(widok, negatyw, "niezwiazane")
        foldy.append({"kampania": kampania, "trening": len(trening), "test": len(test),
                      "progi_hashy": [kalibracja["hashe"][k] for k in ("T_p", "T_d")] if kalibracja["hashe"]["dowod"] else None,
                      "prog_cos": kalibracja["wyglad"][cfg["wyglad"]["model"]].get("T_w"),
                      "progi_elementow": kalibracja["wyglad"][cfg["wyglad"]["model"]]["elementy"],
                      "progi_logo": kalibracja["wyglad"][cfg["wyglad"]["model_logo"]]["logo"]})
    falszywe = sum(d.sila in {"silny", "sredni"} for p in pary if p.relacja == "niezwiazane" for d in p.wynik.dowody)
    return {"status": "zaliczona" if falszywe == 0 else "niezaliczona", "falszywe_silne_srednie": falszywe,
            "grupy": foldy, "zatwierdzone": len(kreacje), "negatywy": len(obce), "klatki": len(filmy), "braki": braki}, pary
