"""Zamiana rekordu OSINT na listę *pivotów* – znormalizowanych par (rodzaj, wartość) z wagą.

Pivot to "ślad, po którym można skoczyć do innych incydentów". Dwa materiały dzielące pivot
z wysoką wagą najpewniej mają wspólnego operatora/kit. Wagi to aprioryczna specyficzność
sygnału (0–1); korelator mnoży je przez rzadkość wartości w korpusie (IDF), więc np. IP
Cloudflare'a, które dzielą setki stron, samo się wygasza.

Zakresy (scope):
  identity – ten sam materiał/reklama (duplikat): domena, ID reklamy
  operator – konto operatora: piksele, konta reklamowe, weryfikacje domen, kontakty
  kit      – konkretne wdrożenie szablonu: katalog kitu, hashe JS/CSS, endpointy, favicon
  infra    – hosting i rejestracja: IP, NS, certyfikat, rejestrator+partia rejestracji
  behavior – cechy zachowania i stylu (słabe, liczą się w kombinacji)
"""
from __future__ import annotations

from datetime import datetime

from .utils import SHARED_ASNS, short_hash

# kind -> (waga, scope, opis)
WEIGHTS: dict[str, tuple[float, str, str]] = {
    # identity
    "registered_domain":      (1.00, "identity", "ta sama domena (rejestrowalna)"),
    "landing_host":           (0.90, "identity", "ten sam host strony docelowej"),
    "fb_ad_id":               (1.00, "identity", "ten sam identyfikator reklamy Meta (parametr ad_id)"),
    "ad_archive_id":          (1.00, "identity", "ta sama reklama w Bibliotece Reklam"),
    # operator
    "fb_pixel":               (0.95, "operator", "ten sam Pixel Meta"),
    "google_ads":             (0.95, "operator", "to samo konto konwersji Google Ads"),
    "ga4":                    (0.90, "operator", "ta sama usługa GA4"),
    "gtm":                    (0.90, "operator", "ten sam kontener GTM"),
    "ua":                     (0.85, "operator", "to samo konto Universal Analytics"),
    "tiktok_pixel":           (0.95, "operator", "ten sam Pixel TikTok"),
    "yandex_metrika":         (0.90, "operator", "ten sam licznik Yandex Metrika"),
    "clarity":                (0.90, "operator", "ten sam projekt MS Clarity"),
    "hotjar":                 (0.85, "operator", "ten sam Hotjar ID"),
    "snap_pixel":             (0.95, "operator", "ten sam Snap Pixel"),
    "taboola":                (0.80, "operator", "to samo konto Taboola"),
    "outbrain":               (0.80, "operator", "to samo konto Outbrain"),
    "adsense":                (0.90, "operator", "to samo konto AdSense"),
    "google_site_verification": (0.95, "operator", "ten sam token weryfikacji Google"),
    "fb_domain_verification": (0.95, "operator", "ten sam token weryfikacji domeny Meta (Business Manager)"),
    "yandex_verification":    (0.90, "operator", "ten sam token weryfikacji Yandex"),
    "fb_ad_object":           (0.95, "operator", "ten sam obiekt Meta Ads (kampania/zestaw/reklama) z parametrów URL"),
    "fb_id_shard":            (0.20, "operator", "heurystyka: te same 4 końcowe cyfry ID obiektów Meta Ads (obserwowane jako stałe w obrębie konta)"),
    "fb_page_id":             (0.95, "operator", "ta sama strona-reklamodawca w Bibliotece Reklam"),
    "ad_naming":              (0.70, "operator", "to samo nazewnictwo kont/zestawów reklam (media buyer)"),
    "phone":                  (0.90, "operator", "ten sam numer telefonu"),
    "email":                  (0.90, "operator", "ten sam adres e-mail"),
    "messenger":              (0.90, "operator", "ten sam kontakt Telegram/WhatsApp"),
    "crypto_wallet":          (1.00, "operator", "ten sam portfel kryptowalut"),
    "secret_param":           (0.90, "operator", "ten sam (zahashowany) token dostępowy w URL"),
    "registrant_org":         (0.80, "operator", "ten sam rejestrujący (gdy bez proxy prywatności)"),
    # kit
    "kit_dir":                (0.90, "kit", "ten sam katalog wdrożenia kitu (np. landing-cdn/<uuid>)"),
    "kit_family":             (0.45, "kit", "ta sama platforma/rodzina kitu (prefiks ścieżek)"),
    "js_hash":                (0.85, "kit", "identyczny (po normalizacji) własny skrypt JS"),
    "css_hash":               (0.75, "kit", "identyczny własny arkusz CSS"),
    "inline_js_hash":         (0.70, "kit", "identyczny skrypt inline"),
    "image_hash":             (0.70, "kit", "identyczny plik obrazu (bajt w bajt)"),
    "endpoint":               (0.75, "kit", "ten sam szablon endpointu API/beacona"),
    "favicon_mmh3":           (0.70, "kit", "ta sama favicona (hash Shodana)"),
    "form_signature":         (0.55, "kit", "ten sam formularz (akcja + pola)"),
    "actor_host":             (0.80, "kit", "ten sam zewnętrzny host aktora (tracker, CDN, API)"),
    "media_host":             (0.70, "kit", "ten sam host wideo/streamu"),
    "url_param_schema":       (0.50, "kit", "ten sam schemat parametrów URL (generator TDS)"),
    "path_layout":            (0.35, "kit", "ten sam układ katalogów (szablon ścieżek)"),
    "dom_simhash":            (0.50, "kit", "podobny szkielet DOM (simhash, Hamming ≤ 6)"),
    # infra
    "ip":                     (0.60, "infra", "ten sam adres IP (poza współdzielonym CDN)"),
    "ip_cdn":                 (0.03, "infra", "ten sam adres IP współdzielonego CDN"),
    "cidr":                   (0.30, "infra", "ta sama sieć (CIDR) hostingu"),
    "asn":                    (0.15, "infra", "ten sam ASN hostingu (mały dostawca)"),
    "ns_set":                 (0.55, "infra", "ten sam zestaw serwerów NS (w CF para NS = konto)"),
    "mx":                     (0.45, "infra", "ten sam serwer pocztowy"),
    "soa_rname":              (0.35, "infra", "ten sam kontakt w rekordzie SOA"),
    "spf_include":            (0.40, "infra", "ten sam include w SPF"),
    "cert_sha256":            (0.95, "infra", "ten sam certyfikat TLS"),
    "cert_san_peer":          (0.90, "infra", "domeny współdzielą certyfikat (SAN)"),
    "registration_batch":     (0.45, "infra", "ten sam rejestrator+TLD i tydzień rejestracji (rejestracje hurtowe)"),
    "registrar":              (0.08, "infra", "ten sam rejestrator"),
    "tld":                    (0.05, "infra", "ta sama domena najwyższego poziomu"),
    "hosting_platform":       (0.10, "infra", "ta sama platforma hostingowa/CDN"),
    "server_stack":           (0.15, "infra", "ten sam stos serwera (server/x-powered-by/via)"),
    "header_fingerprint":     (0.20, "infra", "ten sam zestaw nagłówków HTTP"),
    "cookie":                 (0.45, "infra", "to samo niestandardowe ciasteczko"),
    "csp_host":               (0.60, "infra", "ten sam host w polityce CSP"),
    # behavior
    "tds_campaign":           (0.65, "operator", "ta sama kampania w TDS (np. Keitaro _trk/_camp_id/trafficsource/user_id)"),
    "tds_family":             (0.20, "behavior", "ta sama rodzina TDS/trackera (Keitaro, Binom…)"),
    "naming_script":          (0.10, "behavior", "cyrylica w nazewnictwie reklam (język operatora)"),
    "kit_trait":              (0.08, "behavior", "wspólna cecha zachowania kitu"),
    "kit_trait_set":          (0.40, "behavior", "identyczny zestaw cech kitu (≥3 cechy)"),
    "lib_set":                (0.15, "behavior", "identyczny zestaw bibliotek z wersjami"),
    "generator":              (0.20, "behavior", "ten sam generator strony (meta generator)"),
    "lang":                   (0.02, "behavior", "ten sam język strony"),
}


def _p(kind: str, value, evidence: str = "", weight: float | None = None) -> dict:
    w, scope, _ = WEIGHTS[kind]
    return {"kind": kind, "value": str(value), "weight": round(weight if weight is not None else w, 3),
            "scope": scope, "evidence": evidence[:300]}


def build_pivots(rec: dict) -> list[dict]:
    P: list[dict] = []
    add = P.append
    landing = rec.get("landing") or {}

    # ---- identity
    for d in landing.get("registered_domains") or []:
        add(_p("registered_domain", d, "domena strony docelowej"))
    for h in landing.get("hosts") or []:
        add(_p("landing_host", h, "host strony docelowej"))
    params = rec.get("url_params") or {}
    ids = params.get("ad_ids") or {}
    for v in ids.get("fb_ad_id_explicit") or []:
        add(_p("fb_ad_id", v, "parametr ad_id"))
    shards = set()
    for kind, vals in ids.items():
        if kind == "fb_ad_id_explicit":
            continue
        for v in vals:
            add(_p("fb_ad_object", v, f"parametr URL ({kind})"))
            shards.add(v[-4:])
    if len(shards) == 1:
        add(_p("fb_id_shard", shards.pop(), "wszystkie ID obiektów kończą się tak samo"))
    for k, v in (params.get("naming") or {}).items():
        if k in ("acc", "adset_name", "ad_name", "campaign_name", "buyer") and len(v) >= 6:
            add(_p("ad_naming", f"{k}={v}", f"nazewnictwo w parametrze {k}"))
    for s in params.get("secret_hashes") or []:
        add(_p("secret_param", s, "zahashowany token w URL"))
    if params.get("schema") and params.get("param_count", 0) >= 3:
        add(_p("url_param_schema", short_hash(params["schema"]), params["schema"]))
    tid = {k: v for k, v in (params.get("tds_ids") or {}).items() if k not in ("land_id", "lp")}
    if len(tid) >= 2:
        add(_p("tds_campaign", "&".join(f"{k}={tid[k]}" for k in sorted(tid)), "parametry kampanii TDS"))
    if "cyrillic" in (params.get("naming_scripts") or []):
        add(_p("naming_script", "cyrillic", "znaki cyrylicy w parametrach nazewnictwa"))
    for fam in params.get("tds_families") or []:
        add(_p("tds_family", fam, "parametry charakterystyczne dla TDS"))
    for ad in rec.get("ad_library") or []:
        if ad.get("ad_archive_id"):
            add(_p("ad_archive_id", ad["ad_archive_id"], "Biblioteka Reklam Meta"))
        if ad.get("page_id"):
            add(_p("fb_page_id", ad["page_id"], f"strona: {ad.get('page_name')}"))

    # ---- operator: trackery, kontakty
    for t in rec.get("trackers") or []:
        if t["type"] in WEIGHTS:
            add(_p(t["type"], t["id"], f"źródło: {', '.join(t.get('sources', []))}"))
    html = rec.get("html") or {}
    for ph in html.get("phones") or []:
        add(_p("phone", ph))
    for em in html.get("emails") or []:
        if not em.endswith(("@sentry.io", "@example.com", "@domain.com")):
            add(_p("email", em))
    for m in html.get("messengers") or []:
        add(_p("messenger", m))
    for w in html.get("crypto_wallets") or []:
        add(_p("crypto_wallet", w))

    # ---- kit
    assets = rec.get("assets") or {}
    for d in assets.get("kit_dirs") or []:
        add(_p("kit_dir", d, "katalog zasobów z identyfikatorem wdrożenia"))
    for d in assets.get("kit_families") or []:
        add(_p("kit_family", d, "prefiks ścieżek platformy kitu"))
    for s in assets.get("scripts") or []:
        if not s.get("vendor") and s.get("norm_sha256") and (s.get("size") or 0) >= 300:
            add(_p("js_hash", s["norm_sha256"], s.get("url", "")))
    for s in assets.get("stylesheets") or []:
        if not s.get("vendor") and s.get("norm_sha256") and (s.get("size") or 0) >= 300:
            add(_p("css_hash", s["norm_sha256"], s.get("url", "")))
    for h in html.get("inline_script_hashes") or []:
        add(_p("inline_js_hash", h, "skrypt inline ≥200 znaków"))
    for im in assets.get("images") or []:
        size = im.get("size") or 0
        if im.get("role") != "actor_third_party" and size >= 4096:   # małe ikonki (reakcje FB itp.) to szum
            add(_p("image_hash", im["sha256"], f"{im.get('name', '')} ({size} B)",
                   weight=None if size >= 20_000 else 0.45))
    for e in rec.get("endpoints") or []:
        sig = f"{e['method']} {e['path_template']}" + (f"?{'&'.join(e['query_params'])}" if e["query_params"] else "")
        if e["path_template"] not in ("/", "") or e["query_params"]:
            add(_p("endpoint", sig, f"{e['kind']} na {', '.join(e['hosts'][:3])}"))
    fav = assets.get("favicon") or {}
    if fav.get("mmh3") is not None:
        add(_p("favicon_mmh3", fav["mmh3"], f"md5 {fav.get('md5')}"))
    for f in html.get("forms") or []:
        if f.get("fields") and len(f["fields"]) >= 2:
            sig = f"{f['method']} {f.get('action_template') or '-'} [{','.join(f['fields'][:15])}]"
            add(_p("form_signature", short_hash(sig), sig))
        if f.get("action_host"):
            add(_p("actor_host", f["action_host"], "host akcji formularza"))
    for h in rec.get("hosts") or []:
        if h["role"] == "actor_third_party":
            add(_p("actor_host", h["host"], f"{h['requests']} żądań"))
    for h in html.get("actor_outbound_hosts") or []:
        add(_p("actor_host", h, "link wychodzący"))
    for h in assets.get("media_hosts") or []:
        add(_p("media_host", h, "strumień wideo"))
    for d in assets.get("path_layout") or []:
        if "{" in d:
            add(_p("path_layout", d))
    if html.get("dom_simhash") and (html.get("dom_tag_count") or 0) >= 50:
        add(_p("dom_simhash", html["dom_simhash"], f"{html.get('dom_tag_count')} tagów"))

    # ---- infra
    for ip in rec.get("ips") or []:
        if ip.get("cdn") or (ip.get("asn") in SHARED_ASNS):
            add(_p("ip_cdn", ip["ip"], ip.get("asn_name") or ip.get("cdn") or ""))
        else:
            add(_p("ip", ip["ip"], f"AS{ip.get('asn')} {ip.get('asn_name') or ''}"))
            if ip.get("network_cidr"):
                add(_p("cidr", ip["network_cidr"], ip.get("network_name") or ""))
            if ip.get("asn"):
                add(_p("asn", ip["asn"], ip.get("asn_name") or ""))
        if ip.get("asn") in SHARED_ASNS:
            add(_p("hosting_platform", SHARED_ASNS[ip["asn"]].lower(), f"AS{ip['asn']}"))
    for d in rec.get("domains") or []:
        if d.get("role") not in ("landing", "redirect", "actor_third_party"):
            continue
        dns = d.get("dns") or {}
        rd = d.get("rdap") or {}
        flags = set(d.get("status_flags") or [])
        down = bool(flags & {"likely_taken_down", "registrar_or_parking_ns", "sinkhole_ip", "nxdomain"})
        ns = rd.get("nameservers") or dns.get("ns") or []
        if ns and not down:          # NS zawieszonej domeny = NS rejestratora, nie aktora
            add(_p("ns_set", ",".join(sorted(ns)), d["domain"]))
        for mx in dns.get("mx") or []:
            if not any(x in mx for x in ("google.com", "outlook.com", "mail.protection", "zoho", "yandex")):
                add(_p("mx", mx, d["domain"]))
        soa = (dns.get("soa") or {}).get("rname")
        if soa and not any(x in soa for x in ("cloudflare", "awsdns", "dns.google", "hostmaster.")):
            add(_p("soa_rname", soa, d["domain"]))
        for txt in dns.get("txt") or []:
            low = txt.lower()
            if low.startswith("facebook-domain-verification="):
                add(_p("fb_domain_verification", txt.split("=", 1)[1], f"TXT {d['domain']}"))
            elif low.startswith("google-site-verification="):
                add(_p("google_site_verification", txt.split("=", 1)[1], f"TXT {d['domain']}"))
            elif low.startswith("v=spf1"):
                for part in txt.split():
                    if part.startswith(("include:", "ip4:")) and "google" not in part and "outlook" not in part:
                        add(_p("spf_include", part, d["domain"]))
        tls = d.get("tls") or {}
        if tls.get("sha256") and "tls_cert_mismatch" not in flags and not down:
            add(_p("cert_sha256", tls["sha256"], f"{tls.get('issuer_org')} / {d['domain']}"))
            for san in tls.get("sans") or []:
                s = san.lstrip("*.")
                if s and s != d["domain"] and not s.endswith("." + d["domain"]) and "cloudflare" not in s:
                    add(_p("cert_san_peer", s, f"SAN certyfikatu {d['domain']}"))
        if rd.get("registrar"):
            add(_p("registrar", rd["registrar"], d["domain"]))
            wk = _week(rd.get("created"))
            if wk:
                add(_p("registration_batch", f"{rd['registrar']}|{d.get('tld')}|{wk}", f"{d['domain']} utw. {rd['created']}"))
        if rd.get("registrant_org") and not rd.get("privacy_proxy"):
            add(_p("registrant_org", rd["registrant_org"], d["domain"]))
        if d.get("tld"):
            add(_p("tld", d["tld"]))
    http = rec.get("http") or {}
    if http.get("server_stack"):
        add(_p("server_stack", " | ".join(http["server_stack"][:6])))
    if http.get("header_fingerprint"):
        add(_p("header_fingerprint", http["header_fingerprint"], ",".join(http.get("header_names", [])[:20])))
    if http.get("cdn"):
        add(_p("hosting_platform", http["cdn"], "CDN strony docelowej"))
    for c in http.get("distinct_cookies") or []:
        add(_p("cookie", c))
    for h in http.get("csp_hosts") or []:
        add(_p("csp_host", h))

    # ---- behavior
    traits = rec.get("kit_traits") or []
    for t in traits:
        add(_p("kit_trait", t))
    if len(traits) >= 3:
        add(_p("kit_trait_set", short_hash(",".join(traits)), ",".join(traits)))
    libs = assets.get("libs") or []
    if len(libs) >= 2:
        add(_p("lib_set", short_hash(",".join(sorted(libs))), ",".join(sorted(libs))))
    if html.get("generator"):
        add(_p("generator", html["generator"]))
    if html.get("lang"):
        add(_p("lang", html["lang"].lower()[:5]))

    # deduplikacja (kind, value) – zachowujemy pierwszy dowód
    seen, out = set(), []
    for p in P:
        k = (p["kind"], p["value"])
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _week(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    except ValueError:
        return None
