"""Katalog identyfikatorów analityki/reklam i parametrów TDS.

Identyfikator konta reklamowego/analitycznego (Pixel Meta, Google Ads AW-…, GA4, GTM,
TikTok, Yandex…) jest przypisany do operatora, a nie do domeny. Domeny rotują co kilka dni,
konto pikseli zostaje – to jeden z najmocniejszych sygnałów łączących kampanie."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlsplit

# (typ, regex w tekście HTML/JS) – grupa 1 = identyfikator
TEXT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("fb_pixel", re.compile(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"]?(\d{10,20})")),
    ("fb_pixel", re.compile(r"facebook\.com/tr\?[^\"'\s>]*\bid=(\d{10,20})")),
    ("fb_pixel", re.compile(r"connect\.facebook\.net/signals/config/(\d{10,20})")),
    ("fb_pixel", re.compile(r"['\"]?(?:pixel_?id|fb_?pixel|fbPixel|pixelId)['\"]?\s*[:=]\s*['\"](\d{12,20})['\"]", re.I)),
    ("google_ads", re.compile(r"\b(AW-\d{8,12})\b")),
    ("ga4", re.compile(r"\b(G-[A-Z0-9]{8,12})\b")),
    ("gtm", re.compile(r"\b(GTM-[A-Z0-9]{5,9})\b")),
    ("ua", re.compile(r"\b(UA-\d{4,10}-\d{1,4})\b")),
    ("tiktok_pixel", re.compile(r"ttq\.load\(\s*['\"]([A-Z0-9]{15,25})['\"]")),
    ("tiktok_pixel", re.compile(r"analytics\.tiktok\.com/i18n/pixel/events\.js\?sdkid=([A-Z0-9]{15,25})")),
    ("yandex_metrika", re.compile(r"\bym\(\s*(\d{6,10})\s*,\s*['\"]init")),
    ("yandex_metrika", re.compile(r"mc\.yandex\.ru/watch/(\d{6,10})")),
    ("clarity", re.compile(r"clarity\.ms/tag/([a-z0-9]{8,12})")),
    ("hotjar", re.compile(r"hjid\s*:\s*(\d{5,9})")),
    ("snap_pixel", re.compile(r"snaptr\(\s*['\"]init['\"]\s*,\s*['\"]([0-9a-f-]{36})")),
    ("taboola", re.compile(r"_tfa\.push\(\{[^}]*id:\s*(\d{6,8})")),
    ("outbrain", re.compile(r"OB_ADV_ID\s*=\s*['\"]?([0-9a-f]{20,40})")),
    ("google_site_verification", re.compile(r"name=['\"]google-site-verification['\"]\s+content=['\"]([^'\"]{20,80})")),
    ("fb_domain_verification", re.compile(r"name=['\"]facebook-domain-verification['\"]\s+content=['\"]([a-z0-9]{20,40})")),
    ("yandex_verification", re.compile(r"name=['\"]yandex-verification['\"]\s+content=['\"]([0-9a-f]{16})")),
    ("adsense", re.compile(r"\b(ca-pub-\d{10,20})\b")),
]

# (typ, host-sufiks, regex na ścieżce+query) – identyfikatory w URL-ach żądań z HAR
URL_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("fb_pixel", "facebook.net", re.compile(r"/signals/config/(\d{10,20})")),
    ("fb_pixel", "facebook.com", re.compile(r"/tr/?\?(?:.*&)?id=(\d{10,20})")),
    ("google_ads", "googletagmanager.com", re.compile(r"[?&]id=(AW-\d+)")),
    ("ga4", "googletagmanager.com", re.compile(r"[?&]id=(G-[A-Z0-9]+)")),
    ("gtm", "googletagmanager.com", re.compile(r"[?&]id=(GTM-[A-Z0-9]+)")),
    ("google_ads", "googleadservices.com", re.compile(r"/conversion/(\d{8,12})")),
    ("google_ads", "google.com", re.compile(r"/pagead/1p-(?:conversion|user-list)/(\d{8,12})")),
    ("google_ads", "doubleclick.net", re.compile(r"/viewthroughconversion/(\d{8,12})")),
    ("google_ads", "google.com", re.compile(r"/ccm/collect\?(?:.*&)?tid=(AW-\d+)")),
    ("ga4", "google-analytics.com", re.compile(r"[?&]tid=(G-[A-Z0-9]+)")),
    ("tiktok_pixel", "tiktok.com", re.compile(r"sdkid=([A-Z0-9]{15,25})")),
    ("yandex_metrika", "yandex.ru", re.compile(r"/watch/(\d{6,10})")),
    ("clarity", "clarity.ms", re.compile(r"/tag/([a-z0-9]{8,12})")),
]

# Parametry, które Meta Ads wstawia z makr {{ad.id}} itd. – zdradzają konto/kampanię reklamową.
FB_AD_PARAMS = {
    "ad_id": "fb_ad_id", "adid": "fb_ad_id", "fb_ad_id": "fb_ad_id",
    "adset_id": "fb_adset_id", "campaign_id": "fb_campaign_id", "fb_campaign_id": "fb_campaign_id",
    "utm_id": "fb_campaign_id",                  # w FB utm_id = {{campaign.id}} (gdy 15–20 cyfr)
    "utm_term": "fb_adset_id", "utm_content": "fb_ad_id",   # tylko gdy wartość to 15–20 cyfr
    "pxl": "fb_pixel", "fbpx": "fb_pixel", "pixel": "fb_pixel", "pixel_id": "fb_pixel",
}
NAMING_PARAMS = ("adset_name", "ad_name", "campaign_name", "acc", "utm_campaign", "utm_creative",
                 "placement", "utm_placement", "site_source_name", "creative", "buyer", "source")

# Parametry wskazujące silnik TDS / trackera afiliacyjnego.
TDS_FAMILIES: list[tuple[str, set[str]]] = [
    ("keitaro", {"_trk", "_camp_id", "land_id", "lpkey", "subid", "_subid"}),
    ("binom", {"lp", "camp_id", "trck"}),
    ("voluum", {"cep", "lptoken", "cpid"}),
    ("redtrack", {"rtkck", "rtkcid", "cmpid"}),
    ("bemob", {"bemob_click_id", "cid"}),
    ("utm_visit_kit", {"utm_visit"}),
]
_KEITARO_SUBID = re.compile(r"^[0-9a-v]{20}$")
# Identyfikatory kampanii wewnątrz TDS (stałe dla kampanii; land_id rotuje między landingami).
TDS_ID_PARAMS = ("_trk", "_camp_id", "camp_id", "trafficsource", "user_id", "land_id", "lp")
SECRET_PARAMS = {"access_token", "token", "fb_access_token", "auth", "session", "sid", "password"}


def ids_from_text(text: str) -> list[tuple[str, str]]:
    out = []
    for kind, rx in TEXT_PATTERNS:
        for m in rx.finditer(text):
            out.append((kind, m.group(1)))
    return out


def ids_from_url(url: str) -> list[tuple[str, str]]:
    try:
        sp = urlsplit(url)
    except ValueError:
        return []
    host = (sp.hostname or "").lower()
    tail = sp.path + ("?" + sp.query if sp.query else "")
    out = []
    for kind, suffix, rx in URL_PATTERNS:
        if host == suffix or host.endswith("." + suffix):
            for m in rx.finditer(tail):
                v = m.group(1)
                if kind == "google_ads" and v.isdigit():
                    v = "AW-" + v          # numer konwersji = konto AW-<numer>
                out.append((kind, v))
    return out


def analyze_params(url: str) -> dict:
    """Parametry URL strony docelowej -> identyfikatory reklam, nazewnictwo, rodzina TDS."""
    try:
        q = urlsplit(url).query
    except ValueError:
        return {}
    pairs = parse_qsl(q, keep_blank_values=True)
    ad_ids: dict[str, list[str]] = {}
    naming: dict[str, str] = {}
    secrets: list[str] = []
    click_ids: dict[str, str] = {}
    names = {k for k, _ in pairs}
    for k, v in pairs:
        kl = k.lower()
        v = unquote(v)
        if kl in SECRET_PARAMS or v.startswith("EAA"):
            secrets.append(kl)
            continue
        kind = FB_AD_PARAMS.get(kl)
        if kind and v.isdigit() and 13 <= len(v) <= 20:
            if v not in ad_ids.setdefault(kind, []):
                ad_ids[kind].append(v)
            if kl in ("ad_id", "adid", "fb_ad_id") and v not in ad_ids.setdefault("fb_ad_id_explicit", []):
                ad_ids["fb_ad_id_explicit"].append(v)
        if kl in NAMING_PARAMS and v and not v.isdigit():
            naming[kl] = v[:200]
        if kl in ("clickid", "click_id", "subid", "sub_id", "fbclid", "gclid", "ttclid", "utm_visit", "cid"):
            click_ids[kl] = v[:120]
    families = [fam for fam, keys in TDS_FAMILIES if names & keys]
    tds_ids = {k: unquote(v)[:60] for k, v in pairs if k in TDS_ID_PARAMS}
    cid = click_ids.get("clickid") or click_ids.get("subid")
    if cid and _KEITARO_SUBID.match(cid) and "keitaro" not in families:
        families.append("keitaro")          # format sub_id Keitaro: 20 znaków [0-9a-v]
    return {"ad_ids": ad_ids, "naming": naming, "click_ids": click_ids,
            "tds_families": families, "tds_ids": tds_ids, "secret_params": secrets}
