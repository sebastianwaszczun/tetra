"""Pamięć skanera w Postgresie: reklamy, tropy kampanii i dziennik skanów."""
from __future__ import annotations

import os
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .model import Ocena, ReklamaMeta

BLOKADA = 7_413_001

SCHEMAT = """
CREATE TABLE IF NOT EXISTS adlib_reklamy (
    id                TEXT PRIMARY KEY,
    page_id           TEXT,
    page_name         TEXT,
    domena            TEXT,
    rekord            JSONB NOT NULL,
    zapytania         TEXT[] NOT NULL DEFAULT '{}',
    pierwsze_wykrycie TIMESTAMPTZ NOT NULL DEFAULT now(),
    ostatnie_wykrycie TIMESTAMPTZ NOT NULL DEFAULT now(),
    skan_id           BIGINT,
    wynik             REAL,
    powody            JSONB,
    status            TEXT NOT NULL DEFAULT 'nowa',
    zdjecie           TEXT,
    id_zgloszenia     TEXT,
    analiza           JSONB,
    blad              TEXT
);
CREATE INDEX IF NOT EXISTS adlib_reklamy_page ON adlib_reklamy (page_id);
CREATE INDEX IF NOT EXISTS adlib_reklamy_status ON adlib_reklamy (status);

CREATE TABLE IF NOT EXISTS adlib_tropy (
    rodzaj             TEXT NOT NULL,
    wartosc            TEXT NOT NULL,
    zrodlo             TEXT NOT NULL,
    opis               TEXT,
    dodano             TIMESTAMPTZ NOT NULL DEFAULT now(),
    ostatnie_trafienie TIMESTAMPTZ,
    trafien            INT NOT NULL DEFAULT 0,
    PRIMARY KEY (rodzaj, wartosc)
);

CREATE TABLE IF NOT EXISTS adlib_skany (
    id                BIGSERIAL PRIMARY KEY,
    start             TIMESTAMPTZ NOT NULL DEFAULT now(),
    koniec            TIMESTAMPTZ,
    status            TEXT NOT NULL DEFAULT 'trwa',
    tryb              TEXT,
    wyzwalacz         TEXT,
    zapytan           INT NOT NULL DEFAULT 0,
    reklam            INT NOT NULL DEFAULT 0,
    nowych            INT NOT NULL DEFAULT 0,
    wybranych         INT NOT NULL DEFAULT 0,
    przeanalizowanych INT NOT NULL DEFAULT 0,
    wywolan_api       INT NOT NULL DEFAULT 0,
    bledy             JSONB NOT NULL DEFAULT '[]'
);
"""


class Magazyn:
    def __init__(self, dsn: str | None = None, schemat: str = "public") -> None:
        self.dsn = dsn or os.environ["DATABASE_URL"]
        self.schemat = schemat
        self.conn = self._polacz()

    def _polacz(self) -> psycopg.Connection:
        conn = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schemat}"')
        conn.execute(f'SET search_path TO "{self.schemat}"')
        return conn

    def przygotuj(self) -> None:
        self.conn.execute(SCHEMAT)

    def zamknij(self) -> None:
        self.conn.close()

    @contextmanager
    def blokada(self) -> Iterator[bool]:
        """Osobne połączenie trzyma blokadę przez cały skan. Zwraca False, gdy inny skan już trwa."""
        conn = self._polacz()
        ok = False
        try:
            ok = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (BLOKADA,)).fetchone()["ok"]
            yield ok
        finally:
            if ok:
                conn.execute("SELECT pg_advisory_unlock(%s)", (BLOKADA,))
            conn.close()

    def zapisz_reklamy(self, reklamy: list[ReklamaMeta], rekordy: dict[str, dict], opis_zapytania: str,
                       skan_id: int) -> set[str]:
        """Dopisuje reklamy albo odświeża znane. Zwraca id reklam widzianych pierwszy raz."""
        nowe = set()
        for r in reklamy:
            wiersz = self.conn.execute(
                """INSERT INTO adlib_reklamy (id, page_id, page_name, domena, rekord, zapytania, skan_id)
                   VALUES (%s, %s, %s, %s, %s, ARRAY[%s], %s)
                   ON CONFLICT (id) DO UPDATE SET
                       ostatnie_wykrycie = now(),
                       rekord = EXCLUDED.rekord,
                       zapytania = (SELECT array_agg(DISTINCT z)
                                    FROM unnest(adlib_reklamy.zapytania || EXCLUDED.zapytania) AS z)
                   RETURNING (xmax = 0) AS nowa""",
                (r.id, r.page_id, r.page_name, r.domena, Jsonb(rekordy[r.id]), opis_zapytania, skan_id),
            ).fetchone()
            if wiersz["nowa"]:
                nowe.add(r.id)
        return nowe

    def znane_strony(self, przed: datetime) -> set[str]:
        wiersze = self.conn.execute(
            "SELECT DISTINCT page_id FROM adlib_reklamy WHERE pierwsze_wykrycie < %s AND page_id IS NOT NULL",
            (przed,)).fetchall()
        return {w["page_id"] for w in wiersze}

    def ustaw_ocene(self, id_reklamy: str, ocena: Ocena, status: str) -> None:
        self.conn.execute("UPDATE adlib_reklamy SET wynik = %s, powody = %s, status = %s WHERE id = %s",
                          (ocena.wynik, Jsonb([p.model_dump() for p in ocena.powody]), status, id_reklamy))

    def ustaw_status(self, id_reklamy: str, status: str, blad: str | None = None) -> None:
        self.conn.execute("UPDATE adlib_reklamy SET status = %s, blad = %s WHERE id = %s",
                          (status, blad, id_reklamy))

    def ustaw_analize(self, id_reklamy: str, id_zgloszenia: str | None, analiza: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE adlib_reklamy SET status = 'przeanalizowana', id_zgloszenia = %s, analiza = %s, blad = NULL "
            "WHERE id = %s", (id_zgloszenia, Jsonb(analiza), id_reklamy))

    def oznacz_zdjeta(self, id_reklamy: str, rodzaj: str) -> None:
        self.conn.execute("UPDATE adlib_reklamy SET zdjecie = %s WHERE id = %s", (rodzaj, id_reklamy))

    def do_sprawdzenia(self, dni: int) -> dict[str, list[str]]:
        """Przeanalizowane reklamy z ostatnich dni, jeszcze nie zdjęte, pogrupowane po stronach."""
        wiersze = self.conn.execute(
            "SELECT id, page_id FROM adlib_reklamy WHERE status = 'przeanalizowana' AND zdjecie IS NULL "
            "AND page_id IS NOT NULL AND pierwsze_wykrycie > now() - make_interval(days => %s)", (dni,)).fetchall()
        grupy: dict[str, list[str]] = defaultdict(list)
        for w in wiersze:
            grupy[w["page_id"]].append(w["id"])
        return dict(grupy)

    def reklamy(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        warunek, parametry = ("WHERE status = %s", (status, limit)) if status else ("", (limit,))
        return self.conn.execute(
            f"""SELECT id, page_id, page_name, domena, status, wynik, powody, zdjecie, id_zgloszenia, analiza, blad,
                       zapytania, pierwsze_wykrycie, ostatnie_wykrycie,
                       rekord->'ad_creative_bodies'->>0 AS tresc, rekord->>'ad_delivery_start_time' AS start
                FROM adlib_reklamy {warunek}
                ORDER BY pierwsze_wykrycie DESC, wynik DESC NULLS LAST LIMIT %s""", parametry).fetchall()

    def dodaj_trop(self, rodzaj: str, wartosc: str, zrodlo: str, opis: str = "") -> None:
        self.conn.execute(
            "INSERT INTO adlib_tropy (rodzaj, wartosc, zrodlo, opis) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (rodzaj, wartosc) DO NOTHING", (rodzaj, wartosc, zrodlo, opis))

    def zapisz_trafienie(self, rodzaj: str, wartosci: list[str]) -> None:
        self.conn.execute(
            "UPDATE adlib_tropy SET trafien = trafien + 1, ostatnie_trafienie = now() "
            "WHERE rodzaj = %s AND wartosc = ANY(%s)", (rodzaj, wartosci))

    def strony_tropow(self) -> list[str]:
        return [w["wartosc"] for w in self.conn.execute(
            "SELECT wartosc FROM adlib_tropy WHERE rodzaj = 'strona' ORDER BY dodano").fetchall()]

    def frazy_z_wynikow(self, wygasanie_dni: int, limit: int) -> list[str]:
        return [w["wartosc"] for w in self.conn.execute(
            "SELECT wartosc FROM adlib_tropy WHERE rodzaj = 'fraza' "
            "AND coalesce(ostatnie_trafienie, dodano) > now() - make_interval(days => %s) "
            "ORDER BY coalesce(ostatnie_trafienie, dodano) DESC LIMIT %s", (wygasanie_dni, limit)).fetchall()]

    def nowy_skan(self, tryb: str, wyzwalacz: str) -> int:
        return self.conn.execute("INSERT INTO adlib_skany (tryb, wyzwalacz) VALUES (%s, %s) RETURNING id",
                                 (tryb, wyzwalacz)).fetchone()["id"]

    def zakoncz_skan(self, skan_id: int, status: str, liczby: dict[str, int], bledy: list[str]) -> None:
        self.conn.execute(
            """UPDATE adlib_skany SET koniec = now(), status = %s, zapytan = %s, reklam = %s, nowych = %s,
                   wybranych = %s, przeanalizowanych = %s, wywolan_api = %s, bledy = %s WHERE id = %s""",
            (status, liczby.get("zapytan", 0), liczby.get("reklam", 0), liczby.get("nowych", 0),
             liczby.get("wybranych", 0), liczby.get("przeanalizowanych", 0), liczby.get("wywolan_api", 0),
             Jsonb(bledy), skan_id))

    def ostatni_udany_skan(self) -> datetime | None:
        w = self.conn.execute("SELECT max(start) AS t FROM adlib_skany WHERE status = 'zakonczony'").fetchone()
        return w["t"]

    def status(self) -> dict[str, Any]:
        skany = self.conn.execute("SELECT * FROM adlib_skany ORDER BY id DESC LIMIT 5").fetchall()
        liczby = {w["status"]: w["n"] for w in self.conn.execute(
            "SELECT status, count(*) AS n FROM adlib_reklamy GROUP BY status").fetchall()}
        zdjete = self.conn.execute(
            "SELECT zdjecie, count(*) AS n FROM adlib_reklamy WHERE zdjecie IS NOT NULL GROUP BY zdjecie").fetchall()
        tropy = {w["rodzaj"]: w["n"] for w in self.conn.execute(
            "SELECT rodzaj, count(*) AS n FROM adlib_tropy GROUP BY rodzaj").fetchall()}
        return {"ostatnie_skany": skany, "reklamy": liczby, "zdjete_przez_mete": {w["zdjecie"]: w["n"] for w in zdjete},
                "tropy": tropy}


def teraz() -> datetime:
    return datetime.now(timezone.utc)


def poczatek_okna(ostatni: datetime | None, pierwsze_okno_dni: int, zapas_dni: int) -> str:
    """Data od której pytamy API: ostatni udany skan minus zapas, a przy pierwszym skanie stałe okno."""
    od = (ostatni - timedelta(days=zapas_dni)) if ostatni else teraz() - timedelta(days=pierwsze_okno_dni)
    return od.date().isoformat()
