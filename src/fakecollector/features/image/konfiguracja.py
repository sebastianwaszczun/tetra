"""Odczyt wersjonowanej konfiguracji modułu."""
import tomllib
from pathlib import Path
from typing import Any

KONFIG = Path(__file__).resolve().parents[4] / "config" / "obraz.toml"


def wczytaj_konfiguracje(plik: Path = KONFIG) -> dict[str, Any]:
    """Wczytuje progi i słowniki z pliku TOML."""
    with plik.open("rb") as strumien:
        return tomllib.load(strumien)
