"""Klient Graph API dla Biblioteki Reklam Meta (ads_archive).

Każda odpowiedź jest zapisywana bez tokenu w data/adlib/nagrania/. W trybie odtwarzania klient czyta
te nagrania zamiast pytać API, więc skan działa bez tokenu i bez sieci (testy, demo).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from .model import FiltrTokenu, Zapytanie, bez_tokenu

log = logging.getLogger(__name__)
log.addFilter(FiltrTokenu())
# httpx loguje pełne adresy zapytań, a token jest parametrem adresu.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

POLA = ",".join((
    "id", "ad_creation_time", "ad_delivery_start_time", "ad_delivery_stop_time",
    "ad_creative_bodies", "ad_creative_link_captions", "ad_creative_link_titles", "ad_creative_link_descriptions",
    "ad_snapshot_url", "page_id", "page_name", "beneficiary_payers", "languages", "publisher_platforms",
    "eu_total_reach", "target_ages", "target_gender", "target_locations",
))
KODY_LIMITU = {4, 17, 32, 613} | set(range(80000, 80015))
BRAK_DOSTEPU = {2332002, 2332004}


class BladMeta(Exception):
    """rodzaj: brak_tokenu, token_wygasl, brak_dostepu, limit, zle_zapytanie, siec, inny."""

    def __init__(self, rodzaj: str, komunikat: str, kod: int | None = None, podkod: int | None = None,
                 czekaj_s: float = 0.0) -> None:
        super().__init__(f"{rodzaj}: {bez_tokenu(komunikat)}")
        self.rodzaj, self.kod, self.podkod, self.czekaj_s = rodzaj, kod, podkod, czekaj_s


class KlientMeta:
    def __init__(self, token: str | None, cfg: dict[str, Any], tryb: str = "na_zywo",
                 katalog_nagran: Path | None = None, transport: httpx.BaseTransport | None = None,
                 czekaj: Callable[[float], None] = time.sleep) -> None:
        if tryb not in {"na_zywo", "odtwarzanie"}:
            raise ValueError(f"Nieznany tryb klienta: {tryb}")
        self.token = (token or "").strip() or None
        self.cfg = cfg
        self.tryb = tryb
        self.katalog = katalog_nagran
        self.czekaj = czekaj
        self.http = httpx.Client(base_url=f"https://graph.facebook.com/{cfg['wersja_api']}",
                                 timeout=cfg["limit_czasu_s"], transport=transport)
        self.wywolan = 0
        self.zuzycie = 0.0
        self._odzyskanie_min = 0.0
        self.budzet_wyczerpany = False

    @property
    def dostepny(self) -> bool:
        return self.tryb == "odtwarzanie" or self.token is not None

    def waznosc_tokenu(self) -> dict[str, Any]:
        """Czy token działa i do kiedy (debug_token). Sam token nie wychodzi poza klienta."""
        if not self.token:
            return {"token": "brak"}
        try:
            r = self.http.get("/debug_token", params={"input_token": self.token, "access_token": self.token})
            dane = r.json().get("data") or {}
        except (httpx.HTTPError, ValueError) as exc:
            return {"token": "nie_sprawdzono", "blad": bez_tokenu(str(exc))[:200]}
        wygasa = dane.get("expires_at")
        return {"token": "wazny" if dane.get("is_valid") else "niewazny",
                "wygasa": time.strftime("%Y-%m-%d %H:%M", time.localtime(wygasa)) if wygasa else None}

    def szukaj(self, zapytanie: Zapytanie, data_od: str | None = None, aktywne: bool = True,
               stron: int | None = None) -> list[dict[str, Any]]:
        """Reklamy pasujące do zapytania, od najnowszych. Rekordy zawierają ad_snapshot_url z tokenem."""
        parametry = self._parametry(zapytanie, aktywne)
        klucz = self._klucz(parametry)
        if self.tryb == "odtwarzanie":
            return self._odtworz(klucz)
        if not self.token:
            raise BladMeta("brak_tokenu", "Brak META_ACCESS_TOKEN w środowisku")
        if data_od:
            parametry["ad_delivery_date_min"] = data_od
        wynik: list[dict[str, Any]] = []
        after = None
        for nr in range(stron or self.cfg["stron_na_zapytanie"]):
            if self.wywolan >= self.cfg["budzet_wywolan"]:
                self.budzet_wyczerpany = True
                log.warning("Budżet %s wywołań API wyczerpany", self.cfg["budzet_wywolan"])
                break
            dane = self._get(dict(parametry, after=after) if after else parametry)
            self._nagraj(klucz, nr, parametry, dane)
            wynik += dane.get("data") or []
            stronicowanie = dane.get("paging") or {}
            after = (stronicowanie.get("cursors") or {}).get("after") if stronicowanie.get("next") else None
            if not after:
                break
        return wynik

    def _parametry(self, zapytanie: Zapytanie, aktywne: bool) -> dict[str, Any]:
        p: dict[str, Any] = {
            "ad_reached_countries": json.dumps([self.cfg["kraj"]]),
            "ad_type": "ALL",
            "ad_active_status": "ACTIVE" if aktywne else "ALL",
            "fields": POLA,
            "limit": self.cfg["reklam_na_strone"],
        }
        if zapytanie.rodzaj == "fraza":
            p["search_terms"] = zapytanie.fraza
        else:
            p["search_page_ids"] = json.dumps(zapytanie.strony[:10])
        return p

    @staticmethod
    def _klucz(parametry: dict[str, Any]) -> str:
        """Nagranie rozpoznajemy po treści zapytania, bez dat i kursora, żeby odtwarzanie działało każdego dnia."""
        istotne = {k: parametry.get(k) for k in ("search_terms", "search_page_ids", "ad_active_status",
                                                 "ad_reached_countries")}
        return hashlib.sha1(json.dumps(istotne, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

    def _get(self, parametry: dict[str, Any]) -> dict[str, Any]:
        for proba in range(self.cfg["proby"]):
            self.wywolan += 1
            try:
                r = self.http.get("/ads_archive", params=dict(parametry, access_token=self.token))
            except httpx.HTTPError as exc:
                raise BladMeta("siec", f"{exc.__class__.__name__}: {exc}") from None
            self._odczytaj_zuzycie(r.headers)
            if r.status_code == 200:
                if self.zuzycie >= self.cfg["prog_zuzycia"]:
                    log.info("Zużycie limitu API %.0f%%, pauza %s s", self.zuzycie, self.cfg["pauza_przy_zuzyciu_s"])
                    self.czekaj(self.cfg["pauza_przy_zuzyciu_s"])
                return r.json()
            blad = self._blad(r)
            if blad.rodzaj == "limit" and proba + 1 < self.cfg["proby"] \
                    and blad.czekaj_s <= self.cfg["maks_czekania_s"]:
                log.info("Limit API (kod %s), czekam %.0f s", blad.kod, blad.czekaj_s)
                self.czekaj(blad.czekaj_s)
                continue
            raise blad
        raise BladMeta("limit", "Limit API nie minął po kolejnych próbach")

    def _odczytaj_zuzycie(self, naglowki: httpx.Headers) -> None:
        self.zuzycie, self._odzyskanie_min = 0.0, 0.0
        for nazwa in ("x-business-use-case-usage", "x-app-usage"):
            surowe = naglowki.get(nazwa)
            if not surowe:
                continue
            try:
                dane = json.loads(surowe)
            except json.JSONDecodeError:
                continue
            wpisy = [w for lista in dane.values() for w in lista] if nazwa.startswith("x-business") else [dane]
            for w in wpisy:
                self.zuzycie = max(self.zuzycie, *(float(w.get(k) or 0) for k in
                                                   ("call_count", "total_cputime", "total_time")))
                self._odzyskanie_min = max(self._odzyskanie_min, float(w.get("estimated_time_to_regain_access") or 0))

    def _blad(self, r: httpx.Response) -> BladMeta:
        try:
            e = r.json().get("error") or {}
        except ValueError:
            e = {}
        kod, podkod, tekst = e.get("code"), e.get("error_subcode"), e.get("message") or r.text[:200]
        if kod == 190:
            return BladMeta("token_wygasl", tekst, kod, podkod)
        if kod == 10 and podkod in BRAK_DOSTEPU:
            return BladMeta("brak_dostepu", f"{tekst} – kroki na facebook.com/ads/library/api", kod, podkod)
        if kod in KODY_LIMITU:
            return BladMeta("limit", tekst, kod, podkod, czekaj_s=max(60.0, self._odzyskanie_min * 60))
        if kod == 100:
            return BladMeta("zle_zapytanie", tekst, kod, podkod)
        return BladMeta("inny", f"HTTP {r.status_code}: {tekst}", kod, podkod)

    # ------------------------------------------------------------------ nagrania
    def _nagraj(self, klucz: str, nr: int, parametry: dict[str, Any], dane: dict[str, Any]) -> None:
        if not self.katalog:
            return
        self.katalog.mkdir(parents=True, exist_ok=True)
        zapis = {"klucz": klucz, "strona": nr, "czas": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "parametry": parametry, "odpowiedz": dane}
        tekst = bez_tokenu(json.dumps(zapis, ensure_ascii=False))
        if nr == 0:
            # Dalsze strony starszego nagrania tego zapytania nie mogą się mieszać z nowym.
            for stary in self.katalog.glob(f"{klucz}_*.json"):
                stary.unlink()
        (self.katalog / f"{klucz}_{nr:02d}.json").write_text(tekst, encoding="utf-8")

    def _odtworz(self, klucz: str) -> list[dict[str, Any]]:
        if not self.katalog:
            return []
        wynik = []
        for plik in sorted(self.katalog.glob(f"{klucz}_*.json")):
            wynik += json.loads(plik.read_text(encoding="utf-8"))["odpowiedz"].get("data") or []
        return wynik
