"""Porównanie liczby twarzy i jakości dopasowania modeli."""
import json
from collections import Counter
from hashlib import file_digest, sha256
from pathlib import Path
from collections.abc import Sequence
from statistics import mean
from time import perf_counter
from typing import Any

import cv2
from PIL import Image

from ..konfiguracja import wczytaj_konfiguracje
from ..model import CechyObrazu, Twarz, Wektor
from ..przygotowanie import ROZSZERZENIA, czytaj_bajty, przygotuj_obraz
from ..referencje_twarzy import Baza, decyzja_osoby, kalibruj, ranking_osob, wczytaj_baze, wczytaj_probki
from ..twarze import MODEL_TWARZY, Silnik, pary_detekcji, silnik_twarzy, wykryj_twarze, zgodnosc_glosow
from ..wzorzec import otworz_obraz, plik_danych, wczytaj_kreacje


def _material_testowy(dane: Path, limity: dict[str, int]) -> tuple[list[tuple[str, Image.Image | None, CechyObrazu]], int]:
    """Czyta testy tylko z osobnego katalogu, bez wysyłania zdjęć do bazy."""
    referencje = set()
    katalog_referencji = (dane / "reference_people").resolve()
    for plik in katalog_referencji.glob("*/*"):
        if (plik.resolve().is_relative_to(katalog_referencji) and plik.suffix.lower() in ROZSZERZENIA
                and plik.is_file() and plik.stat().st_size <= limity["maks_bajtow"]):
            with plik.open("rb") as zrodlo:
                referencje.add(file_digest(zrodlo, "sha256").hexdigest())
    material = []
    wspolne = 0
    for plik in sorted((dane / "twarze_testowe").glob("*/opis.json")):
        opis = json.loads(plik.read_text(encoding="utf-8-sig"))
        osoba = opis["osoba"].strip()
        for wpis in opis["zdjecia"]:
            sciezka = (plik.parent / wpis["plik"]).resolve()
            if (not osoba or wpis.get("osoba", osoba).strip() != osoba
                    or not sciezka.is_relative_to(plik.parent.resolve())
                    or sciezka.suffix.lower() not in ROZSZERZENIA - {".svg"}):
                raise ValueError("Niespójny wpis w lokalnym opisie zdjęcia testowego")
            tresc = czytaj_bajty(sciezka, limity["maks_bajtow"])
            if sha256(tresc).hexdigest() in referencje:
                wspolne += 1
                continue
            raster, cechy = przygotuj_obraz(tresc)
            material.append((osoba, raster, cechy))
    return material, wspolne


def _nazwiska(twarze: list[Twarz], baza: Baza, progi: dict[str, Any] | None) -> set[str]:
    if not progi or progi["T_osoba"] is None:
        return set()
    return {osoba for t in twarze if t.wektor is not None
            if (osoba := decyzja_osoby(ranking_osob(t.wektor, baza), progi["T_osoba"], progi["margines"])) is not None}


def _ocena_testow(material: list[tuple[str, Image.Image | None, CechyObrazu]], baza: Baza, sigiel: str,
                  model: Silnik, progi: dict[str, Any] | None) -> dict[str, Any] | None:
    if not material or not progi or progi["T_osoba"] is None:
        return None
    macierz = Counter()
    trafienia = bledne = nieznane = grupowe = bez_wektora = 0
    for prawda, raster, cechy in material:
        twarze = wykryj_twarze(raster, cechy, sigiel, model) if raster is not None else []
        grupowe += len(twarze) > 1
        bez_wektora += not any(t.wektor is not None for t in twarze)
        nazwiska = _nazwiska(twarze, baza, progi)
        trafienia += nazwiska == {prawda}
        bledne += bool(nazwiska - {prawda})
        nieznane += not nazwiska
        macierz[prawda, ", ".join(sorted(nazwiska)) or "nieznana"] += 1
    return {"trafne": trafienia, "zdjecia": len(material), "pomylki": bledne, "nieznane": nieznane,
            "zdjecia_grupowe": grupowe, "bez_wektora": bez_wektora,
            "macierz": [{"prawda": a, "decyzja": b, "liczba": n} for (a, b), n in sorted(macierz.items())]}


def _glos_probki(
    indeks: dict[tuple[str, str], Wektor], klucz: tuple[str, str], progi: dict[str, Any], obca: bool,
) -> str | None:
    wektor = indeks.get(klucz)
    if wektor is None or progi.get("T_osoba") is None:
        return None
    baza: Baza = {}
    for rekord, wpis in indeks.items():
        if rekord != klucz and not (obca and rekord[0] == klucz[0]):
            baza.setdefault(rekord[0], []).append(wpis)
    return decyzja_osoby(ranking_osob(wektor, baza), progi["T_osoba"], progi["margines"])


def _licz(oceny: Sequence[tuple[str | None, str | None, str]]) -> dict[str, int]:
    return {
        "proby": len(oceny), "trafne": sum(a == b and s != "niepewne" for a, b, s in oceny),
        "bledne_nazwiska": sum(b is not None and a != b for a, b, _ in oceny),
        "niepewne": sum(s == "niepewne" for _, _, s in oceny),
        "nieznane": sum(s == "nieznana" for _, _, s in oceny),
    }


def _ocena_glosow(dane: Path, modele: dict, zbiory: dict, iou: float) -> dict[str, Any]:
    sigle = tuple(MODEL_TWARZY)
    progi = [modele[s]["kalibracja"] or {} for s in sigle]
    indeksy = [wczytaj_probki(s, dane / "reference_people") for s in sigle]
    klucze = sorted(set(indeksy[0]) | set(indeksy[1]))
    znane, symulowane = [], []
    osobno = {s: {"znane": [], "symulowane": []} for s in sigle}
    for klucz in klucze:
        osoba = klucz[0]
        dostepni = [klucz in indeks and sum(k[0] == osoba for k in indeks) >= 2 for indeks in indeksy]
        glosy = [_glos_probki(indeks, klucz, p, False) if dostepny else None
                 for indeks, p, dostepny in zip(indeksy, progi, dostepni, strict=True)]
        obce = [_glos_probki(indeks, klucz, p, True) for indeks, p in zip(indeksy, progi, strict=True)]
        if any(dostepni):
            znane.append((osoba, *zgodnosc_glosow(*glosy)))
        symulowane.append((None, *zgodnosc_glosow(*obce)))
        for i, s in enumerate(sigle):
            if dostepni[i]:
                osobno[s]["znane"].append((osoba, glosy[i], "rozpoznana" if glosy[i] else "nieznana"))
            if klucz in indeksy[i]:
                osobno[s]["symulowane"].append((None, obce[i], "rozpoznana" if obce[i] else "nieznana"))
    kontrole = []
    for id_, lewy in zbiory[sigle[0]][1].items():
        prawy = zbiory[sigle[1]][1].get(id_, [])
        glosy = []
        for a, b in pary_detekcji(lewy, prawy, iou):
            nazwy = [next(iter(_nazwiska([t], zbiory[s][0], p)), None) if t is not None else None
                     for t, s, p in zip((a, b), sigle, progi, strict=True)]
            glosy.append(zgodnosc_glosow(*nazwy))
        kontrole.append({"id": id_, "wyniki": [{"osoba": n, "status": s} for n, s in glosy]})
    return {"znane": _licz(znane), "symulowane": _licz(symulowane), "kontrole": kontrole,
            "osobno": {s: {k: _licz(w) for k, w in q.items()} for s, q in osobno.items()}}


def ewaluuj_twarze(dane: Path, uwzglednij_szkice: bool = False, nieznane: tuple[str, ...] = ()) -> dict[str, Any]:
    """Kalibruje z bazy i kontroli, a zdjęcia testowe raportuje osobno."""
    plik = dane / "wzorzec/kreacje.json"
    wycinki = []
    braki = []
    konfiguracja = wczytaj_konfiguracje()
    cfg = konfiguracja["twarze"]
    material, duplikaty_testowe = _material_testowy(dane, konfiguracja["wczytywanie"])
    if plik.is_file():
        for k in wczytaj_kreacje(dane).kreacje:
            if k.status != "zatwierdzone" and not uwzglednij_szkice:
                continue
            if k.oczekiwane.liczba_twarzy is None and k.id not in nieznane:
                continue
            x, y, w, h = k.prostokat_kreacji
            wycinek = otworz_obraz(plik_danych(dane, k.plik)).crop((x, y, x + w, y + h))
            raster, cechy = przygotuj_obraz(wycinek)
            if raster is not None:
                wycinki.append((k.id, k.oczekiwane.liczba_twarzy, raster, cechy))
    if not wycinki:
        braki.append("Brak dostępnych kreacji z adnotacją liczba_twarzy.")
    if duplikaty_testowe:
        braki.append(f"Pominięto {duplikaty_testowe} zdjęć testowych identycznych z materiałem referencyjnym.")

    modele = {}
    zbiory = {}
    rozgrzewka, cechy_rozgrzewki = przygotuj_obraz(Image.new("RGB", (256, 128), "white"))
    for sigiel in MODEL_TWARZY:
        try:
            model = silnik_twarzy(sigiel)
            wykryj_twarze(rozgrzewka, cechy_rozgrzewki, sigiel, model)
            pomiary = []
            kontrole = {}
            for id_, ile, raster, cechy in wycinki:
                start = perf_counter()
                twarze = wykryj_twarze(raster, cechy, sigiel, model)
                if id_ in nieznane:
                    kontrole[id_] = twarze
                if ile is not None:
                    pomiary.append({"id": id_, "oczekiwane": ile, "po_limicie": min(ile, cfg["maks_liczba"]),
                                    "wykryte": len(twarze), "uzyteczne_wektory": sum(t.wektor is not None for t in twarze),
                                    "czas_ms": (perf_counter() - start) * 1000})
            baza = wczytaj_baze(sigiel, dane / "reference_people")
            zgodna_baza = {osoba: zgodne for osoba, wektory in baza.items()
                           if (zgodne := [w for w in wektory if w.wersja == model.wersja and w.model == MODEL_TWARZY[sigiel]])}
            kontrolne_wektory = [t.wektor for twarze in kontrole.values() for t in twarze if t.wektor is not None]
            kalibracja = kalibruj(zgodna_baza, kontrolne_wektory)
            if kalibracja is None:
                braki.append(f"{sigiel}: brak wystarczającej zgodnej bazy do sprawdzianu «wyjmij jedno».")
            zbiory[sigiel] = (zgodna_baza, kontrole)
            modele[sigiel] = {
                "wersja": model.wersja,
                "wykrycie": pomiary,
                "zgodne_liczby": sum(p["wykryte"] == p["po_limicie"] for p in pomiary),
                "mae": mean(abs(p["wykryte"] - p["po_limicie"]) for p in pomiary) if pomiary else None,
                "sredni_czas_ms": mean(p["czas_ms"] for p in pomiary) if pomiary else None,
                "osoby_referencyjne": len(zgodna_baza),
                "zdjecia_referencyjne": sum(map(len, zgodna_baza.values())),
                "kalibracja": kalibracja,
                "zdjecia_testowe": _ocena_testow(material, zgodna_baza, sigiel, model, kalibracja),
                "kontrole_nieznanych": [
                    {"id": id_, "twarze": len(kontrole.get(id_, [])),
                     "wektory": sum(t.wektor is not None for t in kontrole.get(id_, [])),
                     "nazwiska": sorted(_nazwiska(kontrole.get(id_, []), zgodna_baza, kalibracja))}
                    for id_ in nieznane
                ],
            }
        except (OSError, ValueError, RuntimeError, ImportError, KeyError, cv2.error) as blad:
            braki.append(f"{sigiel}: {blad}")
    laczony = _ocena_glosow(dane, modele, zbiory, cfg["prog_iou_glosow"]) if set(modele) == set(MODEL_TWARZY) else None
    return {"modele": modele, "laczony": laczony, "braki": braki, "kreacje": len(wycinki), "uwzglednij_szkice": uwzglednij_szkice,
            "liczba_zdjec_testowych": len(material), "duplikaty_testowe": duplikaty_testowe}
