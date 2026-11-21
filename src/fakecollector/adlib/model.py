"""Reklama z Biblioteki Reklam Meta, zapytanie do API i ocena filtra."""
from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TOKEN_RE = re.compile(r"(access_token=)[^&\s\"'<>\\]+")
DOMENA_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


def bez_tokenu(tekst: str) -> str:
    """Usuwa token z adresów i komunikatów, zanim trafią do pliku, bazy albo logu."""
    return TOKEN_RE.sub(r"\1<usunięty>", tekst)


class FiltrTokenu(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = bez_tokenu(record.getMessage())
        record.args = ()
        return True


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Platnik(Model):
    platnik: str | None = None
    beneficjent: str | None = None


class ReklamaMeta(Model):
    id: str
    page_id: str | None = None
    page_name: str | None = None
    platnicy: list[Platnik] = Field(default_factory=list)
    tresci: list[str] = Field(default_factory=list)
    tytuly: list[str] = Field(default_factory=list)
    opisy: list[str] = Field(default_factory=list)
    podpisy: list[str] = Field(default_factory=list)
    utworzona: str | None = None
    start: str | None = None
    koniec: str | None = None
    zasieg_ue: int | None = None
    jezyki: list[str] = Field(default_factory=list)
    platformy: list[str] = Field(default_factory=list)
    wiek: list[str] = Field(default_factory=list)
    plec: str | None = None
    lokalizacje: list[str] = Field(default_factory=list)

    @classmethod
    def z_api(cls, r: dict[str, Any]) -> ReklamaMeta:
        return cls(
            id=str(r["id"]),
            page_id=str(r["page_id"]) if r.get("page_id") else None,
            page_name=r.get("page_name"),
            platnicy=[Platnik(platnik=p.get("payer"), beneficjent=p.get("beneficiary"))
                      for p in r.get("beneficiary_payers") or []],
            tresci=list(r.get("ad_creative_bodies") or []),
            tytuly=list(r.get("ad_creative_link_titles") or []),
            opisy=list(r.get("ad_creative_link_descriptions") or []),
            podpisy=list(r.get("ad_creative_link_captions") or []),
            utworzona=r.get("ad_creation_time"),
            start=r.get("ad_delivery_start_time"),
            koniec=r.get("ad_delivery_stop_time"),
            zasieg_ue=r.get("eu_total_reach"),
            jezyki=list(r.get("languages") or []),
            platformy=list(r.get("publisher_platforms") or []),
            wiek=[str(w) for w in r.get("target_ages") or []],
            plec=r.get("target_gender"),
            lokalizacje=[str(l.get("name")) for l in r.get("target_locations") or [] if l.get("name")],
        )

    @property
    def tekst(self) -> str:
        return "\n".join(dict.fromkeys(t.strip() for t in self.tresci + self.tytuly + self.opisy if t.strip()))

    @property
    def domena(self) -> str | None:
        """Domena z wyświetlanego podpisu linku. API nie podaje adresu strony docelowej."""
        for podpis in self.podpisy:
            kandydat = re.sub(r"^[a-z]+://", "", podpis.strip().lower()).split("/")[0].removeprefix("www.")
            if DOMENA_RE.match(kandydat):
                return kandydat
        return None

    @property
    def adres_biblioteki(self) -> str:
        return f"https://www.facebook.com/ads/library/?id={self.id}"


def rodzaj_zdjecia(reklama: ReklamaMeta, wzorce: dict[str, list[str]]) -> str | None:
    """Meta po zdjęciu reklamy podmienia jej teksty na komunikat. Zwraca rodzaj zdjęcia albo None."""
    tekst = reklama.tekst.lower()
    for rodzaj, frazy in wzorce.items():
        if any(f.lower() in tekst for f in frazy):
            return rodzaj
    return None


class Zapytanie(Model):
    rodzaj: Literal["fraza", "strony"]
    fraza: str | None = None
    strony: list[str] = Field(default_factory=list)
    zrodlo: Literal["konfiguracja", "trop", "fraza_z_wynikow", "reczne"]

    @property
    def opis(self) -> str:
        return f"fraza:{self.fraza}" if self.rodzaj == "fraza" else "strony:" + ",".join(self.strony)


class Powod(Model):
    sygnal: str
    waga: float
    opis: str


class Ocena(Model):
    wynik: float
    powody: list[Powod] = Field(default_factory=list)
    z_tropu: bool = False
