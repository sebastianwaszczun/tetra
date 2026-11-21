"""Ekstrakcja śladów sieciowych z zasobów (HAR / MHTML / live): łańcuch przekierowań,
hosty i ich role, IP, nagłówki HTTP, ciasteczka, endpointy API, zasoby kitu, trackery."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from . import trackers
from .capture import Capture, Resource
from .html_extract import _LIB, libs_from_urls
from .utils import (cdn_of_ip, dedup, host_of, is_commodity, is_id_like, normalize_code,
                    path_template, query_schema, sha256, short_hash, split_host)

# Nagłówki zmienne per-żądanie – nie wchodzą do odcisku zestawu nagłówków.
_VOLATILE_HEADERS = {"date", "cf-ray", "content-length", "age", "expires", "last-modified", "etag",
                     "x-request-id", "x-amz-cf-id", "x-served-by", "x-cache", "x-cache-hits",
                     "x-timer", "set-cookie", "report-to", "nel", "server-timing", "cdn-requestid",
                     "x-nf-request-id", "x-vercel-id", "cf-cache-status", "alt-svc", "priority",
                     "content-encoding", "vary", "accept-ranges", "connection", "keep-alive",
                     "transfer-encoding", "status", "x-fastly-request-id", "cdn-cache", "cdn-cachedat",
                     "cdn-edgestorageid", "cdn-proxyver", "cdn-pullzone", "cdn-requestcountrycode",
                     "cdn-requestpullcode", "cdn-requestpullsuccess", "cdn-status", "cdn-uid"}
_SECURITY_HEADERS = ("content-security-policy", "strict-transport-security", "x-frame-options",
                     "x-content-type-options", "referrer-policy", "permissions-policy")
_GENERIC_COOKIES = {"__cf_bm", "cf_clearance", "_cfuvid", "__cfduid", "phpsessid", "_ga", "_gid", "_fbp",
                    "_fbc", "_gcl_au", "nid", "ide", "fr", "datr", "sb", "test_cookie", "aec", "1p_jar"}
_DISTINCT_SLUG = re.compile(r"^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+){2,}$")
_API_TYPES = {"xhr", "fetch", "ping", "beacon", "eventsource", "websocket"}
_STATIC_TYPES = {"script", "stylesheet", "image", "font", "media", "manifest"}
_MEDIA_EXT = re.compile(r"\.(ts|m3u8|mp4|m4s|webm|mp3|aac|m4a|mpd)$", re.I)


def _qname(n: str) -> str:
    """event8 / event12 -> event{n}: numerowane parametry beaconów nie mnożą sygnatur."""
    return re.sub(r"\d+$", "{n}", n)


def _cdn_from_headers(r: Resource) -> str | None:
    h = {k: v for k, v in r.headers}
    srv = (h.get("server") or "").lower()
    if "cf-ray" in h or srv == "cloudflare":
        return "cloudflare"
    if "x-amz-cf-id" in h or "cloudfront" in (h.get("via") or "").lower():
        return "cloudfront"
    if srv.startswith("bunnycdn") or "cdn-pullzone" in h:
        return "bunnycdn"
    if "x-nf-request-id" in h or srv == "netlify":
        return "netlify"
    if "x-vercel-id" in h:
        return "vercel"
    if "fastly" in (h.get("x-served-by") or "").lower() or "x-fastly-request-id" in h:
        return "fastly"
    if "akamai" in srv or "x-akamai-transformed" in h:
        return "akamai"
    if "fly.io" in (h.get("via") or "") or "fly-request-id" in h:
        return "fly.io"
    return None


def _distinct_prefix(path: str) -> str | None:
    """Najdłuższy prefiks katalogu kończący się "charakterystycznym" segmentem (uuid, id,
    długi slug). Taki prefiks to zwykle identyfikator konkretnego wdrożenia kitu."""
    segs = path.split("/")[:-1]
    last = -1
    for i, s in enumerate(segs):
        if is_id_like(s) or (len(s) >= 12 and _DISTINCT_SLUG.match(s)):
            last = i
    return "/".join(segs[: last + 1]) if last > 0 else None


def _family_prefix(path: str) -> str | None:
    """Prefiks katalogu do pierwszego identyfikatora – "rodzina" platformy kitu
    (np. /_cdn/production/landing-cdn/), wspólna dla różnych wdrożeń."""
    segs = path.split("/")[:-1]
    for i, s in enumerate(segs):
        if is_id_like(s):
            return "/".join(segs[:i]) + "/" if i >= 2 else None
    return None


def extract_network(cap: Capture, landing_hosts: set[str]) -> dict:
    landing_regs = {split_host(h)["registered_domain"] for h in landing_hosts}
    res = [r for r in cap.resources if r.url.startswith("http")]

    def role(host: str) -> str:
        if "." not in host:
            return "placeholder"        # np. https://delivered/?id= – niewypełniona zmienna kitu
        if host in landing_hosts:
            return "landing"
        if split_host(host)["registered_domain"] in landing_regs:
            return "first_party"
        if is_commodity(host):
            return "commodity"
        return "actor_third_party"

    # ---- hosty, IP
    host_stats: dict[str, dict] = {}
    for r in res:
        h = host_of(r.url)
        if not h:
            continue
        st = host_stats.setdefault(h, {"host": h, "role": role(h), "requests": 0, "ips": set(), "cdn": None})
        st["requests"] += 1
        if r.ip:
            st["ips"].add(r.ip)
        st["cdn"] = st["cdn"] or _cdn_from_headers(r) or (cdn_of_ip(r.ip) if r.ip else None)
    hosts = []
    for st in sorted(host_stats.values(), key=lambda s: (-s["requests"], s["host"])):
        sh = split_host(st["host"])
        hosts.append({**st, "ips": sorted(st["ips"]), "registered_domain": sh["registered_domain"],
                      "platform": sh["platform"]})

    # ---- łańcuch przekierowań (dokumenty + 3xx)
    chain = []
    for r in sorted(res, key=lambda x: x.started or ""):
        if r.source == "live":
            continue                 # tryb live ma własny, dokładniejszy zapis skoków (live_chain)
        is_doc = r.resource_type == "document" or (not r.resource_type and "html" in r.mime)
        is_redirect = r.status in (301, 302, 303, 307, 308)
        if is_doc or (is_redirect and role(host_of(r.url)) != "commodity"):
            loc = r.redirect_url or r.header("location")
            chain.append({"url": r.url, "host": host_of(r.url), "status": r.status,
                          "type": "http_redirect" if is_redirect else "document",
                          "location": loc, "ip": r.ip, "source": r.source})
    chain = cap.live_chain + chain

    # ---- odcisk HTTP głównego dokumentu
    main = next((r for r in res if host_of(r.url) in landing_hosts and r.resource_type == "document"
                 and r.status and r.status < 400 and r.headers), None) \
        or next((r for r in res if host_of(r.url) in landing_hosts and r.headers), None)
    http = {}
    if main:
        hd = dict(main.headers)
        names = sorted({k for k, _ in main.headers if k not in _VOLATILE_HEADERS and not k.startswith(":")})
        csp = hd.get("content-security-policy") or ""
        http = {
            "status": main.status,
            "server": hd.get("server"),
            "x_powered_by": hd.get("x-powered-by"),
            "via": hd.get("via"),
            "cdn": _cdn_from_headers(main) or (cdn_of_ip(main.ip) if main.ip else None),
            "cdn_pop": (hd.get("cf-ray") or "").rsplit("-", 1)[-1] or None,
            "content_type": hd.get("content-type"),
            "cache_control": hd.get("cache-control"),
            "header_names": names,
            "header_fingerprint": short_hash("|".join(names)),
            "security_headers": [h for h in _SECURITY_HEADERS if h in hd],
            "csp_hosts": sorted({x for x in re.findall(r"https?://([a-z0-9.*-]+)", csp)}),
            "expires_header": hd.get("expires"),
        }
    stacks = Counter()
    for r in res:
        if role(host_of(r.url)) in ("landing", "first_party", "actor_third_party"):
            hd = dict(r.headers)
            for k in ("server", "x-powered-by", "via"):
                if hd.get(k):
                    stacks[f"{k}: {hd[k]}"] += 1
    http["server_stack"] = sorted(stacks)

    # ---- ciasteczka
    cookies = sorted({c for r in res for c in r.cookies if c and c != "[REDACTED]"})
    http["cookies"] = cookies
    http["distinct_cookies"] = [c for c in cookies if c.lower() not in _GENERIC_COOKIES]

    # ---- endpointy API / beacony (aktor, nie Google/Meta)
    endpoints = {}
    for r in res:
        h = host_of(r.url)
        rl = role(h)
        if rl == "commodity":
            continue
        sp = urlsplit(r.url)
        if r.resource_type == "media" or _MEDIA_EXT.search(sp.path) or r.mime.startswith(("video/", "audio/")):
            continue
        is_api = (r.method != "GET" or r.resource_type in _API_TYPES
                  or "json" in r.mime or "/api/" in sp.path)
        is_beacon = r.resource_type in ("image", "other", "ping") and sp.query and (r.size or 0) < 200 \
            and rl == "actor_third_party"
        if not (is_api or is_beacon):
            continue
        qnames = sorted({_qname(n) for n in query_schema(sp.query)[0]})
        tpl = path_template(sp.path)
        key = (r.method, rl, tpl, tuple(qnames))
        e = endpoints.setdefault(key, {"method": r.method, "host_role": rl, "hosts": set(),
                                       "path_template": tpl, "query_params": qnames,
                                       "statuses": set(), "kind": "beacon" if is_beacon and not is_api else "api"})
        e["hosts"].add(h)
        if r.status is not None:
            e["statuses"].add(r.status)
    endpoints_out = [{**e, "hosts": sorted(e["hosts"]), "statuses": sorted(e["statuses"])} for e in endpoints.values()]

    # ---- zasoby: skrypty, style, obrazy, katalogi kitu
    scripts, styles, images = [], [], []
    kit_dirs, families, layout = Counter(), Counter(), Counter()
    favicon = None
    for r in res + [x for x in cap.resources if x.url.startswith("saved:")]:
        h = host_of(r.url) if r.url.startswith("http") else ""
        rl = role(h) if h else "saved_page"
        sp = urlsplit(r.url)
        fname = sp.path.rsplit("/", 1)[-1].lower()
        rtype = r.resource_type or ("script" if fname.endswith(".js") else "stylesheet" if fname.endswith(".css") else "")
        if "favicon" in fname or fname.endswith(".ico") or "icon" in (r.mime or ""):
            if r.body and not favicon:
                favicon = r.body
        static = rtype in _STATIC_TYPES or r.resource_type in _STATIC_TYPES or r.url.startswith("saved:")
        if rl in ("landing", "first_party") and static:
            d = _distinct_prefix(sp.path)
            if d:
                kit_dirs[d] += 1
            fp = _family_prefix(sp.path)
            if fp:
                families[fp] += 1
            dir_tpl = path_template(sp.path.rsplit("/", 1)[0] or "/")
            if "{" in dir_tpl or dir_tpl.count("/") >= 2:
                layout[dir_tpl] += 1
        if rtype in ("script", "stylesheet"):
            is_vendor = rl == "commodity" or bool(_LIB.search(fname)) or ".min." in fname and rl != "landing"
            norm_hash = None
            if r.body:
                norm_hash = sha256(normalize_code(r.body.decode("utf-8", "replace")).encode())[:32]
            item = {"url": r.url if r.url.startswith("http") else r.url.replace("saved:///", ""),
                    "host": h or None, "role": rl, "vendor": is_vendor,
                    "sha256": (r.body_sha256 or "")[:32] or None, "norm_sha256": norm_hash, "size": r.size,
                    "path_template": path_template(sp.path) if h else None}
            (scripts if rtype == "script" else styles).append(item)
        elif rtype == "image" and rl in ("landing", "first_party", "actor_third_party", "saved_page") and r.body_sha256:
            if (r.size or 0) >= 1024:         # pomijamy piksele 1x1 i ikonki
                images.append({"name": fname[:80], "sha256": r.body_sha256[:32], "size": r.size, "role": rl})

    # ---- identyfikatory trackerów z URL-i żądań
    tracker_ids = []
    for r in res:
        for kind, val in trackers.ids_from_url(r.url):
            tracker_ids.append((kind, val))

    # ---- hosty mediów (m3u8 / wideo)
    media_hosts = sorted({host_of(r.url) for r in res if r.resource_type == "media" or ".m3u8" in r.url
                          or r.mime.startswith("video/")} | {host_of(s) for s in cap.streams})

    return {
        "hosts": hosts,
        "chain": dedup(chain),
        "http": http,
        "endpoints": endpoints_out,
        "scripts": dedup(scripts),
        "stylesheets": dedup(styles),
        "images": dedup(images)[:200],
        "kit_dirs": [d for d, n in kit_dirs.most_common(10) if n >= 2],
        "kit_families": [d for d, n in families.most_common(5) if n >= 2],
        "path_layout": sorted(layout)[:40],
        "libs": libs_from_urls([r.url for r in res if r.resource_type == "script" or r.url.endswith(".js")]),
        "tracker_ids": dedup(tracker_ids),
        "media_hosts": [m for m in media_hosts if m],
        "favicon_bytes": favicon,
        "first_party_bodies": [r.body.decode("utf-8", "replace") for r in res
                               if r.body and role(host_of(r.url)) in ("landing", "first_party", "actor_third_party")
                               and (r.resource_type in ("script", "document") or "javascript" in r.mime)][:40],
        "ip_hosts": _ip_hosts(res),
    }


def _ip_hosts(res: list[Resource]) -> dict[str, list[str]]:
    m: dict[str, set] = defaultdict(set)
    for r in res:
        if r.ip:
            m[r.ip].add(host_of(r.url))
    return {ip: sorted(h) for ip, h in m.items()}
