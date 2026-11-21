"""Adapter modułu obrazu dla potoku i API.

Bierze z materiału (Incident) zrzuty, obrazy ze strony i klatki wideo wycięte przez preprocessing,
liczy cechy jedną partią (`analizuj_obrazy`: duplikaty i klatki tego samego ujęcia liczone raz)
i składa wynik:
  * `items`  – cechy każdego obrazu i klatki + skrót do porównań (sha256, pHash, osoby, logo, QR),
  * `kreacje` – zbiorczo dla zrzutów i obrazów (najlepsze twarze, logo, QR, elementy szablonu),
  * `wideo`  – zbiorczo dla klatek każdego filmu: twarze lektora / celebryty i logo w nagraniu.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import analizuj_obraz, analizuj_obrazy, na_rekord, porownaj
from .konfiguracja import wczytaj_konfiguracje
from .model import CechyObrazu, CechyZbiorcze, Kontekst
from .przygotowanie import czytaj_bajty
from .zbierz import zbierz

MAX_KLATEK = int(os.environ.get("IMAGE_MAX_FRAMES", "12"))
MAX_OBRAZOW = int(os.environ.get("IMAGE_MAX_IMAGES", "40"))
KLATKI_FFMPEG = 60
ROLA = {"screenshot": "kreacja", "image": "obrazek_strony", "frame": "klatka"}
_NUMER_KLATKI = re.compile(r"frame_(\d+)\.")


def limit_bajtow() -> int:
    return int(wczytaj_konfiguracje()["wczytywanie"]["maks_bajtow"])


def _widok(c: CechyObrazu | CechyZbiorcze, wektory: bool) -> dict[str, Any]:
    return c.model_dump(mode="json") if wektory else c.dla_frontu()


def skrot(c: CechyObrazu) -> dict[str, Any]:
    """Pola do szybkiego porównania i do bazy (bez wektorów)."""
    r = na_rekord(c)
    return {
        "sha256": c.sha256 or None,
        "screen_phash": r["screen_phash"],
        "persons_visual": r["persons_visual"],
        "deepfake_score": r["deepfake_score"],
        "faces_count": len(c.twarze),
        "logo": sorted({l.marka for l in c.logo_obraz}),
        "qr": [k.tekst for k in c.qr],
        "similar_group": c.grupa_podobnych,
        "same_as": c.jak_obraz,
    }


def wynik_obrazu(c: CechyObrazu, nazwa: str, rola: str, wektory: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"plik": nazwa, "rola": rola, **skrot(c), "cechy": _widok(c, wektory)}
    if wektory:
        out["rekord"] = na_rekord(c)
    return out


def analizuj_plik(dane: bytes, nazwa: str, rola: str = "kreacja", wektory: bool = False) -> dict[str, Any]:
    """Jeden obraz (endpoint /image/analyze)."""
    c = analizuj_obraz(dane, Kontekst(zrodlo=nazwa, rola=rola))
    return wynik_obrazu(c, nazwa, rola, wektory)


def porownaj_pliki(a: tuple[str, bytes], b: tuple[str, bytes]) -> dict[str, Any]:
    """Dwa obrazy (endpoint /image/compare): dowody z siłą i metryki."""
    ca = analizuj_obraz(a[1], Kontekst(zrodlo=a[0], rola="kreacja"))
    cb = analizuj_obraz(b[1], Kontekst(zrodlo=b[0], rola="kreacja"))
    return {"compare": porownaj(ca, cb).model_dump(mode="json"),
            "a": wynik_obrazu(ca, a[0], "kreacja"), "b": wynik_obrazu(cb, b[0], "kreacja")}


def _rowno(lista: list, k: int) -> list:
    if k <= 0:
        return []
    if len(lista) <= k:
        return lista
    if k == 1:
        return [lista[len(lista) // 2]]
    return [lista[round(i * (len(lista) - 1) / (k - 1))] for i in range(k)]


def _czas_klatki(art: Any) -> float | None:
    """Klatki z ffmpeg: co `max(1, czas/60)` s, numerowane od 1 (ingest.video.extract_frames)."""
    m = _NUMER_KLATKI.search(Path(art.path).name)
    if not m:
        return None
    dlugosc = float(art.meta.get("duration") or 0)
    co = max(1.0, dlugosc / KLATKI_FFMPEG) if dlugosc else 1.0
    return round((int(m.group(1)) - 1) * co, 1)


def _podsumuj_wideo(nazwa: str, wszystkie: int, cechy: list[CechyObrazu], wektory: bool) -> dict[str, Any]:
    zb = zbierz(cechy)
    widok = _widok(zb, wektory)
    return {
        "plik": nazwa,
        "klatki_w_filmie": wszystkie,
        "klatki_przeanalizowane": len(cechy),
        "persons_visual": sorted({t.osoba for t in zb.twarze if t.osoba}),
        "deepfake_score": max((t.deepfake for t in zb.twarze if t.deepfake is not None), default=None),
        "faces_count": len(zb.twarze),
        "logo": sorted({l.marka for l in zb.logo}),
        "qr": [k.tekst for k in zb.qr],
        "zbiorczo": widok,
    }


def extract(incident: Any, wektory: bool = False) -> dict[str, Any] | None:
    """Incident -> cechy obrazów. None, gdy materiał nie ma żadnego obrazu ani klatki."""
    arts = [a for a in incident.for_module("image") if a.path]
    if not arts:
        return None
    maks = limit_bajtow()
    braki: list[str] = []

    statyczne = [a for a in arts if a.kind != "frame"]
    if len(statyczne) > MAX_OBRAZOW:
        braki.append(f"pominięto {len(statyczne) - MAX_OBRAZOW} obrazów (limit IMAGE_MAX_IMAGES={MAX_OBRAZOW})")
        statyczne = statyczne[:MAX_OBRAZOW]
    klatki: dict[str, list[Any]] = defaultdict(list)
    for a in arts:
        if a.kind == "frame":
            klatki[a.meta.get("video") or ""].append(a)
    wybrane = list(statyczne)
    for film in klatki:
        wybrane += _rowno(klatki[film], MAX_KLATEK)

    wejscie, opis = [], []
    for a in wybrane:
        nazwa = Path(a.path).name
        try:
            dane = czytaj_bajty(Path(a.path), maks)
        except (OSError, ValueError) as blad:
            braki.append(f"{nazwa}: {blad}")
            continue
        rola = ROLA.get(a.kind, "kreacja")
        czas = _czas_klatki(a) if a.kind == "frame" else None
        wejscie.append((dane, Kontekst(zrodlo=nazwa, rola=rola, czas_klatki=czas)))
        opis.append((a, nazwa, rola, czas))

    wyniki = analizuj_obrazy(wejscie)
    items, kreacje, per_film = [], [], defaultdict(list)
    for (a, nazwa, rola, czas), c in zip(opis, wyniki, strict=True):
        item = wynik_obrazu(c, nazwa, rola, wektory)
        item["zrodlo"] = a.source
        if a.kind == "frame":
            film = a.meta.get("video") or ""
            item["wideo"] = Path(film).name if film else None
            item["czas_klatki"] = czas
            per_film[film].append(c)
        else:
            kreacje.append(c)
        items.append(item)

    braki_modeli = sorted({f"{b.skladnik}: {b.powod}" for c in wyniki for b in c.braki})
    return {
        "items": items,
        "kreacje": _widok(zbierz(kreacje), wektory) if kreacje else None,
        "wideo": [_podsumuj_wideo(Path(f).name if f else "?", len(klatki[f]), cs, wektory)
                  for f, cs in per_film.items()],
        "persons_visual": sorted({o for c in wyniki for o in skrot(c)["persons_visual"]}),
        "deepfake_score": max((d for c in wyniki if (d := skrot(c)["deepfake_score"]) is not None), default=None),
        "logo": sorted({l.marka for c in wyniki for l in c.logo_obraz}),
        "qr": sorted({k.tekst for c in wyniki for k in c.qr}),
        "images_analyzed": len(wyniki),
        "heavy_model_calls": wyniki.wywolania_ciezkich_modeli,
        "czasy_ms": {k: round(v, 1) for k, v in wyniki.czasy_ms.items()},
        "missing": braki,
        "braki_modeli": braki_modeli,
    }
