"""Wspólne narzędzia: normalizacja URL/domen, szablony ścieżek, kształty wartości, hashe,
listy "infrastruktury współdzielonej" (szum), zakresy CDN."""
from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from collections import Counter
from functools import lru_cache
from urllib.parse import parse_qsl, unquote, urlsplit

import tldextract

# Wbudowana lista PSL (bez pobierania z sieci) – deterministycznie i offline.
_TLD_PRIV = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)
_TLD_ICANN = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=False)


# --------------------------------------------------------------------------- domeny

@lru_cache(maxsize=4096)
def split_host(host: str) -> dict:
    """host -> {host, registered_domain (z PSL private), icann_domain, tld, subdomain, platform}.

    registered_domain traktuje subdomeny platform (np. x.netlify.app) jako osobne "domeny
    aktora", bo to aktor je zakłada. icann_domain to domena, którą można odpytać RDAP/WHOIS."""
    host = (host or "").lower().strip(".")
    p = _TLD_PRIV(host)
    i = _TLD_ICANN(host)
    reg = ".".join(x for x in (p.domain, p.suffix) if x) if p.domain else host
    icann = ".".join(x for x in (i.domain, i.suffix) if x) if i.domain else host
    platform = None
    if p.is_private:
        platform = p.suffix
    else:
        for plat in ACTOR_PLATFORMS:
            if host.endswith("." + plat):
                platform = plat
                reg = host  # cały host = zasób aktora (np. strefa BunnyCDN)
                break
    return {
        "host": host,
        "registered_domain": reg,
        "icann_domain": icann,
        "tld": i.suffix or host.rsplit(".", 1)[-1],
        "subdomain": p.subdomain if not platform else host[: -len(reg)].rstrip("."),
        "platform": platform,
    }


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def is_public_ip(s: str) -> bool:
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified)


# Domeny powszechnie używane przez wszystkich (Google, Meta, publiczne CDN-y z bibliotekami).
# Ich obecność NIE łączy kampanii – ale identyfikatory w nich (Pixel ID, AW-…) już tak.
COMMODITY_DOMAINS = {
    "google.com", "google.pl", "googleapis.com", "gstatic.com", "googletagmanager.com",
    "google-analytics.com", "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "youtube.com", "ytimg.com", "youtube-nocookie.com", "ggpht.com", "googleusercontent.com",
    "facebook.com", "facebook.net", "fbcdn.net", "fb.com", "instagram.com", "cdninstagram.com",
    "fb.me", "fb.watch", "fb.gg", "m.me", "messenger.com", "whatsapp.com", "wa.me", "threads.net",
    "jsdelivr.net", "cdnjs.cloudflare.com", "cloudflare.com", "cloudflareinsights.com",
    "unpkg.com", "jquery.com", "bootstrapcdn.com", "jsdelivr.com", "fontawesome.com",
    "unsplash.com", "pexels.com", "pixabay.com", "gravatar.com", "wp.com", "w3.org",
    "schema.org", "apple.com", "microsoft.com", "bing.com", "clarity.ms", "tiktok.com",
    "yandex.ru", "mc.yandex.ru", "hotjar.com", "typekit.net", "polyfill.io", "x.com",
    "twitter.com", "twimg.com", "vimeo.com", "vimeocdn.com", "recaptcha.net",
}

# Platformy, na których subdomena należy do aktora (darmowy hosting / pull-zone CDN).
ACTOR_PLATFORMS = (
    "netlify.app", "b-cdn.net", "pages.dev", "workers.dev", "vercel.app", "github.io",
    "web.app", "firebaseapp.com", "herokuapp.com", "azurewebsites.net", "cloudfront.net",
    "s3.amazonaws.com", "blogspot.com", "wixsite.com", "glitch.me", "onrender.com",
    "fly.dev", "r2.dev", "weebly.com", "framer.app", "webflow.io", "carrd.co",
    "tilda.ws", "surge.sh", "translate.goog",
)


def is_commodity(host: str) -> bool:
    host = host.lower()
    if not host:
        return True
    reg = split_host(host)["registered_domain"]
    return host in COMMODITY_DOMAINS or reg in COMMODITY_DOMAINS or any(
        host.endswith("." + d) for d in COMMODITY_DOMAINS)


# --------------------------------------------------------------------------- CDN / hosting
_CLOUDFLARE_V4 = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22")]

# ASN-y "wielkich chmur/CDN" – współdzielone przez miliony stron => bardzo słaby sygnał.
SHARED_ASNS = {
    "13335": "Cloudflare", "209242": "Cloudflare", "16509": "Amazon", "14618": "Amazon",
    "15169": "Google", "396982": "Google Cloud", "8075": "Microsoft", "54113": "Fastly",
    "20940": "Akamai", "16625": "Akamai", "32934": "Meta", "60068": "CDN77/DataCamp",
    "212238": "CDN77/DataCamp", "13414": "Twitter", "2635": "Automattic", "397273": "Render",
    "209": "Lumen", "19551": "Incapsula", "30148": "Sucuri",
}


def cdn_of_ip(ip: str) -> str | None:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if a.version == 4 and any(a in n for n in _CLOUDFLARE_V4):
        return "cloudflare"
    if a.version == 6 and (str(a).startswith("2606:4700:") or str(a).startswith("2a06:98c")):
        return "cloudflare"
    return None


# --------------------------------------------------------------------------- kształty wartości
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-([0-9a-f])[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX = re.compile(r"^[0-9a-f]+$", re.I)
_DIG = re.compile(r"^\d+$")
_ALNUM = re.compile(r"^[A-Za-z0-9]+$")


def entropy(s: str) -> float:
    if not s:
        return 0.0
    c = Counter(s)
    n = len(s)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def value_shape(v: str) -> str:
    """Zamienia konkretną wartość na jej "kształt" – identyfikuje generator (TDS/tracker),
    a nie konkretne kliknięcie. Np. 'WVVfDj0sSpk725CZefaO' -> 'A20', UUIDv7 -> 'uuid7'."""
    v = unquote(v or "")
    if v == "":
        return "empty"
    m = _UUID.match(v)
    if m:
        return f"uuid{m.group(1)}"
    if _DIG.match(v):
        return f"N{len(v)}"
    if _HEX.match(v) and len(v) >= 8:
        return f"H{len(v)}"
    if _ALNUM.match(v):
        if v.isalpha():
            mixed = any(c.isupper() for c in v[1:]) and any(c.islower() for c in v)
            if mixed and len(v) >= 8:
                return f"A{len(v)}"        # losowy token bez cyfr (np. 'LyTgHoJLnc')
            return "word" if len(v) < 12 else f"L{len(v)}"
        return f"A{len(v)}"
    if v.startswith("{{") or v.startswith("{"):
        return "macro"
    return "text"


def is_id_like(seg: str) -> bool:
    """Segment ścieżki wyglądający na identyfikator (uuid, długi hex, liczba, losowy token)."""
    if not seg:
        return False
    if _UUID.match(seg) or _DIG.match(seg):
        return True
    if _HEX.match(seg) and len(seg) >= 12:
        return True
    if _ALNUM.match(seg) and len(seg) >= 16 and any(c.isdigit() for c in seg) and any(c.isalpha() for c in seg):
        return True
    return False


def template_segment(seg: str) -> str:
    if _UUID.match(seg):
        return "{uuid}"
    if _DIG.match(seg):
        return "{n}"
    if _HEX.match(seg) and len(seg) >= 12:
        return "{hex}"
    if _ALNUM.match(seg) and len(seg) >= 16 and any(c.isdigit() for c in seg):
        return "{id}"
    # nazwy plików z hashem: main.933bc64f.png -> main.{h}.png
    seg = re.sub(r"(?<=[._-])[0-9a-f]{8,}(?=[._-])", "{h}", seg, flags=re.I)
    seg = re.sub(r"^[0-9a-f]{24,}(?=\.)", "{h}", seg, flags=re.I)
    return seg


def path_template(path: str) -> str:
    segs = [template_segment(s) for s in (path or "/").split("/")]
    return "/".join(segs) or "/"


def query_schema(query: str) -> tuple[list[str], str]:
    """-> (posortowane nazwy parametrów, sygnatura 'name=shape&...')."""
    pairs = parse_qsl(query or "", keep_blank_values=True)
    names = sorted({k for k, _ in pairs})
    shapes = {}
    for k, v in pairs:
        shapes.setdefault(k, value_shape(v))
    sig = "&".join(f"{k}={shapes[k]}" for k in names)
    return names, sig


def uuid7_timestamp(v: str) -> str | None:
    """UUIDv7 zawiera znacznik czasu (ms) – zdradza moment wygenerowania wizyty przez TDS."""
    m = _UUID.match(v or "")
    if not m or m.group(1) != "7":
        return None
    from datetime import datetime, timezone
    ms = int(v.replace("-", "")[:12], 16)
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


# --------------------------------------------------------------------------- hashe
def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def short_hash(s: str, n: int = 16) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:n]


def normalize_code(text: str) -> str:
    """Normalizacja JS/CSS przed hashowaniem: bez białych znaków i komentarzy blokowych,
    tak by drobne różnice formatowania nie psuły dopasowania."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"\s+", "", text)


def simhash64(tokens: list[str]) -> str:
    if not tokens:
        return "0" * 16
    v = [0] * 64
    for t, w in Counter(tokens).items():
        h = int.from_bytes(hashlib.md5(t.encode()).digest()[:8], "big")
        for i in range(64):
            v[i] += w if (h >> i) & 1 else -w
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return f"{out:016x}"


def hamming_hex(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def dedup(seq):
    seen = set()
    out = []
    for x in seq:
        k = x if isinstance(x, (str, int, float, tuple)) else repr(x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out
