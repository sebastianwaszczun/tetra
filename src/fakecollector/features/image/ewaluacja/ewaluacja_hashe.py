"""Lokalna ewaluacja hashy i generowanie listy par do weryfikacji."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
from pathlib import Path
from statistics import mean, median
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from .. import analizuj_podstawy
from ..hashe import odleglosci, pasujaca_para
from .kalibracja_hashy import Pomiary, dobierz_progi
from ..konfiguracja import wczytaj_konfiguracje
from ..model import CechyObrazu, Prostokat
from ..przygotowanie import ROZSZERZENIA, czy_svg, czytaj_bajty, otworz_raster
from ..warianty import generuj_warianty
from ..wzorzec import ParaObrazow, otworz_obraz, plik_danych, wczytaj_kreacje, wczytaj_pary

@dataclass
class Obserwacja:
    nazwa: str
    skrot_pliku: str
    cechy: CechyObrazu


def _analizuj_warianty(obraz: Image.Image, cechy: CechyObrazu, pozytywy: dict[str, list[Pomiary]]) -> None:
    for wariant in generuj_warianty(ImageOps.exif_transpose(obraz)):
        if wariant.oczekiwanie == "obiecane":
            _, po_zmianie = analizuj_podstawy(wariant.obraz)
            pozytywy[wariant.nazwa].append(odleglosci(cechy.hashe, po_zmianie.hashe))


def _zasoby(dane: Path, pozytywy: dict[str, list[Pomiary]]) -> tuple[list[Obserwacja], list[str]]:
    katalog = dane / "nask/DANE"
    konfiguracja = wczytaj_konfiguracje()
    limity = konfiguracja["wczytywanie"]
    obrazy = []
    bledy = []
    zbadane = set()
    for plik in sorted(katalog.rglob("*")):
        wzgledna = plik.relative_to(katalog)
        if (not any(p.endswith("_files") for p in wzgledna.parts[:-1])
                or plik.suffix.lower() not in ROZSZERZENIA or not plik.is_file()):
            continue
        nazwa = plik.relative_to(dane).as_posix()
        bezpieczny = plik_danych(dane, nazwa)
        try:
            tresc = czytaj_bajty(bezpieczny, limity["maks_bajtow"])
        except (OSError, ValueError):
            bledy.append(nazwa)
            continue
        _, cechy = analizuj_podstawy(tresc)
        skrot = sha256(tresc).hexdigest()
        if cechy.sha256 or czy_svg(tresc):
            obrazy.append(Obserwacja(nazwa, skrot, cechy))
        else:
            bledy.append(nazwa)
        if cechy.hashe.phash and skrot not in zbadane:
            zbadane.add(skrot)
            with otworz_raster(tresc, limity["maks_pikseli"]) as obraz:
                _analizuj_warianty(obraz, cechy, pozytywy)
    return obrazy, bledy


def _klucz(a: str, b: str) -> tuple[str, str]:
    return min(a, b), max(a, b)


def _porownaj_zasoby(obrazy: list[Obserwacja], pary: dict[tuple[str, str], ParaObrazow],
                      progi: dict[str, int]) -> tuple[list[Pomiary], Counter[str]]:
    negatywy = {}
    identyczne: dict[str, set[str]] = defaultdict(set)
    for a, b in combinations(obrazy, 2):
        klucz = _klucz(a.nazwa, b.nazwa)
        istniejaca = pary.get(klucz)
        if a.skrot_pliku == b.skrot_pliku:
            if istniejaca and istniejaca.relacja == "rozne":
                raise ValueError("Para oznaczona jako różna ma identyczne SHA-256")
            pary.setdefault(klucz, ParaObrazow(a=klucz[0], b=klucz[1], relacja="identyczne", zrodlo="sha256"))
            grupa_a, grupa_b = (Path(nazwa).parts[3] for nazwa in klucz)
            identyczne[f"{grupa_a}↔{grupa_b}"].add(a.skrot_pliku)
            continue
        if istniejaca and istniejaca.relacja == "identyczne":
            continue
        pomiary = odleglosci(a.cechy.hashe, b.cechy.hashe)
        if not pomiary:
            continue
        negatywy[_klucz(a.skrot_pliku, b.skrot_pliku)] = pomiary
        trafienie = pasujaca_para(pomiary, progi["T_p_sporne"], progi["T_d_sporne"])
        if trafienie and istniejaca is None:
            pary[klucz] = ParaObrazow(
                a=klucz[0], b=klucz[1], relacja="sporne", zrodlo="phash+dhash",
                uwagi=f"Do sprawdzenia. {trafienie.wariant}, pHash {trafienie.phash}, dHash {trafienie.dhash}.",
            )
    return list(negatywy.values()), Counter({para: len(skroty) for para, skroty in identyczne.items()})


def _zamaskuj(obraz: Image.Image, prostokat: Prostokat) -> Image.Image:
    wynik = obraz.copy()
    x, y, w, h = prostokat
    ImageDraw.Draw(wynik).rectangle((x, y, x + w - 1, y + h - 1), fill="gray")
    return wynik


def _kreacje(dane: Path, pozytywy: dict[str, list[Pomiary]]) -> tuple[int, list[Pomiary], list[tuple[Pomiary, Pomiary]]]:
    if not (dane / "wzorzec/kreacje.json").is_file():
        return 0, [], []
    zbior = wczytaj_kreacje(dane)
    cechy = {}
    maskowane = {}
    for kreacja in zbior.kreacje:
        if kreacja.status != "zatwierdzone":
            continue
        x, y, w, h = kreacja.prostokat_kreacji
        obraz = otworz_obraz(plik_danych(dane, kreacja.plik)).crop((x, y, x + w, y + h))
        _, rekord = analizuj_podstawy(obraz)
        cechy[kreacja.id] = rekord
        if rekord.hashe.phash:
            _analizuj_warianty(obraz, rekord, pozytywy)
        przycisk = kreacja.oczekiwane.prostokat_odtwarzania
        if przycisk:
            px, py, pw, ph = przycisk
            _, maskowane[kreacja.id] = analizuj_podstawy(_zamaskuj(obraz, (px - x, py - y, pw, ph)))
    negatywy = []
    nakladki = []
    identyczne = {_klucz(p.a, p.b) for p in zbior.pary if p.relacja == "duplikat"}
    for a, b in combinations(cechy, 2):
        if _klucz(a, b) in identyczne:
            continue
        bez_maski = odleglosci(cechy[a].hashe, cechy[b].hashe)
        if bez_maski:
            negatywy.append(bez_maski)
        if a in maskowane or b in maskowane:
            z_maska = odleglosci(maskowane.get(a, cechy[a]).hashe, maskowane.get(b, cechy[b]).hashe)
            nakladki.append((bez_maski, z_maska))
    return len(cechy), negatywy, nakladki


def ewaluuj_hashe(dane: Path, zapisz_pary: bool = False) -> dict[str, Any]:
    """Mierzy odporność na warianty, sprawdza pary i wyznacza progi."""
    progi = wczytaj_konfiguracje()["hashe"]
    pozytywy: dict[str, list[Pomiary]] = defaultdict(list)
    plik_par = dane / "wzorzec/pary_obrazow.json"
    pary = {_klucz(p.a, p.b): p for p in wczytaj_pary(dane)} if plik_par.is_file() else {}
    obrazy, bledy = _zasoby(dane, pozytywy)
    negatywy, identyczne = _porownaj_zasoby(obrazy, pary, progi)
    kreacje, trudne, nakladki = _kreacje(dane, pozytywy)
    negatywy.extend(trudne)
    wszystkie_warianty = [para for wariant in pozytywy.values() for para in wariant]
    kalibracja = dobierz_progi(wszystkie_warianty, negatywy, progi["zapas_bit"]) if (
        wszystkie_warianty and negatywy
    ) else None
    T_p, T_d = kalibracja if kalibracja is not None else (progi["T_p"], progi["T_d"])

    def trafienia(pary_pomiarow: list[Pomiary], zapas: int = 0) -> int:
        return sum(pasujaca_para(para, T_p + zapas, T_d + zapas) is not None for para in pary_pomiarow)

    if zapisz_pary and obrazy:
        plik_par.parent.mkdir(parents=True, exist_ok=True)
        zapis = "[" + ",\n".join(pary[k].model_dump_json() for k in sorted(pary)) + "]\n"
        tymczasowy = plik_par.with_suffix(".tmp")
        tymczasowy.write_text(zapis, encoding="utf-8")
        tymczasowy.replace(plik_par)

    czasy = [sum(o.cechy.czasy_ms.values()) for o in obrazy if o.cechy.sha256]
    return {
        "T_p": T_p, "T_d": T_d, "skalibrowano": kalibracja is not None,
        "zapas_bit": progi["zapas_bit"], "zasoby": len(obrazy), "kreacje_zatwierdzone": kreacje,
        "nierozpoznane_pliki": bledy, "negatywy": len(negatywy), "trudne_pary_kreacji": len(trudne),
        "warianty": {k: {"trafione": trafienia(v), "wszystkie": len(v), "bez_hashy": sum(not p for p in v)}
                     for k, v in pozytywy.items()},
        "identyczne": dict(sorted(identyczne.items())), "liczba_identycznych": sum(p.relacja == "identyczne" for p in pary.values()),
        "sporne": sum(p.relacja == "sporne" for p in pary.values()),
        "trafione_warianty": trafienia(wszystkie_warianty), "liczba_wariantow": len(wszystkie_warianty),
        "falszywe": trafienia(negatywy), "falszywe_z_zapasem": trafienia(negatywy, progi["zapas_bit"]),
        "czas_ms": {"srednia": round(mean(czasy), 2), "mediana": round(median(czasy), 2)} if czasy else {},
        "nakladki": {"pary": len(nakladki), "falszywe_bez_maski": trafienia([a for a, _ in nakladki]),
                     "falszywe_z_maska": trafienia([b for _, b in nakladki])} if nakladki else None,
    }
