"""Konfiguracja modułu OSINT (wszystko przez zmienne środowiskowe)."""
from __future__ import annotations

import os
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


_REPO_ROOT = Path(__file__).resolve().parents[4]

# Cache wyników wzbogacania (RDAP/DNS/ASN/TLS/crt.sh) – jedna domena nie jest odpytywana dwa razy.
CACHE_DIR = Path(os.getenv("OSINT_CACHE_DIR", _REPO_ROOT / "data" / "osint_cache"))
CACHE_TTL_S = int(os.getenv("OSINT_CACHE_TTL_S", str(24 * 3600)))

# Czasy i limity
HTTP_TIMEOUT_S = float(os.getenv("OSINT_HTTP_TIMEOUT_S", "10"))
ENRICH_TIMEOUT_S = float(os.getenv("OSINT_ENRICH_TIMEOUT_S", "8"))
ENRICH_BUDGET_S = float(os.getenv("OSINT_ENRICH_BUDGET_S", "45"))
MAX_REDIRECTS = int(os.getenv("OSINT_MAX_REDIRECTS", "10"))
MAX_ENRICH_DOMAINS = int(os.getenv("OSINT_MAX_ENRICH_DOMAINS", "8"))
MAX_ENRICH_IPS = int(os.getenv("OSINT_MAX_ENRICH_IPS", "10"))
MAX_LIVE_SUBRESOURCES = int(os.getenv("OSINT_MAX_LIVE_SUBRESOURCES", "25"))
MAX_BODY_BYTES = int(os.getenv("OSINT_MAX_BODY_BYTES", str(5 * 1024 * 1024)))
MAX_UPLOAD_BYTES = int(os.getenv("OSINT_MAX_UPLOAD_BYTES", str(300 * 1024 * 1024)))

# Przełączniki
ENABLE_CT = _bool("OSINT_ENABLE_CT", True)            # crt.sh (Certificate Transparency)
ALLOW_PRIVATE_TARGETS = _bool("OSINT_ALLOW_PRIVATE", False)  # ochrona SSRF dla trybu URL

# Scamy często pokazują "białą stronę" botom. Udajemy telefon wchodzący z Facebooka.
USER_AGENT = os.getenv(
    "OSINT_USER_AGENT",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/22F76 [FBAN/FBIOS;FBAV/500.0.0.0;FBBV/1;FBDV/iPhone15,2;"
    "FBMD/iPhone;FBSN/iOS;FBSV/18.5;FBSS/3;FBCR/;FBID/phone;FBLC/pl_PL;FBOP/5]",
)
REFERER = os.getenv("OSINT_REFERER", "https://l.facebook.com/")
ACCEPT_LANGUAGE = os.getenv("OSINT_ACCEPT_LANGUAGE", "pl-PL,pl;q=0.9,en;q=0.6")
