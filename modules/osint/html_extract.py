"""Ekstrakcja śladów z dokumentu HTML: skrypty, style, formularze, linki wychodzące,
identyfikatory trackerów, kontakty, przekierowania klienckie, szkielet DOM, cechy kitu."""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from . import trackers
from .utils import (dedup, host_of, is_commodity, normalize_code, path_template, short_hash,
                    simhash64)

_PHONE = re.compile(r"(?<![\w+])(?:\+|00)(?:48|44|49|1|380|420|421|370|371|372|40|36|43|33|34|39|31|32|41|45|46|47|358)"
                    r"[\s.-]?\(?\d{2,3}\)?[\s.-]?\d{3}[\s.-]?\d{2,3}[\s.-]?\d{0,3}(?!\d)")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,12}\b")
_WALLETS = [
    ("btc", re.compile(r"\b(bc1[a-z0-9]{25,59}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")),
    ("eth", re.compile(r"\b(0x[a-fA-F0-9]{40})\b")),
    ("trx", re.compile(r"\b(T[A-Za-z1-9]{33})\b")),
]
_MESSENGER = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me|wa\.me|api\.whatsapp\.com|chat\.whatsapp\.com|"
                        r"viber\.click|invite\.viber\.com|signal\.me)/[^\s\"'<>]+", re.I)
_META_REFRESH = re.compile(r"url\s*=\s*['\"]?([^'\";\s]+)", re.I)
_JS_REDIRECT = re.compile(
    r"(?:window\.|document\.|top\.|self\.)?location(?:\.href)?\s*=\s*['\"](https?://[^'\"]+|/[^'\"]*)['\"]"
    r"|location\.(?:replace|assign)\(\s*['\"](https?://[^'\"]+|/[^'\"]*)['\"]", re.I)
_LIB = re.compile(r"(jquery|swiper|bootstrap|lazysizes|embla-carousel|hls|video|plyr|slick|owl\.carousel|"
                  r"font-awesome|gsap|axios|vue|react|alpinejs|lottie|aos|fancybox|glide|splide|intl-tel-input|"
                  r"imask|inputmask|sweetalert2?)(?:[@/-]|\.min[@/-]?|/dist/)?v?(\d+(?:\.\d+){0,2})", re.I)
_OBF_CLASS = re.compile(r"^[a-z]?[0-9a-f]{6,8}$")
_SAVED_REF = re.compile(r"_files/([^/?#]+)$")


def _resolve(base: str | None, ref: str) -> str:
    ref = (ref or "").strip()
    m = _SAVED_REF.search(ref)
    if m and not ref.startswith("http"):
        name = re.sub(r"\.(pobrane|download)$", "", m.group(1), flags=re.I)
        return f"saved:///{name}"
    if ref.startswith("//"):
        return "https:" + ref
    if base and base.startswith("http"):
        try:
            return urljoin(base, ref)
        except ValueError:
            return ref
    return ref


def extract_html(html: str, url: str | None) -> dict:
    soup = BeautifulSoup(html, "lxml")
    base_tag = soup.find("base", href=True)
    base = _resolve(url, base_tag["href"]) if base_tag else url
    page_host = host_of(url or "")

    def meta(**kw):
        t = soup.find("meta", attrs=kw)
        return (t.get("content") or "").strip() if t else None

    out: dict = {
        "url": url,
        "title": (soup.title.get_text(" ", strip=True)[:300] if soup.title else None),
        "lang": (soup.html.get("lang") if soup.html else None),
        "description": meta(name="description"),
        "generator": meta(name="generator"),
        "og": {k: meta(property=f"og:{k}") for k in ("title", "site_name", "url", "image", "type")},
        "canonical": None,
    }
    out["og"] = {k: v for k, v in out["og"].items() if v}
    can = soup.find("link", rel=lambda r: r and "canonical" in r)
    out["canonical"] = can.get("href") if can else None

    # ---- skrypty
    scripts, inline_hashes, inline_texts = [], [], []
    for s in soup.find_all("script"):
        if s.get("src"):
            scripts.append(_resolve(base, s["src"]))
        else:
            txt = s.string or s.get_text() or ""
            if txt.strip():
                inline_texts.append(txt)
                norm = normalize_code(txt)
                if len(norm) >= 200 and not norm.startswith("{\"@context"):
                    inline_hashes.append(short_hash(norm, 20))
    out["scripts"] = dedup(scripts)
    out["inline_script_hashes"] = dedup(inline_hashes)

    # ---- style / ikony / iframe
    css, icons = [], []
    for ln in soup.find_all("link", href=True):
        rel = " ".join(ln.get("rel") or []).lower()
        if "stylesheet" in rel:
            css.append(_resolve(base, ln["href"]))
        elif "icon" in rel:
            icons.append(_resolve(base, ln["href"]))
    out["stylesheets"] = dedup(css)
    out["favicon_refs"] = dedup(icons)
    out["iframes"] = dedup(_resolve(base, f["src"]) for f in soup.find_all("iframe", src=True))

    # ---- formularze (szablon akcji + nazwy pól – identyfikują backend zbierający leady)
    forms = []
    for f in soup.find_all("form"):
        action = _resolve(base, f.get("action") or "") if f.get("action") else ""
        fields = sorted({(i.get("name") or i.get("id") or i.get("type") or "?")[:40]
                         for i in f.find_all(["input", "select", "textarea"])})
        hidden = {i.get("name"): (i.get("value") or "")[:80]
                  for i in f.find_all("input", attrs={"type": "hidden"}) if i.get("name")}
        a_host = host_of(action) if action.startswith("http") else ""
        forms.append({
            "action": action or None,
            "action_host": a_host or None,
            "action_template": path_template(urlsplit(action).path) if action.startswith("http") else (f.get("action") or None),
            "method": (f.get("method") or "GET").upper(),
            "fields": fields,
            "hidden": hidden,
        })
    out["forms"] = forms

    # ---- linki wychodzące / CTA
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    abs_links = [_resolve(base, h) for h in hrefs if not h.startswith(("#", "javascript:", "mailto:", "tel:"))]
    out_hosts = Counter(host_of(h) for h in abs_links if h.startswith("http"))
    out["outbound_hosts"] = sorted(h for h in out_hosts if h and h != page_host)
    out["actor_outbound_hosts"] = [h for h in out["outbound_hosts"] if not is_commodity(h)]
    out["link_count"] = len(hrefs)
    out["self_link_ratio"] = round(sum(1 for h in hrefs if h.startswith("#") or h in ("", "/")) / len(hrefs), 3) if hrefs else None

    # ---- kontakty i portfele
    text = soup.get_text(" ", strip=True)
    tel = [h[4:] for h in hrefs if h.startswith("tel:")]
    out["phones"] = dedup(re.sub(r"[^\d+]", "", p) for p in _PHONE.findall(text) + tel)[:20]
    mails = [h[7:].split("?")[0] for h in hrefs if h.startswith("mailto:")] + _EMAIL.findall(text)
    out["emails"] = dedup(m.lower() for m in mails if not re.search(r"\.(png|jpe?g|webp|gif|svg)$", m, re.I))[:20]
    out["messengers"] = dedup(re.sub(r"^https?://(www\.)?", "", m.lower()).rstrip("/.")
                              for m in _MESSENGER.findall(html))[:20]
    wallets = []
    for kind, rx in _WALLETS:
        for w in rx.findall(text):
            if kind == "btc" and not (w.startswith("bc1") or len(w) >= 30):
                continue
            wallets.append(f"{kind}:{w}")
    out["crypto_wallets"] = dedup(wallets)[:20]

    # ---- identyfikatory trackerów w treści
    out["tracker_ids"] = dedup(trackers.ids_from_text(html))

    # ---- przekierowania po stronie klienta
    client_redirects = []
    for m in soup.find_all("meta", attrs={"http-equiv": re.compile("refresh", re.I)}):
        mm = _META_REFRESH.search(m.get("content") or "")
        if mm:
            client_redirects.append({"type": "meta_refresh", "url": _resolve(base, mm.group(1))})
    for txt in inline_texts:
        for mm in _JS_REDIRECT.finditer(txt):
            client_redirects.append({"type": "js", "url": _resolve(base, mm.group(1) or mm.group(2))})
    out["client_redirects"] = dedup(client_redirects)[:10]

    # ---- szkielet DOM (simhash) – podobny szablon = mała odległość Hamminga
    tags = [t for t in soup.find_all(True)]
    seq = [t.name for t in tags]
    shingles = [" ".join(seq[i:i + 4]) for i in range(max(0, len(seq) - 3))]
    shingles += [f"{t.parent.name}>{t.name}[{','.join(sorted(t.attrs.keys()))}]" for t in tags if t.parent]
    out["dom_simhash"] = simhash64(shingles)
    out["dom_tag_count"] = len(tags)

    # ---- klasy CSS (wykrycie zaciemniania nazw klas)
    classes = [c for t in tags for c in (t.get("class") or [])]
    obf = sum(1 for c in classes if _OBF_CLASS.match(c))
    out["css_class_count"] = len(classes)
    out["obfuscated_class_ratio"] = round(obf / len(classes), 3) if classes else 0.0
    out["html_style"] = (soup.html.get("style") if soup.html else None)
    out["_inline_js"] = inline_texts  # do analizy cech kitu, usuwane z rekordu
    return out


def libs_from_urls(urls: list[str]) -> list[str]:
    libs = []
    for u in urls:
        p = urlsplit(u).path.lower() if u.startswith("http") else u.lower()
        m = _LIB.search(p)
        if m:
            libs.append(f"{m.group(1)}@{m.group(2)}")
        elif "jquery" in p:
            libs.append("jquery")
    return dedup(libs)


# --------------------------------------------------------------------------- cechy kitu
# Zachowania typowe dla zestawów (kitów) stron scamowych. Pojedynczo słabe, ale ich
# kombinacja dobrze odróżnia rodziny szablonów. Każda cecha = (nazwa, regex, opis).
KIT_TRAITS: list[tuple[str, re.Pattern, str]] = [
    ("hue_rotate_evasion", re.compile(r"filter\s*:\s*hue-rotate\(\s*\d+deg\)", re.I),
     "losowy hue-rotate na <html> – psuje hashe percepcyjne zrzutów"),
    ("back_button_hijack", re.compile(r"history\.pushState[\s\S]{0,2000}popstate|popstate[\s\S]{0,2000}history\.pushState", re.I),
     "przechwycenie przycisku 'wstecz'"),
    ("context_menu_block", re.compile(r"oncontextmenu\s*=\s*['\"]?\s*return\s+false|contextmenu['\"]\s*,\s*\w*\s*=>?\s*\{?\s*\w+\.preventDefault", re.I),
     "blokada prawego przycisku"),
    ("devtools_detection", re.compile(r"devtools|setInterval\(\s*function\s*\(\)\s*\{\s*debugger|keyCode\s*==+\s*123", re.I),
     "wykrywanie narzędzi deweloperskich"),
    ("webdriver_check", re.compile(r"navigator\.webdriver", re.I), "wykrywanie botów/automatyzacji (cloaking)"),
    ("geo_ip_lookup", re.compile(r"ipapi\.co|ip-api\.com|ipinfo\.io|geoplugin|ipwho\.is|geolocation-db|ipgeolocation|freegeoip|api\.country\.is", re.I),
     "personalizacja po geolokalizacji IP"),
    ("dynamic_date", re.compile(r"toLocaleDateString\(\s*['\"]pl|getDate\(\)\s*-\s*\d|new Date\(\)[^;]{0,80}setDate", re.I),
     "dynamiczna 'dzisiejsza' data w artykule"),
    ("countdown_timer", re.compile(r"countdown|count-down|timer__|\bminutes\b[\s\S]{0,200}\bseconds\b", re.I),
     "licznik presji czasu"),
    ("fake_comments", re.compile(r"comments?App|fb-comments-fake|comment__(?:avatar|author)|class=['\"][^'\"]*comment-item", re.I),
     "wbudowane fałszywe komentarze"),
    ("lead_form_php", re.compile(r"<form[^>]+action=['\"][^'\"]*\.php", re.I), "formularz leadowy do skryptu PHP"),
    ("service_worker", re.compile(r"serviceWorker\.register", re.I), "rejestracja service workera"),
    ("hls_video", re.compile(r"\.m3u8|hls\.js|Hls\.isSupported", re.I), "wideo HLS (fałszywy materiał TV)"),
    ("exchange_rate_api", re.compile(r"er-api\.com|exchangerate|currencyapi", re.I), "pobieranie kursów walut (inwestycje)"),
    ("phone_mask_input", re.compile(r"intl-tel-input|intlTelInput|imask|inputmask", re.I), "maska numeru telefonu"),
    ("google_translate_widget", re.compile(r"translate\.google\.com/translate_a/element|google_translate_element", re.I),
     "widżet Google Translate"),
    ("fake_app_store", re.compile(r"ios-app\.js|app-store|apps\.apple\.com|appstore", re.I), "imitacja App Store / PWA"),
    ("exit_popup", re.compile(r"mouseleave|exit-?intent|beforeunload", re.I), "popup przy próbie wyjścia"),
    ("template_placeholder_leak", re.compile(r"\{\{\s*[a-z_]+\s*\}\}|%[A-Z_]{3,}%|\[\[[a-z_]+\]\]"), "niewypełnione zmienne szablonu"),
    ("base64_obfuscation", re.compile(r"atob\(\s*['\"][A-Za-z0-9+/=]{80,}"), "zaciemniony JS (atob)"),
]


def kit_traits(texts: list[str], obfuscated_class_ratio: float = 0.0) -> list[str]:
    blob = "\n".join(t[:400_000] for t in texts)
    found = [name for name, rx, _ in KIT_TRAITS if rx.search(blob)]
    if obfuscated_class_ratio >= 0.3:
        found.append("obfuscated_css_classes")
    return sorted(set(found))


TRAIT_DESCRIPTIONS = {n: d for n, _, d in KIT_TRAITS} | {
    "obfuscated_css_classes": "losowe/hashowane nazwy klas CSS (utrudnia porównanie szablonów)"}
