"""Deduplikacja wejścia: hash plików, normalizacja adresów i indeks odcisków."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Parametry śledzące, które zmieniają się między kliknięciami tej samej reklamy
TRACKING_PARAMS = {
    "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "fbclid", "msclkid", "yclid", "ttclid",
    "twclid", "li_fat_id", "mc_cid", "mc_eid", "igshid", "_ga", "_gl",
}
TRACKING_PREFIXES = ("utm_", "gad_", "hsa_", "pk_", "mtm_")


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).lower().encode()).hexdigest()


def normalize_url(url: str) -> str:
    """Adres bez parametrów śledzących, fragmentu i końcowego ukośnika, z małymi literami w hoście.

    https://Example.com/oferta/?utm_source=fb&id=7#top -> https://example.com/oferta?id=7
    Parametry kampanii zapisuje osobno moduł OSINT, bo same w sobie są cennym sygnałem.
    """
    url = url.strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)]
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/") or ""
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def fingerprint(incident) -> str | None:
    """Odcisk materiału: te same pliki wejściowe, ten sam tekst albo ten sam adres = duplikat."""
    keys = sorted(a.sha256 for a in incident.artifacts if a.sha256 and a.source in {"input", "folder"})
    if not keys and incident.input_type == "text" and incident.text:
        keys = [sha256_text(incident.text)]
    if not keys and incident.urls:
        keys = [normalize_url(incident.urls[0])]
    if not keys:
        return None
    return hashlib.sha256("|".join(keys).encode()).hexdigest()[:32]


class DedupIndex:
    """Prosty indeks odcisk -> id materiału w pliku JSON (data/store/index.json).

    Docelowo tę rolę przejmie tabela w Postgresie; plik wystarcza do testów i demo.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            self.data: dict[str, str] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def check_and_add(self, fp: str | None, incident_id: str) -> str | None:
        """Zwraca id wcześniejszego materiału, jeśli odcisk już był; w przeciwnym razie zapisuje nowy."""
        if not fp:
            return None
        if fp in self.data and self.data[fp] != incident_id:
            return self.data[fp]
        self.data[fp] = incident_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        return None
