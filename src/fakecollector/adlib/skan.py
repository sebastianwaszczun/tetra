"""Jeden pełny skan: reguły → API Mety → deduplikacja → filtr → zrzut kreacji → /analyze → tropy."""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, ContextManager

import httpx
from pydantic import ValidationError

from .filters import KontekstFiltra, ocen
from .konfiguracja import katalog_roboczy, wczytaj_konfiguracje
from .magazyn import Magazyn, poczatek_okna, teraz
from .meta import BladMeta, KlientMeta
from .model import FiltrTokenu, Ocena, ReklamaMeta, Zapytanie, bez_tokenu, rodzaj_zdjecia
from .przechwyt import Przechwyt, pakiet, podsumuj, wyslij
from .reguly import MAKS_STRON_W_ZAPYTANIU, fraza_produktu, zapytania_na_skan

log = logging.getLogger(__name__)
log.addFilter(FiltrTokenu())

BLEDY_PRZERYWAJACE = {"brak_tokenu", "token_wygasl", "brak_dostepu"}

Analizator = Callable[[str, list[tuple[str, bytes]]], dict[str, Any]]


def zasiej_tropy(magazyn: Magazyn, katalog: Path) -> list[str]:
    """Strony reklamodawców z plików .info.json Biblioteki Reklam w zgłoszeniach (np. z yt-dlp).
    Ich nowe reklamy to nowe odsłony kampanii, które już ktoś zgłosił."""
    strony = []
    for plik in katalog.rglob("*.info.json"):
        try:
            j = json.loads(plik.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(j, dict) or j.get("extractor_key") != "FacebookAds":
            continue
        strona = str(j.get("playlist_uploader_id") or j.get("uploader_id") or "").strip()
        if strona.isdigit():
            magazyn.dodaj_trop("strona", strona, "dane_nask", "strona reklamy ze zgłoszenia")
            strony.append(strona)
    return strony


class Skaner:
    def __init__(self, cfg: dict[str, Any], magazyn: Magazyn, klient: KlientMeta, analizator: Analizator,
                 przechwyt: Callable[[], ContextManager[Any]], katalog: Path) -> None:
        self.cfg, self.magazyn, self.klient = cfg, magazyn, klient
        self.analizator, self.przechwyt, self.katalog = analizator, przechwyt, katalog
        self._cert: tuple[float, set[str]] = (0.0, set())
        self._waznosc: tuple[float, dict[str, Any]] = (0.0, {})

    @classmethod
    def z_srodowiska(cls) -> Skaner:
        cfg = wczytaj_konfiguracje()
        katalog = katalog_roboczy()
        token = os.environ.get("META_ACCESS_TOKEN")
        tryb = os.environ.get("ADLIB_TRYB", "na_zywo")
        klient = KlientMeta(token, cfg["meta"], tryb, katalog / "nagrania")
        magazyn = Magazyn()
        magazyn.przygotuj()
        dane = Path(os.environ.get("ADLIB_DANE_NASK") or katalog.parent / "nask" / "DANE")
        if dane.is_dir():
            zasiej_tropy(magazyn, dane)
        url = os.environ.get("ADLIB_ANALYZE_URL") or cfg["analiza"]["url"]

        def analizator(tekst: str, pliki: list[tuple[str, bytes]]) -> dict[str, Any]:
            return wyslij(tekst, pliki, url, cfg["analiza"]["limit_czasu_s"])

        zrzuty_token = token if tryb == "na_zywo" else None
        return cls(cfg, magazyn, klient, analizator, lambda: Przechwyt(zrzuty_token, cfg["przechwyt"], katalog),
                   katalog)

    def skanuj(self, wyzwalacz: str = "harmonogram", fraza: str | None = None,
               limit: int | None = None) -> dict[str, Any]:
        with self.magazyn.blokada() as wolne:
            if not wolne:
                return {"status": "zajety", "komunikat": "Inny skan właśnie trwa"}
            return self._skanuj(wyzwalacz, fraza, limit)

    def _skanuj(self, wyzwalacz: str, fraza: str | None, limit: int | None) -> dict[str, Any]:
        f, h = self.cfg["filtr"], self.cfg["harmonogram"]
        skan_id = self.magazyn.nowy_skan(self.klient.tryb, wyzwalacz)
        poczatek = teraz()
        liczby: Counter = Counter()
        bledy: list[str] = []
        self.klient.wywolan, self.klient.budzet_wyczerpany = 0, False

        if not self.klient.dostepny:
            bledy.append("Brak META_ACCESS_TOKEN – ustaw go w .env albo uruchom ADLIB_TRYB=odtwarzanie")
            self.magazyn.zakoncz_skan(skan_id, "brak_tokenu", liczby, bledy)
            return {"skan_id": skan_id, "status": "brak_tokenu", "bledy": bledy}

        self.odswiez_cert()
        data_od = poczatek_okna(self.magazyn.ostatni_udany_skan(), h["pierwsze_okno_dni"], h["zapas_okna_dni"])
        strony_tropow = self.magazyn.strony_tropow()
        zapytania = zapytania_na_skan(self.cfg["reguly"], strony_tropow, self.magazyn.frazy_z_wynikow(
            self.cfg["reguly"]["wygasanie_fraz_dni"], self.cfg["reguly"]["maks_fraz_z_wynikow"]), fraza)

        widziane: dict[str, ReklamaMeta] = {}
        rekordy: dict[str, dict[str, Any]] = {}
        nowe: set[str] = set()
        for z in zapytania:
            try:
                surowe = self.klient.szukaj(z, data_od)
            except BladMeta as exc:
                bledy.append(f"{z.opis}: {exc}")
                if exc.rodzaj in BLEDY_PRZERYWAJACE:
                    break
                continue
            liczby["zapytan"] += 1
            reklamy = []
            for rekord in surowe:
                try:
                    r = ReklamaMeta.z_api(rekord)
                except (KeyError, ValidationError) as exc:
                    bledy.append(f"rekord bez wymaganych pól: {exc.__class__.__name__}")
                    continue
                reklamy.append(r)
                rekordy[r.id], widziane[r.id] = rekord, r
            nowe |= self.magazyn.zapisz_reklamy(reklamy, rekordy, z.opis, skan_id)
            if reklamy and z.zrodlo == "trop":
                self.magazyn.zapisz_trafienie("strona", sorted({r.page_id for r in reklamy if r.page_id}))
            if reklamy and z.zrodlo == "fraza_z_wynikow" and z.fraza:
                self.magazyn.zapisz_trafienie("fraza", [z.fraza])
            if self.klient.budzet_wyczerpany:
                bledy.append("budżet wywołań API wyczerpany, pozostałe zapytania w następnym skanie")
                break
        liczby["reklam"], liczby["nowych"] = len(widziane), len(nowe)

        kontekst = KontekstFiltra(strony_tropow=set(strony_tropow), domeny_cert=self.domeny_cert(),
                                  reklam_strony=Counter(r.page_id for r in widziane.values() if r.page_id),
                                  strony_znane=self.magazyn.znane_strony(poczatek), dzis=poczatek.date())
        oceny: dict[str, Ocena] = {}
        for id_reklamy in nowe:
            oceny[id_reklamy] = o = ocen(widziane[id_reklamy], kontekst, f)
            kandydat = o.z_tropu or o.wynik >= f["prog"]
            self.magazyn.ustaw_ocene(id_reklamy, o, "kandydat" if kandydat else "pominieta")
        wybrane = sorted((i for i, o in oceny.items() if o.z_tropu or o.wynik >= f["prog"]),
                         key=lambda i: (not oceny[i].z_tropu, -oceny[i].wynik))[: limit or f["maks_do_analizy"]]
        liczby["wybranych"] = len(wybrane)

        if wybrane:
            with self.przechwyt() as przechwyt:
                for id_reklamy in wybrane:
                    if self._analizuj(widziane[id_reklamy], rekordy[id_reklamy], oceny[id_reklamy], przechwyt, bledy):
                        liczby["przeanalizowanych"] += 1

        liczby["wywolan_api"] = self.klient.wywolan
        status = "zakonczony" if liczby["zapytan"] else "blad"
        self.magazyn.zakoncz_skan(skan_id, status, liczby, bledy)
        log.info("Skan %s: %s, %s", skan_id, status, dict(liczby))
        return {"skan_id": skan_id, "status": status, "tryb": self.klient.tryb, "okno_od": data_od,
                **liczby, "bledy": bledy}

    def _analizuj(self, r: ReklamaMeta, rekord: dict[str, Any], ocena: Ocena, przechwyt: Any,
                  bledy: list[str]) -> bool:
        self.magazyn.ustaw_status(r.id, "w_analizie")
        try:
            png = przechwyt.zrzut(r)
            tekst, pliki = pakiet(r, rekord, png)
            wynik = podsumuj(self.analizator(tekst, pliki))
            wynik["zrzut_kreacji"] = png is not None
            self.magazyn.ustaw_analize(r.id, wynik["id_zgloszenia"], wynik)
        except Exception as exc:
            komunikat = bez_tokenu(f"{exc.__class__.__name__}: {exc}")[:300]
            self.magazyn.ustaw_status(r.id, "blad", komunikat)
            bledy.append(f"reklama {r.id}: {komunikat}")
            return False
        mocne = [p.sygnal for p in ocena.powody if p.sygnal in self.cfg["filtr"]["mocne_sygnaly"]]
        if wynik["osoby_na_obrazie"]:
            mocne.append("osoba na obrazie: " + ", ".join(wynik["osoby_na_obrazie"]))
        if r.page_id and not ocena.z_tropu and ocena.wynik >= self.cfg["filtr"]["prog_tropu"] and mocne:
            self.magazyn.dodaj_trop("strona", r.page_id, "wynik",
                                    f"{r.page_name}: wynik filtra {ocena.wynik}, {', '.join(mocne)}")
            produkt = fraza_produktu(r, self.cfg["reguly"])
            if produkt:
                self.magazyn.dodaj_trop("fraza", produkt, "wynik", f"tytuł reklamy {r.id}")
        return True

    def ponow_analize(self, limit: int | None = None) -> dict[str, Any]:
        """Ponowna analiza reklam, których wynik nie trafił do kartoteki (tabela materials): przeanalizowanych
        zanim potok zapisywał rekordy, z błędem albo przerwanych w trakcie. Ocena filtra zostaje ta sama;
        zapisany zrzut kreacji jest używany ponownie, więc nie trzeba tokenu do podglądu reklamy."""
        with self.magazyn.blokada() as wolne:
            if not wolne:
                return {"status": "zajety", "komunikat": "Inny skan właśnie trwa"}
            jest_kartoteka = self.magazyn.conn.execute("SELECT to_regclass('materials') IS NOT NULL AS j").fetchone()["j"]
            brak = "TRUE" if not jest_kartoteka else \
                "r.id_zgloszenia IS NULL OR NOT EXISTS (SELECT 1 FROM materials m WHERE m.id = r.id_zgloszenia)"
            wiersze = self.magazyn.conn.execute(
                f"""SELECT r.id, r.rekord, r.wynik, r.powody FROM adlib_reklamy r
                    WHERE r.status IN ('przeanalizowana', 'blad', 'w_analizie') AND ({brak})
                    ORDER BY r.wynik DESC NULLS LAST""").fetchall()
            wiersze = wiersze[:limit] if limit else wiersze
            bledy: list[str] = []
            udane = 0
            if wiersze:
                with self.przechwyt() as przechwyt:
                    for w in wiersze:
                        try:
                            r = ReklamaMeta.z_api(w["rekord"])
                            o = Ocena(wynik=w["wynik"] or 0.0, powody=w["powody"] or [])
                        except (KeyError, ValidationError) as exc:
                            bledy.append(f"reklama {w['id']}: {exc.__class__.__name__}")
                            continue
                        if self._analizuj(r, w["rekord"], o, przechwyt, bledy):
                            udane += 1
        log.info("Ponowna analiza: %s z %s", udane, len(wiersze))
        return {"status": "zakonczony", "do_ponowienia": len(wiersze), "przeanalizowanych": udane, "bledy": bledy}

    def sprawdz_zdjete(self) -> dict[str, Any]:
        """Czy Meta zdjęła przeanalizowane reklamy. Pojedynczej reklamy nie da się odczytać po id,
        więc pytamy o strony (do 10 na wywołanie) i szukamy naszych reklam w wynikach."""
        grupy = self.magazyn.do_sprawdzenia(self.cfg["harmonogram"]["sprawdzanie_dni"])
        szukane = {i for ids in grupy.values() for i in ids}
        strony = sorted(grupy)
        zdjete: Counter = Counter()
        bledy: list[str] = []
        with self.magazyn.blokada() as wolne:
            if not wolne:
                return {"status": "zajety"}
            for i in range(0, len(strony), MAKS_STRON_W_ZAPYTANIU):
                z = Zapytanie(rodzaj="strony", strony=strony[i:i + MAKS_STRON_W_ZAPYTANIU], zrodlo="trop")
                try:
                    surowe = self.klient.szukaj(z, aktywne=False, stron=2)
                except BladMeta as exc:
                    bledy.append(str(exc))
                    if exc.rodzaj in BLEDY_PRZERYWAJACE:
                        break
                    continue
                for rekord in surowe:
                    if str(rekord.get("id")) not in szukane:
                        continue
                    rodzaj = rodzaj_zdjecia(ReklamaMeta.z_api(rekord), self.cfg["zdjecie"])
                    if rodzaj:
                        self.magazyn.oznacz_zdjeta(str(rekord["id"]), rodzaj)
                        zdjete[rodzaj] += 1
        return {"status": "zakonczony", "sprawdzonych": len(szukane), "zdjete": dict(zdjete), "bledy": bledy}

    def odswiez_cert(self, wymus: bool = False) -> int:
        """Lista Ostrzeżeń CERT Polska w data/adlib/cert_domeny.txt, odświeżana raz na dobę."""
        plik = self.katalog / "cert_domeny.txt"
        wiek_h = (time.time() - plik.stat().st_mtime) / 3600 if plik.exists() else None
        if wymus or wiek_h is None or wiek_h >= self.cfg["cert"]["odswiezanie_godzin"]:
            try:
                r = httpx.get(self.cfg["cert"]["url"], timeout=60)
                r.raise_for_status()
                plik.write_text(r.text, encoding="utf-8")
            except httpx.HTTPError as exc:
                log.warning("Lista CERT nie odświeżona: %s", exc)
        return len(self.domeny_cert())

    def domeny_cert(self) -> set[str]:
        plik = self.katalog / "cert_domeny.txt"
        if not plik.exists():
            return set()
        mtime = plik.stat().st_mtime
        if self._cert[0] != mtime:
            domeny = {l.strip().lower().removeprefix("www.") for l in plik.read_text(encoding="utf-8").splitlines()}
            self._cert = (mtime, {d for d in domeny if d and not d.startswith("#")})
        return self._cert[1]

    def status(self) -> dict[str, Any]:
        if time.time() - self._waznosc[0] > 900:
            self._waznosc = (time.time(), self.klient.waznosc_tokenu() if self.klient.tryb == "na_zywo" else {})
        return {"tryb": self.klient.tryb, **self._waznosc[1], "domen_cert": len(self.domeny_cert()),
                **self.magazyn.status()}
