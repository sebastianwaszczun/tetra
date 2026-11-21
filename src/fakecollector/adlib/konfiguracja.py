"""Wczytywanie config/adlib.toml i ścieżek roboczych skanera."""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

KORZEN = Path(__file__).resolve().parents[3]


@lru_cache
def wczytaj_konfiguracje(sciezka: str | None = None) -> dict[str, Any]:
    plik = Path(sciezka or os.environ.get("ADLIB_CONFIG") or KORZEN / "config" / "adlib.toml")
    with plik.open("rb") as f:
        return tomllib.load(f)


def katalog_roboczy() -> Path:
    """Nagrania odpowiedzi API, zrzuty kreacji i lista CERT. Poza repozytorium (data/)."""
    katalog = Path(os.environ.get("ADLIB_DIR") or KORZEN / "data" / "adlib")
    katalog.mkdir(parents=True, exist_ok=True)
    return katalog
