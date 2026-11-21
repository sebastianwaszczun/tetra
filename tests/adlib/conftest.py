"""Wspólne przygotowanie testów skanera: sztuczne rekordy z API i baza w osobnym schemacie."""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from fakecollector.adlib.konfiguracja import wczytaj_konfiguracje


def rekord(id_reklamy: str, page_id: str = "100", page_name: str = "Strona testowa", tresc: str = "",
           tytul: str | None = None, podpis: str | None = None, platnik: str | None = "Jan Testowy",
           start: str = "2026-11-21") -> dict[str, Any]:
    r: dict[str, Any] = {
        "id": id_reklamy, "page_id": page_id, "page_name": page_name,
        "ad_creation_time": start, "ad_delivery_start_time": start,
        "ad_snapshot_url": f"https://www.facebook.com/ads/archive/render_ad/?id={id_reklamy}&access_token=SEKRET123",
        "languages": ["pl"], "publisher_platforms": ["facebook"], "eu_total_reach": 1000,
        "target_ages": ["40", "65"], "target_gender": "All",
        "target_locations": [{"name": "Poland", "num_obfuscated": 0, "type": "countries", "excluded": False}],
    }
    if tresc:
        r["ad_creative_bodies"] = [tresc]
    if tytul:
        r["ad_creative_link_titles"] = [tytul]
    if podpis:
        r["ad_creative_link_captions"] = [podpis]
    r["beneficiary_payers"] = [{"payer": platnik, "beneficiary": platnik, "current": True}] if platnik else []
    return r


@pytest.fixture
def cfg() -> dict[str, Any]:
    return wczytaj_konfiguracje()


@pytest.fixture
def magazyn():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("brak DATABASE_URL")
    from fakecollector.adlib.magazyn import Magazyn
    try:
        m = Magazyn(schemat=f"adlib_test_{uuid.uuid4().hex[:8]}")
    except Exception as exc:
        pytest.skip(f"baza niedostępna: {exc.__class__.__name__}")
    m.przygotuj()
    yield m
    m.conn.execute(f'DROP SCHEMA "{m.schemat}" CASCADE')
    m.zamknij()
