"""Pomiary ORB, wydajności i próby generalnej."""
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .. import analizuj_obraz, analizuj_podstawy, porownaj
from ..konfiguracja import wczytaj_konfiguracje
from ..model import Kontekst, WynikPorownania
from ..orb import porownaj_orb
from .proba_generalna import analiza, klatki_nask, wykonaj_probe, wycinek
from ..przygotowanie import czytaj_bajty
from ..raport_html import MaterialHTML, ParaHTML
from ..warianty import scena_syntetyczna, warianty_porownania
from ..wzorzec import otworz_obraz, plik_danych, wczytaj_kreacje, wczytaj_pary

_HASH = {"identyczny_plik", "duplikat_kreacji"}
_WYGLAD = {"podobny_wyglad", "wspolne_elementy_szablonu"}
_WARSTWY = (("hashe", _HASH), ("+ CLIP", _WYGLAD), ("+ ORB", {"ten_sam_kadr"}),
            ("+ twarze", {"ta_sama_osoba", "ta_sama_twarz"}), ("+ logo", {"wspolne_logo"}), ("+ QR", {"wspolny_qr"}))
_OBCE = {"rozne", "niezwiazane"}
_WARIANTY_ORB = {"pasek_15", "przyciecie_10", "przyciecie_20", "przyciecie_30", "wstawka"}
_ETAPY_POMIARU = {
    "przygotowanie": ("wczytywanie", "przygotowanie", "filtr_tresci"),
    "hashe": ("hashe",),
    "CLIP": ("wektor", "wektory_wycinkow", "elementy_szablonu"),
    "logo": ("logo_obraz",),
    "twarze": ("twarze",),
    "MagFace": ("magface",),
    "FaceTorch": ("facetorch",),
    "QR": ("qr",),
}


def zmierz_wydajnosc(dane: Path) -> dict[str, Any]:
    """Mierzy trzy przebiegi analizy pojedynczych kreacji po pełnej rozgrzewce."""
    obrazy = [wycinek(dane, k) for k in wczytaj_kreacje(dane).kreacje]
    if not obrazy:
        raise ValueError("Brak kreacji do pomiaru")
    kontekst = Kontekst(zrodlo="pomiar_wydajnosci", rola="kreacja")
    for obraz in obrazy:
        analizuj_obraz(obraz, kontekst)

    przebiegi = []
    pomiary: dict[str, list[float]] = {etap: [] for etap in _ETAPY_POMIARU}
    wykonania: dict[str, int] = defaultdict(int)
    for _ in range(3):
        start = perf_counter()
        wyniki = [analizuj_obraz(obraz, kontekst) for obraz in obrazy]
        czas = perf_counter() - start
        przebiegi.append({"sekundy": czas, "obrazy_na_s": len(wyniki) / czas})
        for wynik in wyniki:
            for etap, czesci in _ETAPY_POMIARU.items():
                czasy = wynik.czasy_ms
                if not any(czesc in czasy for czesc in czesci):
                    continue
                pomiar = sum(czasy.get(czesc, 0.0) for czesc in czesci)
                if etap == "twarze":
                    pomiar -= czasy.get("magface", 0.0)
                pomiary[etap].append(max(0.0, pomiar))
            wykonania["obrazy_z_CLIP"] += wynik.wektor is not None
            wykonania["obrazy_z_modelem_logo"] += wynik.wersja_logo is not None
            wykonania["twarze"] += len(wynik.twarze)
            wykonania["obrazy_z_brakiem_wag_twarzy"] += bool(wynik.niedostepne_modele_twarzy)
            wykonania["twarze_z_MagFace"] += sum(t.miara_jakosci == "skladnik" for t in wynik.twarze)
            wykonania["twarze_z_FaceTorch"] += sum(t.deepfake is not None and t.emocja is not None for t in wynik.twarze)
    return {"obrazy_na_przebieg": len(obrazy), "przebiegi": przebiegi, "wykonania": dict(wykonania),
            "etapy_ms": {k: {"n": len(v), "mediana": median(v) if v else None, "maksimum": max(v, default=None)}
                         for k, v in pomiary.items()}}


def _lekko(nazwa: str, raster: Any) -> MaterialHTML:
    _, rekord = analizuj_podstawy(raster)
    return MaterialHTML(nazwa, raster, rekord)


def proby_orb(cfg: dict, ziarna: range) -> tuple[list[dict[str, Any]], list[ParaHTML]]:
    """Mierzy homografię dla znanych wariantów i rozłącznych scen."""
    dane = []
    pary = []
    obrazy = [_lekko(f"scena-{i}", scena_syntetyczna(i)) for i in ziarna]
    proby = [(a, _lekko(f"{a.nazwa}/{w.nazwa}", w.obraz), w.nazwa)
             for a in obrazy for w in warianty_porownania(a.obraz) if w.nazwa in _WARIANTY_ORB]
    proby += [(a, b, "rozne") for a, b in combinations(obrazy, 2)]
    for a, b in zip(obrazy, obrazy[1:] + obrazy[:1], strict=True):
        w, h = a.obraz.size
        obraz = b.obraz.copy()
        fragment = a.obraz.crop((w // 3, h // 3, 2 * w // 3, 2 * h // 3))
        obraz.paste(fragment, (w // 3, h // 3))
        proby.append((a, _lekko(b.nazwa + "/wspolny-fragment", obraz), "rozne_fragment"))
        rogi = np.float32(((0, 0), (w, 0), (w, h), (0, h)))
        cel = np.float32(((w * 0.09, h * 0.12), (w * 0.97, h * 0.06), (w * 0.89, h * 0.97), (w * 0.02, h * 0.85)))
        perspektywa = cv2.warpPerspective(np.asarray(a.obraz), cv2.getPerspectiveTransform(rogi, cel), (w, h))
        proby.append((a, _lekko(a.nazwa + "/perspektywa", Image.fromarray(perspektywa)), "perspektywa"))
    for a, b, rodzaj in proby:
        start = perf_counter()
        surowe, dowod = porownaj_orb(a.cechy, b.cechy, cfg["orb"])
        dodatnia = not rodzaj.startswith("rozne")
        dane.append({"wariant": rodzaj, "pozytyw": dodatnia, "trafienie": dowod is not None, **surowe,
                     "czas_ms": (perf_counter() - start) * 1000})
        wynik = WynikPorownania(dowody=[dowod] if dowod else [])
        wynik.metryki.orb = surowe
        pary.append(ParaHTML(a, b, "wariant" if dodatnia else "rozne", wynik))
    return dane, pary


def _ocena_dowodu(relacja: str, rodzaj: str, sila: str) -> str:
    if relacja in _OBCE:
        return "falszywe" if sila in {"silny", "sredni"} else "bez_adnotacji"
    if relacja == "identyczne":
        return "trafne" if rodzaj == "identyczny_plik" else "falszywe"
    if relacja == "ten_sam_szablon":
        return "trafne" if rodzaj in _WYGLAD else "falszywe" if rodzaj in _HASH | {"ten_sam_kadr"} else "bez_adnotacji"
    if relacja in {"wariant", "duplikat"} and rodzaj in _HASH | _WYGLAD | {"ten_sam_kadr"}:
        return "trafne"
    if relacja == "ta_sama_reklama" and rodzaj in _HASH | _WYGLAD | {"ten_sam_kadr"}:
        return "trafne"
    return "bez_adnotacji"


def wyniki_par(pary: list[ParaHTML], cfg: dict) -> dict[str, Any]:
    """Zlicza dowody według rodzaju i pary rozpoznane w kolejnych warstwach."""
    wedlug_rodzaju = {n: {"sila": sila, "trafne": 0, "falszywe": 0, "bez_adnotacji": 0, "czulosc": None}
                     for n, sila in cfg["dowody"].items()}
    liczby = defaultdict(int)
    for para in pary:
        liczby[para.relacja] += 1
        for d in para.wynik.dowody:
            stan = _ocena_dowodu(para.relacja, d.rodzaj, d.sila)
            wedlug_rodzaju[d.rodzaj][stan] += 1
    for w in wedlug_rodzaju.values():
        razem = w["trafne"] + w["falszywe"]
        w["precyzja"] = w["trafne"] / razem if razem else None
    for rodzaj, relacja in (("identyczny_plik", "identyczne"), ("duplikat_kreacji", "duplikat")):
        n = liczby[relacja]
        wedlug_rodzaju[rodzaj]["czulosc"] = (sum(p.relacja == relacja and any(d.rodzaj == rodzaj for d in p.wynik.dowody)
                                                for p in pary) / n if n else None)

    ablacja = []
    wlaczone: set[str] = set()
    for etap, rodzaje in _WARSTWY:
        wlaczone.update(rodzaje)
        dowody_par = [(p.relacja, [d for d in p.wynik.dowody if d.rodzaj in wlaczone]) for p in pary]
        ablacja.append({"etap": etap, "pary_trafne": sum(any(
                            _ocena_dowodu(rel, d.rodzaj, d.sila) == "trafne"
                            or (rel == "klatka_kampanii" and d.sila != "slaby") for d in ds) for rel, ds in dowody_par),
                        "pary_falszywe_silne_srednie": sum(any(d.sila != "slaby"
                            and _ocena_dowodu(rel, d.rodzaj, d.sila) == "falszywe" for d in ds)
                            for rel, ds in dowody_par),
                        "pary_slabe": sum(bool(ds) and all(d.sila == "slaby" for d in ds) for _, ds in dowody_par)})
    return {"porownania": len(pary), "relacje": dict(liczby), "dowody": wedlug_rodzaju, "ablacja": ablacja}


def _pary_z_adnotacji(dane: Path, cfg: dict) -> tuple[list[ParaHTML], dict]:
    pary = []
    limit = cfg["wczytywanie"]["maks_bajtow"]
    obrazy_stron: dict[str, MaterialHTML] = {}
    if (dane / "wzorzec/pary_obrazow.json").is_file():
        wpisy = wczytaj_pary(dane)
        pelna_analiza = {nazwa for wpis in wpisy if wpis.relacja == "rozne" for nazwa in (wpis.a, wpis.b)}
        for wpis in wpisy:
            for nazwa in (wpis.a, wpis.b):
                if nazwa in obrazy_stron:
                    continue
                p = plik_danych(dane, nazwa)
                try:
                    miniatura = otworz_obraz(p)
                except (OSError, ValueError):
                    miniatura = None
                tresc = czytaj_bajty(p, limit)
                id_ = f"obraz strony {len(obrazy_stron) + 1}"
                rekord = (analizuj_obraz(tresc, Kontekst(zrodlo=id_, rola="obrazek_strony"))
                          if nazwa in pelna_analiza else analizuj_podstawy(tresc)[1])
                obrazy_stron[nazwa] = MaterialHTML(id_, miniatura, rekord)
            a, b = obrazy_stron[wpis.a], obrazy_stron[wpis.b]
            pary.append(ParaHTML(a, b, wpis.relacja, porownaj(a.cechy, b.cechy)))

    wyniki = {}
    tani_s = pelny_s = 0.0
    if (dane / "wzorzec/kreacje.json").is_file():
        zbior = wczytaj_kreacje(dane)
        obrazy = {k.id: wycinek(dane, k) for k in zbior.kreacje}
        start = perf_counter()
        for obraz in obrazy.values():
            analizuj_podstawy(obraz)
        tani_s = perf_counter() - start
        start = perf_counter()
        wyniki = {id_: analiza(id_, obraz) for id_, obraz in obrazy.items()}
        pelny_s = perf_counter() - start
        for wpis in zbior.pary:
            a, b = wyniki[wpis.a], wyniki[wpis.b]
            pary.append(ParaHTML(a, b, wpis.relacja, porownaj(a.cechy, b.cechy)))
    n = len(wyniki)
    return pary, {"obrazy": n, "tani_s": tani_s, "pelny_s": pelny_s,
                  "tani_na_s": n / tani_s if n else None, "pelny_na_s": n / pelny_s if n else None}


def ewaluuj_porownania(dane: Path) -> tuple[dict[str, Any], list[ParaHTML]]:
    """Mierzy osobno diagnostykę oraz kwalifikowaną próbę generalną."""
    cfg = wczytaj_konfiguracje()
    pomiar_orb, pary_orb = proby_orb(cfg, range(900, 905))
    porownania, szybkosc = _pary_z_adnotacji(dane, cfg)
    formalna, pary_formalne = wykonaj_probe(dane)
    if pary_formalne:
        formalna["wyniki"] = wyniki_par(pary_formalne, cfg)
    klatki = sum(1 for _ in klatki_nask(dane, cfg)) if formalna["status"] == "pominieta" else formalna["klatki"]
    rozklad = {}
    for nazwa in sorted({w["wariant"] for w in pomiar_orb}):
        wybrane = [w for w in pomiar_orb if w["wariant"] == nazwa]
        rozklad[nazwa] = {"pary": len(wybrane), "odczytane": sum(w["trafienie"] for w in wybrane),
                         "mediana_ms": median(w["czas_ms"] for w in wybrane)}
    wynik = {"orb": rozklad, "orb_proby": pomiar_orb, "diagnostyka": wyniki_par(porownania, cfg),
             "proba_generalna": formalna, "klatki_odczytane": klatki, "wydajnosc": szybkosc,
             "pary_diagnostyczne": [{"a": p.a.nazwa, "b": p.b.nazwa, "relacja": p.relacja,
                                     **p.wynik.model_dump()} for p in porownania]}
    return wynik, [*pary_formalne, *porownania, *pary_orb]
