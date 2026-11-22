"""Zapytania na jeden skan: tropy kampanii, frazy z konfiguracji i nazwy produktów z wcześniejszych wyników."""
from __future__ import annotations

import re
from typing import Any

from .model import ReklamaMeta, Zapytanie

MAKS_STRON_W_ZAPYTANIU = 10


def _norm(fraza: str) -> str:
    return " ".join(fraza.lower().split())


def zapytania_na_skan(cfg: dict[str, Any], strony_tropow: list[str], frazy_z_wynikow: list[str],
                      fraza: str | None = None) -> list[Zapytanie]:
    """Najpierw strony z tropów (nowe odsłony znanych kampanii), potem frazy. Fraza ręczna zastępuje resztę."""
    if fraza and fraza.strip():
        return [Zapytanie(rodzaj="fraza", fraza=fraza.strip(), zrodlo="reczne")]
    wynik = [Zapytanie(rodzaj="strony", strony=strony_tropow[i:i + MAKS_STRON_W_ZAPYTANIU], zrodlo="trop")
             for i in range(0, len(strony_tropow), MAKS_STRON_W_ZAPYTANIU)]
    uzyte: set[str] = set()
    for zrodlo, frazy in (("konfiguracja", cfg["frazy"]), ("fraza_z_wynikow", frazy_z_wynikow)):
        for f in frazy:
            if f.strip() and _norm(f) not in uzyte:
                uzyte.add(_norm(f))
                wynik.append(Zapytanie(rodzaj="fraza", fraza=f.strip(), zrodlo=zrodlo))
    return wynik


def fraza_produktu(reklama: ReklamaMeta, cfg: dict[str, Any]) -> str | None:
    """Tytuł linku podejrzanej reklamy często jest nazwą produktu. Ogólne wezwania do działania odpadają."""
    from fakecollector.features.text import stopwords

    for tytul in reklama.tytuly:
        czysty = " ".join(re.sub(r"[^\w\s-]", " ", tytul, flags=re.UNICODE).split())
        slowa = czysty.split()
        if not 1 <= len(slowa) <= 4 or not 3 <= len(czysty) <= 40 or not re.search(r"[^\W\d_]", czysty):
            continue
        if _norm(czysty) in {_norm(o) for o in cfg["ogolne_tytuly"]}:
            continue
        if all(s.lower() in stopwords() for s in slowa):
            continue
        return czysty
    return None
