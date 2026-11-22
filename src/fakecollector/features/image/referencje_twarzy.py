"""Lokalna baza referencyjna, dopasowanie i sprawdzian „wyjmij jedno”."""
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from itertools import combinations
from math import nextafter
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any
from urllib.parse import urlsplit

import numpy as np
from pydantic import Field, ValidationError

from .konfiguracja import wczytaj_konfiguracje
from .model import Kandydat, Model, Twarz, Wektor
from .przygotowanie import ROZSZERZENIA, czytaj_bajty, przygotuj_obraz
from .twarze import MODEL_TWARZY, wykryj_twarze
from .wektor import cosinus

KATALOG_REFERENCJI = Path(__file__).resolve().parents[4] / "data/reference_people"
Niepusty = Annotated[str, Field(min_length=1)]
Baza = dict[str, list[Wektor]]


class ZrodloZdjecia(Model):
    plik: Niepusty
    url: Niepusty
    autor: Niepusty
    licencja: Niepusty
    tytul_pliku: str | None = None


class ZrodlaOsoby(Model):
    osoba: Niepusty
    zdjecia: list[ZrodloZdjecia]


@dataclass(frozen=True)
class Proba:
    osoba: str | None
    ranking: list[Kandydat]


def _plik_bazy(katalog: Path, sigiel: str) -> Path:
    if sigiel not in MODEL_TWARZY:
        raise ValueError(f"Nieznany model twarzy {sigiel}")
    return katalog / f"wektory_{sigiel}.sqlite3"


def _zapisz_baze(katalog: Path, sigiel: str, baza: Baza, zdjecia: dict[str, list[str]]) -> None:
    plik = _plik_bazy(katalog, sigiel)
    with NamedTemporaryFile(dir=katalog, suffix=".sqlite3", delete=False) as roboczy:
        tymczasowy = Path(roboczy.name)
    try:
        with closing(sqlite3.connect(tymczasowy)) as db, db:
            db.execute("""CREATE TABLE wektory (osoba TEXT, pozycja INTEGER, id_zdjecia TEXT, model TEXT,
                          wersja TEXT, wymiar INTEGER, float32 BLOB, PRIMARY KEY (osoba, pozycja))""")
            for osoba, wektory in baza.items():
                w = wektory[0]
                srednia = np.mean([v.wartosci for v in wektory], axis=0)
                norma = np.linalg.norm(srednia)
                if not norma:
                    raise ValueError(f"Nie można znormalizować średniego wektora osoby {osoba}")
                wartosci = [srednia / norma, *(v.wartosci for v in wektory)]
                db.executemany("INSERT INTO wektory VALUES (?,?,?,?,?,?,?)", [
                    (osoba, i, id_, w.model, w.wersja, w.wymiar, np.asarray(v, dtype="<f4").tobytes())
                    for i, (id_, v) in enumerate(zip([None, *zdjecia[osoba]], wartosci, strict=True))
                ])
        tymczasowy.replace(plik)
    finally:
        tymczasowy.unlink(missing_ok=True)


def wczytaj_probki(sigiel: str, katalog: Path = KATALOG_REFERENCJI) -> dict[tuple[str, str], Wektor]:
    """Czyta próbki ze stabilnymi identyfikatorami do połączenia głosów modeli."""
    plik = _plik_bazy(katalog, sigiel)
    if not plik.is_file():
        return {}
    probki = {}
    try:
        with closing(sqlite3.connect(f"{plik.resolve().as_uri()}?mode=ro", uri=True)) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            kolumny = {w[1] for w in db.execute("PRAGMA table_info(wektory)")}
            identyfikator = "id_zdjecia" if "id_zdjecia" in kolumny else "CAST(pozycja AS TEXT)"
            for osoba, id_, model, wersja, wymiar, bufor in db.execute(
                f"SELECT osoba, {identyfikator}, model, wersja, wymiar, float32 FROM wektory "
                "WHERE pozycja > 0 ORDER BY osoba, pozycja",
            ):
                wektor = Wektor(model=model, wersja=wersja, wymiar=wymiar,
                                wartosci=np.frombuffer(bufor, dtype="<f4").tolist())
                probki[osoba, id_] = wektor
    except sqlite3.Error as blad:
        raise ValueError(f"Nie można odczytać bazy {sigiel}") from blad
    return probki


def wczytaj_baze(sigiel: str, katalog: Path = KATALOG_REFERENCJI) -> Baza:
    """Odczytuje wektory zdjęć, pomijając zapisany wektor średni."""
    baza: Baza = {}
    for (osoba, _), w in wczytaj_probki(sigiel, katalog).items():
        baza.setdefault(osoba, []).append(w)
    return baza


def _zrodla(folder: Path) -> list[tuple[str, Path, bool]]:
    opis = folder / "zrodla.json"
    if not opis.resolve().is_relative_to(folder.resolve()):
        raise ValueError("Opis źródeł poza katalogiem osoby")
    zrodla = ZrodlaOsoby.model_validate_json(opis.read_text(encoding="utf-8-sig"))
    if not zrodla.osoba.strip():
        raise ValueError("Brak nazwy osoby w metadanych źródłowych")
    zdjecia = []
    widziane = set()
    for zrodlo in zrodla.zdjecia:
        plik = (folder / zrodlo.plik).resolve()
        url = urlsplit(zrodlo.url)
        if (not plik.is_relative_to(folder.resolve()) or plik.suffix.lower() not in ROZSZERZENIA - {".svg"}
                or not plik.is_file() or plik in widziane or url.scheme not in {"http", "https"} or not url.netloc
                or not zrodlo.autor.strip() or not zrodlo.licencja.strip()):
            raise ValueError(f"Nieprawidłowa ścieżka lub źródło obrazu {Path(zrodlo.plik).name}")
        widziane.add(plik)
        zdjecia.append((zrodla.osoba.strip(), plik, zrodlo.licencja.casefold().startswith("nieustalona")))
    return zdjecia


def zbuduj_baze(katalog: Path, sigiel: str) -> dict[str, Any]:
    """Buduje bazę wyłącznie z dostarczonych zdjęć z podpisanymi źródłami."""
    baza: Baza = {}
    identyfikatory: dict[str, list[str]] = {}
    bez_licencji = []
    braki = []
    foldery = sorted(p for p in katalog.iterdir() if p.is_dir() and not p.is_symlink()) if katalog.is_dir() else []
    limity = wczytaj_konfiguracje()["wczytywanie"]
    for folder in foldery:
        try:
            zdjecia = _zrodla(folder)
        except (OSError, ValueError, ValidationError):
            braki.append({"osoba": folder.name, "blad": "Brak lub nieprawidłowy zrodla.json"})
            continue
        if not zdjecia:
            braki.append({"osoba": folder.name, "blad": "Pusta lista zdjęć"})
        for osoba, plik, lokalne in zdjecia:
            if lokalne:
                bez_licencji.append({"osoba": osoba, "plik": plik.name, "oznaczenie": "*"})
            try:
                obraz, cechy = przygotuj_obraz(czytaj_bajty(plik, limity["maks_bajtow"]))
            except (OSError, ValueError):
                obraz = None
            if obraz is None:
                braki.append({"osoba": osoba, "plik": plik.name, "blad": "Nieczytelny obraz"})
                continue
            twarze = wykryj_twarze(obraz, cechy, sigiel)
            if len(twarze) != 1:
                braki.append({"osoba": osoba, "plik": plik.name, "blad": "Wymagana jedna twarz", "twarze": len(twarze)})
            elif twarze[0].wektor is None:
                braki.append({"osoba": osoba, "plik": plik.name, "blad": "Twarz bez użytecznego wektora",
                              "jakosc": twarze[0].jakosc})
            else:
                baza.setdefault(osoba, []).append(twarze[0].wektor)
                identyfikatory.setdefault(osoba, []).append(plik.relative_to(katalog.resolve()).as_posix())
    if baza:
        _zapisz_baze(katalog, sigiel, baza, identyfikatory)
    return {"osoby": len(baza), "zdjecia": sum(map(len, baza.values())), "braki": braki,
            "bez_ustalonej_licencji": bez_licencji, "kalibracja": kalibruj(baza) if baza else None}


def ranking_osob(szukany: Wektor, baza: Mapping[str, Sequence[Wektor]]) -> list[Kandydat]:
    """Każdą osobę reprezentuje najlepszy zgodny wektor jej zdjęcia."""
    ranking = []
    for osoba, wektory in baza.items():
        maksimum = max((s for w in wektory if (s := cosinus(szukany, w)) is not None), default=None)
        if maksimum is not None:
            ranking.append(Kandydat(osoba=osoba, dopasowanie=maksimum))
    return sorted(ranking, key=lambda k: (-k.dopasowanie, k.osoba))


def decyzja_osoby(ranking: list[Kandydat], prog: float, margines: float) -> str | None:
    if len(ranking) < 2:
        return None
    a, b = ranking[:2]
    if a.dopasowanie >= prog and a.dopasowanie > b.dopasowanie and a.dopasowanie - b.dopasowanie >= margines:
        return a.osoba
    return None


def dopasuj_osobe(twarz: Twarz, baza: Baza, prog: float, margines: float) -> None:
    """Pozostawia osobę pustą, gdy wynik lub przewaga nad drugim kandydatem jest za mała."""
    if twarz.wektor is None:
        return
    ranking = ranking_osob(twarz.wektor, baza)
    twarz.osoba = decyzja_osoby(ranking, prog, margines)
    twarz.dopasowanie = ranking[0].dopasowanie if ranking else None
    twarz.drugi_kandydat = ranking[1] if len(ranking) > 1 else None


def proby_bazy(baza: Baza) -> list[Proba]:
    """Odkłada jedno zdjęcie tożsamości z powtórką albo całą tożsamość dla kontroli."""
    proby = []
    for osoba, zdjecia in baza.items():
        pozostali = {k: v for k, v in baza.items() if k != osoba}
        for i, wektor in enumerate(zdjecia):
            if len(zdjecia) >= 2:
                znani = {**pozostali, osoba: [p for j, p in enumerate(zdjecia) if j != i]}
                proby.append(Proba(osoba, ranking_osob(wektor, znani)))
            proby.append(Proba(None, ranking_osob(wektor, pozostali)))
    return proby


def _prog_twarzy(baza: Baza, kontrole: Sequence[Wektor]) -> float | None:
    zdjecia = [(osoba, wektor) for osoba, wektory in baza.items() for wektor in wektory]
    pomiary = [(a == b, wynik) for (a, w), (b, v) in combinations(
        zdjecia, 2,
    ) if (wynik := cosinus(w, v)) is not None]
    pomiary.extend((False, wynik) for kontrola in kontrole for _, w in zdjecia
                   if (wynik := cosinus(kontrola, w)) is not None)
    max_obcych = max((wynik for zgodny, wynik in pomiary if not zgodny), default=None)
    if max_obcych is None:
        return None
    najblizszy_powyzej = min((wynik for zgodny, wynik in pomiary if zgodny and wynik > max_obcych), default=None)
    return (max_obcych + najblizszy_powyzej) / 2 if najblizszy_powyzej is not None else None


def _porownanie_progow(
    proby: list[Proba], pary: Sequence[tuple[float, float]],
) -> tuple[int, float, float, list[str | None]] | None:
    najlepszy = None
    for prog, margines in pary:
        decyzje = [decyzja_osoby(p.ranking, prog, margines) for p in proby]
        bledy = sum(d is not None and d != p.osoba for d, p in zip(decyzje, proby, strict=True))
        trafne = sum(d is not None and d == p.osoba for d, p in zip(decyzje, proby, strict=True))
        if bledy == 0 and trafne and (najlepszy is None or trafne > najlepszy[0]):
            najlepszy = (trafne, prog, margines, decyzje)
    return najlepszy


def _progi_rozdzielne(proby: list[Proba], udzial_marginesu: float) -> tuple[float, float] | None:
    """Ustawia progi wewnątrz luk zaobserwowanych w rozdzielnej kalibracji."""
    trafne = []
    bledne = []
    for p in proby:
        a, b = p.ranking[:2]
        cel = trafne if a.osoba == p.osoba else bledne
        cel.append((a.dopasowanie, a.dopasowanie - b.dopasowanie))
    if trafne and bledne:
        min_wynik = min(w for w, _ in trafne)
        min_margines = min(m for _, m in trafne)
        max_blad = max(w for w, _ in bledne)
        max_margines = max(m for _, m in bledne)
        if max_blad < min_wynik and max_margines < min_margines:
            return (max_blad + min_wynik) / 2, max_margines + udzial_marginesu * (min_margines - max_margines)
    return None


def kalibruj(baza: Baza, kontrole: Sequence[Wektor] = ()) -> dict[str, Any] | None:
    """Sprawdza zdjęcia z pominięciem próbki i symuluje osoby spoza bazy."""
    if len(baza) < 2:
        return None
    proby = proby_bazy(baza) + [Proba(None, ranking_osob(w, baza)) for w in kontrole]
    porownywalne = [p for p in proby if len(p.ranking) >= 2]
    nieznane_z_kandydatami = sum(p.osoba is None for p in porownywalne)
    wyniki = {p.ranking[0].dopasowanie for p in porownywalne}
    przewagi = {p.ranking[0].dopasowanie - p.ranking[1].dopasowanie for p in porownywalne}
    progi = {0., *(w for w in wyniki if w >= 0), *(nextafter(w, 2.) for w in wyniki if 0 <= w < 1)}
    marginesy = {0., *przewagi, *(nextafter(w, 3.) for w in przewagi if w < 2)}
    istnieje_kontrola = nieznane_z_kandydatami > 0
    udzial = wczytaj_konfiguracje()["twarze"]["udzial_zapasu_marginesu"]
    rozdzielne = _progi_rozdzielne(porownywalne, udzial) if istnieje_kontrola else None
    pary = ([(p, m) for p in sorted(progi, reverse=True) for m in sorted(marginesy, reverse=True)]
            if istnieje_kontrola else [])
    najlepszy = _porownanie_progow(proby, [rozdzielne, *pary] if rozdzielne else pary)
    prog, margines = (najlepszy[1], najlepszy[2]) if najlepszy else (None, None)
    decyzje = najlepszy[3] if najlepszy else [None] * len(proby)
    macierz = Counter((p.osoba or "nieznana", d or "nieznana") for p, d in zip(proby, decyzje, strict=True))
    return {"T_osoba": prog, "margines": margines, "T_f": _prog_twarzy(baza, kontrole),
            "trafne": najlepszy[0] if najlepszy else 0,
            "proby_znanych": sum(p.osoba is not None for p in proby),
            "proby_nieznanych": sum(p.osoba is None for p in proby),
            "proby_nieznanych_z_dwoma_kandydatami": nieznane_z_kandydatami,
            "proby_kontrolne": len(kontrole),
            "falszywe_dopasowania": 0 if najlepszy else None,
            "macierz": [{"prawda": a, "decyzja": b, "liczba": n} for (a, b), n in sorted(macierz.items())]}
