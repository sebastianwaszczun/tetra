"""Tani wynik podejrzliwości reklamy z metadanych i tekstu, zanim trafi do ciężkiej analizy.

Wynik łączy sygnały jak niezależne przesłanki: 1 - iloczyn(1 - waga). Każdy sygnał zostawia powód,
który analityk widzi przy reklamie.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .model import Ocena, Powod, ReklamaMeta


@dataclass
class KontekstFiltra:
    strony_tropow: set[str] = field(default_factory=set)
    domeny_cert: set[str] = field(default_factory=set)
    reklam_strony: Counter = field(default_factory=Counter)
    strony_znane: set[str] = field(default_factory=set)
    dzis: date = field(default_factory=date.today)


def _na_liscie(domena: str | None, lista: set[str]) -> str | None:
    if not domena:
        return None
    czesci = domena.split(".")
    for i in range(len(czesci) - 1):
        kandydat = ".".join(czesci[i:])
        if kandydat in lista:
            return kandydat
    return None


def _spoza_lacinki(tekst: str) -> bool:
    # Polskie litery mieszczą się w Latin Extended-A, więc wszystko powyżej U+024F to inne pismo.
    return any(z.isalpha() and ord(z) > 0x24F for z in tekst)


def ocen(r: ReklamaMeta, k: KontekstFiltra, cfg: dict[str, Any]) -> Ocena:
    if r.page_id and r.page_id in k.strony_tropow:
        return Ocena(wynik=1.0, z_tropu=True,
                     powody=[Powod(sygnal="trop_strony", waga=1.0, opis="strona z tropów wykrytej kampanii")])

    from fakecollector.features import text as t

    wagi = cfg["wagi"]
    powody: list[Powod] = []

    def dodaj(sygnal: str, opis: str) -> None:
        powody.append(Powod(sygnal=sygnal, waga=wagi[sygnal], opis=opis))

    trafienie = _na_liscie(r.domena, k.domeny_cert)
    if trafienie:
        dodaj("lista_cert", f"domena {trafienie} na Liście Ostrzeżeń CERT Polska")

    tekst = r.tekst
    scamowa = False
    if tekst:
        narracja = t.classify_narrative(tekst, None)
        scamowa = narracja["category"] in cfg["narracje_scamowe"]
        if narracja["category"] != "other":
            dodaj("narracja", f"narracja: {narracja['label']}")
        presja = t.persuasion_markers(tekst)
        if presja.get("profit"):
            dodaj("presja_zysk", "obietnica zysku")
        inne = [g for g in ("urgency", "secrecy", "sensational", "authority") if presja.get(g)]
        if inne:
            dodaj("presja_inne", "presja: " + ", ".join(inne))
        encje = t.find_entities(tekst)
        osoby = ", ".join(encje["persons"])
        if osoby:
            dodaj("osoba", f"osoba z bazy w tekście: {osoby}")
            if scamowa:
                dodaj("osoba_i_narracja", f"{osoby} w narracji: {narracja['label']}")
        if encje["brands"]:
            dodaj("marka", "marka lub instytucja w tekście: " + ", ".join(encje["brands"]))

    nazwa = (r.page_name or "").lower()
    if scamowa and nazwa:
        media = t.find_entities(nazwa)["brands"]
        slowo = next((s for s in cfg["slowa_mediow"] if s in nazwa), None)
        if media or slowo:
            dodaj("strona_jak_media", f"nazwa strony jak serwis informacyjny ({', '.join(media) or slowo}) "
                                      "przy narracji o zysku albo skandalu")

    ile = k.reklam_strony.get(r.page_id, 0)
    if r.page_id and ile >= cfg["reklam_strony"]:
        dodaj("spalanie_strony", f"{ile} reklam tej strony w jednym skanie")

    # W pierwszym skanie każda strona jest nowa, więc sygnał ma sens dopiero przy znanej historii.
    if r.page_id and k.strony_znane and r.page_id not in k.strony_znane and r.start:
        try:
            wiek_dni = (k.dzis - date.fromisoformat(r.start[:10])).days
        except ValueError:
            wiek_dni = None
        if wiek_dni is not None and wiek_dni <= cfg["dni_swiezosci"]:
            dodaj("nowa_strona", f"strona wcześniej niewidziana, reklama sprzed {wiek_dni} dni")

    nazwy = [n for p in r.platnicy for n in (p.platnik, p.beneficjent) if n]
    if not r.platnicy:
        dodaj("platnik", "brak informacji o płatniku")
    elif any(_spoza_lacinki(n) for n in nazwy):
        dodaj("platnik", "płatnik albo beneficjent zapisany innym pismem niż łacińskie")

    wynik = 1.0
    for p in powody:
        wynik *= 1.0 - p.waga
    return Ocena(wynik=round(1.0 - wynik, 4), powody=powody)
