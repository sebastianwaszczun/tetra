"""Orkiestracja: wejście -> Capture -> ekstrakcja -> wzbogacenie -> rekord + pivoty."""
from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from urllib.parse import parse_qsl, unquote, urlsplit

from . import config, enrich
from .capture import Capture
from .html_extract import TRAIT_DESCRIPTIONS, extract_html, kit_traits, libs_from_urls
from .network_extract import extract_network
from .pivots import build_pivots
from .schema import normalize
from .trackers import SECRET_PARAMS, analyze_params
from .utils import (cdn_of_ip, dedup, entropy, host_of, is_commodity, is_ip, query_schema,
                    split_host, uuid7_timestamp)

SCHEMA_VERSION = "osint-record/1.0"
_DOC_PRIORITY = {"dom": 0, "har": 1, "mhtml": 2, "saved_html": 3, "live": 4}
_LIST_FIELDS = ("scripts", "stylesheets", "favicon_refs", "iframes", "outbound_hosts", "actor_outbound_hosts",
                "phones", "emails", "messengers", "crypto_wallets", "tracker_ids", "client_redirects",
                "inline_script_hashes")


def _landing_hosts(cap: Capture) -> set[str]:
    hosts = set()
    if cap.page_url:
        hosts.add(host_of(cap.page_url))
    for d in cap.documents:
        if d.url and d.url.startswith("http"):
            hosts.add(host_of(d.url))
    for hop in cap.live_chain:
        for u in (hop.get("url"), hop.get("location")):
            if u and u.startswith("http"):
                hosts.add(host_of(u))
    for r in cap.resources:
        if r.resource_type == "document" and r.status and r.status < 400 and r.url.startswith("http"):
            hosts.add(host_of(r.url))
    return {h for h in hosts if h and not is_commodity(h)}


def _merge_html(cap: Capture) -> tuple[dict, list[str], list[dict]]:
    docs = sorted(cap.documents, key=lambda d: (_DOC_PRIORITY.get(d.kind, 9), -len(d.html)))[:6]
    extracts = []
    for d in docs:
        try:
            ex = extract_html(d.html, d.url or cap.page_url)
            ex["_source"] = d.source
            extracts.append(ex)
        except Exception as e:  # noqa: BLE001
            cap.notes.append(f"błąd parsowania HTML {d.source}: {type(e).__name__}")
    if not extracts:
        return {}, [], []
    main = dict(extracts[0])
    for ex in extracts[1:]:
        for f in _LIST_FIELDS:
            main[f] = dedup(list(main.get(f) or []) + list(ex.get(f) or []))
        main["forms"] = dedup((main.get("forms") or []) + (ex.get("forms") or []))
        for k in ("title", "lang", "description", "generator", "canonical"):
            main[k] = main.get(k) or ex.get(k)
        main["obfuscated_class_ratio"] = max(main.get("obfuscated_class_ratio", 0), ex.get("obfuscated_class_ratio", 0))
        if ex.get("html_style") and not main.get("html_style"):
            main["html_style"] = ex["html_style"]
    texts = [d.html for d in docs]
    for ex in extracts:
        texts += ex.pop("_inline_js", [])
    main.pop("_inline_js", None)
    main["sources"] = [ex.pop("_source") for ex in extracts]
    main.pop("_source", None)
    variants = [{"source": s, "dom_simhash": ex["dom_simhash"], "title": ex.get("title")}
                for s, ex in zip(main["sources"], extracts)]
    return main, texts, variants


def _url_params(url: str | None) -> dict:
    if not url:
        return {}
    sp = urlsplit(url)
    names, schema = query_schema(sp.query)
    info = analyze_params(url)
    secret_hashes = []
    for k, v in parse_qsl(sp.query, keep_blank_values=True):
        v = unquote(v)
        if k.lower() in SECRET_PARAMS or v.startswith("EAA"):
            secret_hashes.append("sha256:" + hashlib.sha256(v.encode()).hexdigest()[:24])
    ts = {k: uuid7_timestamp(v) for k, v in parse_qsl(sp.query) if uuid7_timestamp(v)}
    return {
        "path": sp.path, "param_names": names, "param_count": len(names), "schema": schema,
        **{k: v for k, v in info.items() if k != "secret_params"},
        "secret_params_redacted": info.get("secret_params", []), "secret_hashes": secret_hashes,
        "uuid7_timestamps": ts,
        "naming_scripts": sorted({"cyrillic" if any("\u0400" <= ch <= "\u04ff" for ch in v) else "latin"
                                  for v in (info.get("naming") or {}).values()}),
    }


def _lexical(domain: str) -> dict:
    label = domain.split(".")[0]
    vowels = sum(c in "aeiouy" for c in label)
    return {"label": label, "length": len(label), "entropy": round(entropy(label), 3),
            "digits": sum(c.isdigit() for c in label), "hyphens": label.count("-"),
            "vowel_ratio": round(vowels / len(label), 3) if label else 0}


def analyze_capture(cap: Capture, do_enrich: bool = True, do_ct: bool | None = None) -> dict:
    t0 = time.time()
    do_ct = config.ENABLE_CT if do_ct is None else do_ct
    landing_hosts = _landing_hosts(cap)
    html, texts, variants = _merge_html(cap)
    if not landing_hosts:
        for u in (html.get("canonical"), (html.get("og") or {}).get("url")):
            if u and u.startswith("http") and not is_commodity(host_of(u)):
                landing_hosts.add(host_of(u))
                cap.page_url = cap.page_url or u
    landing_inferred = False
    if not landing_hosts:
        cand = [host_of(r.url) for r in cap.resources if r.url.startswith("http")
                and (r.resource_type == "document" or "html" in r.mime or r.resource_type == "other")]
        cand = [h for h in cand if h and "." in h and not is_commodity(h)]
        if cand:
            landing_hosts.add(cand[0])
            landing_inferred = True
            cap.notes.append(f"brak URL strony – landing wywnioskowany z ruchu: {cand[0]}")
    net = extract_network(cap, landing_hosts)

    tr: dict[tuple, set] = {}
    for kind, val in html.get("tracker_ids") or []:
        tr.setdefault((kind, val), set()).add("html")
    for kind, val in net["tracker_ids"]:
        tr.setdefault((kind, val), set()).add("network")
    for text in net["first_party_bodies"]:
        from .trackers import ids_from_text
        for kind, val in ids_from_text(text):
            tr.setdefault((kind, val), set()).add("first_party_js")
    params = _url_params(cap.page_url)
    for kind, vals in (params.get("ad_ids") or {}).items():
        if kind == "fb_pixel":
            for v in vals:
                tr.setdefault(("fb_pixel", v), set()).add("url_param")
    params["ad_ids"] = {k: v for k, v in (params.get("ad_ids") or {}).items() if k != "fb_pixel"}
    trackers_out = [{"type": k, "id": v, "sources": sorted(s)} for (k, v), s in sorted(tr.items())]

    traits = kit_traits(texts + net["first_party_bodies"], html.get("obfuscated_class_ratio", 0.0))
    if (html.get("html_style") or "").lower().find("hue-rotate") >= 0 and "hue_rotate_evasion" not in traits:
        traits.append("hue_rotate_evasion")
    if any("{{" in (e.get("path_template") or "") for e in net["endpoints"]) or any(
            h["host"] in ("delivered",) or "." not in h["host"] for h in net["hosts"]):
        traits.append("template_placeholder_leak")
    traits = sorted(set(traits))

    observed = cap.observed_at or datetime.now(timezone.utc).isoformat()
    chain_hosts = [h["host"] for h in net["chain"] if h.get("host") and not is_commodity(h["host"])]
    actor_hosts = [h["host"] for h in net["hosts"] if h["role"] == "actor_third_party"]
    roles: dict[str, str] = {}
    for h in sorted(landing_hosts):
        roles[h] = "landing"
    for h in chain_hosts:
        roles.setdefault(h, "redirect")
    for h in actor_hosts + (html.get("actor_outbound_hosts") or []):
        roles.setdefault(h, "actor_third_party")
    roles = {h: r for h, r in roles.items() if h and "." in h and not is_ip(h)}
    enrich_hosts = list(roles)[: config.MAX_ENRICH_DOMAINS]

    domains, ips, missing = [], [], []
    if do_enrich and enrich_hosts:
        domains, ips, fav, missing = _enrich(enrich_hosts, roles, net, html, observed, do_ct, bool(cap.observed_at))
        if fav and not net.get("favicon"):
            net["favicon"] = fav
    else:
        for h in enrich_hosts:
            sh = split_host(h)
            domains.append({"domain": h, **{k: sh[k] for k in ("registered_domain", "icann_domain", "tld", "platform")},
                            "role": roles[h], "lexical": _lexical(sh["registered_domain"])})
        for ip, hs in net["ip_hosts"].items():
            if any(roles.get(x) for x in hs):
                ips.append({"ip": ip, "hosts": hs, "cdn": cdn_of_ip(ip)})
        if not do_enrich:
            missing.append("wzbogacanie wyłączone (enrich=false)")
    if net.get("favicon_bytes") and not net.get("favicon"):
        net["favicon"] = {**enrich.favicon_hashes(net["favicon_bytes"]), "url": "z przechwyconego ruchu"}

    landing_regs = sorted({split_host(h)["registered_domain"] for h in landing_hosts})
    final_url = next((h.get("location") or h.get("url") for h in reversed(cap.live_chain)
                      if h.get("type") in ("final", "browser_navigation")), None)
    rec = {
        "schema_version": SCHEMA_VERSION,
        "record_id": _record_id(cap),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "observed_at": cap.observed_at,
        "inputs": cap.inputs,
        "landing": {
            "url": cap.page_url, "final_url": final_url, "hosts": sorted(landing_hosts),
            "registered_domains": landing_regs, "title": html.get("title") or cap.title, "lang": html.get("lang"),
            "inferred": landing_inferred,
        },
        "url_params": params,
        "redirect_chain": net["chain"],
        "domains": domains,
        "ips": ips,
        "http": net["http"],
        "hosts": net["hosts"],
        "trackers": trackers_out,
        "endpoints": net["endpoints"],
        "assets": {
            "kit_dirs": net["kit_dirs"], "kit_families": net["kit_families"], "path_layout": net["path_layout"],
            "scripts": net["scripts"], "stylesheets": net["stylesheets"], "images": net["images"],
            "libs": sorted(set(net["libs"]) | set(libs_from_urls(html.get("scripts") or []))),
            "favicon": net.get("favicon"), "media_hosts": net["media_hosts"],
        },
        "html": {k: v for k, v in html.items() if k not in ("tracker_ids",)},
        "html_variants": variants,
        "kit_traits": traits,
        "kit_trait_descriptions": {t: TRAIT_DESCRIPTIONS.get(t, "") for t in traits},
        "ad_library": cap.ad_library,
        "streams": cap.streams,
    }
    rec = normalize(rec)
    rec["pivots"] = build_pivots(rec)
    rec["quality"] = _quality(rec, cap, missing, time.time() - t0)
    return rec


def _enrich(hosts, roles, net, html, observed, do_ct, observed_known=True):
    missing: list[str] = []
    jobs = {}
    deadline = time.time() + config.ENRICH_BUDGET_S
    ex = ThreadPoolExecutor(max_workers=16)
    try:
        for h in hosts:
            sh = split_host(h)
            jobs[("dns", h)] = ex.submit(enrich.dns_lookup, h)
            if not sh["platform"]:
                jobs[("rdap", h)] = ex.submit(enrich.rdap_domain, sh["icann_domain"])
            if roles[h] in ("landing", "redirect"):
                jobs[("tls", h)] = ex.submit(enrich.tls_cert, h)
                if do_ct and not sh["platform"]:
                    jobs[("ct", h)] = ex.submit(enrich.crtsh, sh["icann_domain"])
        landing = [h for h in hosts if roles[h] == "landing"]
        if landing and not net.get("favicon_bytes"):
            jobs[("fav", landing[0])] = ex.submit(enrich.fetch_favicon, landing[0], html.get("favicon_refs") or [])
        wait(list(jobs.values()), timeout=max(1, deadline - time.time()))
        res = {}
        for k, f in jobs.items():
            if f.done():
                try:
                    res[k] = f.result()
                except Exception as e:  # noqa: BLE001
                    res[k] = {"error": f"{type(e).__name__}: {e}"[:200]}
            else:
                res[k] = {"error": "przekroczony budżet czasu wzbogacania"}

        ip_hosts: dict[str, set] = {}
        for ip, hs in net["ip_hosts"].items():
            if any(x in roles for x in hs):
                ip_hosts.setdefault(ip, set()).update(x for x in hs if x in roles)
        for h in hosts:
            d = res.get(("dns", h)) or {}
            for ip in (d.get("a") or []) + (d.get("aaaa") or []):
                ip_hosts.setdefault(ip, set()).add(h)
        ip_list = sorted(ip_hosts, key=lambda i: (cdn_of_ip(i) is not None, i))[: config.MAX_ENRICH_IPS]
        ip_jobs = {ip: ex.submit(enrich.ip_info, ip) for ip in ip_list if not cdn_of_ip(ip)}
        ptr_jobs = {ip: ex.submit(enrich.reverse_dns, ip) for ip in ip_list if not cdn_of_ip(ip)}
        wait(list(ip_jobs.values()) + list(ptr_jobs.values()), timeout=max(1, deadline - time.time()))
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    domains = []
    for h in hosts:
        sh = split_host(h)
        rd = res.get(("rdap", h))
        d = {
            "domain": h, "registered_domain": sh["registered_domain"], "icann_domain": sh["icann_domain"],
            "tld": sh["tld"], "platform": sh["platform"], "role": roles[h],
            "lexical": _lexical(sh["registered_domain"]),
            "dns": res.get(("dns", h)), "rdap": rd, "tls": res.get(("tls", h)), "ct": res.get(("ct", h)),
        }
        created = (rd or {}).get("created") or ((d["ct"] or {}).get("first_seen") if d["ct"] else None)
        d["age_days_at_observation"] = enrich.domain_age_days(created, observed)
        d["age_reference"] = "observed_at" if observed_known else "analysis_time"
        d["status_flags"] = enrich.domain_flags(d["dns"], rd, d["tls"], h)
        d["age_source"] = "rdap" if (rd or {}).get("created") else ("crt.sh" if created else None)
        for k in ("dns", "rdap", "tls", "ct"):
            v = d.get(k)
            if isinstance(v, dict) and v.get("error"):
                missing.append(f"{k} {h}: {v['error']}")
        if (d["dns"] or {}).get("nxdomain"):
            missing.append(f"dns {h}: NXDOMAIN (domena już nie istnieje)")
        domains.append(d)

    ips = []
    for ip in ip_list:
        cdn = cdn_of_ip(ip)
        info = {"ip": ip, "hosts": sorted(ip_hosts[ip]), "cdn": cdn}
        if cdn:
            info.update({"asn": "13335", "asn_name": "CLOUDFLARENET", "note": "adres CDN – współdzielony"})
        else:
            f = ip_jobs.get(ip)
            if f and f.done():
                try:
                    info.update({k: v for k, v in (f.result() or {}).items() if k != "ip"})
                except Exception as e:  # noqa: BLE001
                    info["error"] = str(e)[:200]
            pf = ptr_jobs.get(ip)
            info["ptr"] = pf.result() if pf and pf.done() else None
        if info.get("error"):
            missing.append(f"ip {ip}: {info['error']}")
        ips.append(info)

    fav = None
    for k, v in res.items():
        if k[0] == "fav" and isinstance(v, dict) and v.get("mmh3") is not None:
            fav = v
    return domains, ips, fav, missing


def _record_id(cap: Capture) -> str:
    keys = sorted(str(i.get("sha256") or i.get("name")) for i in cap.inputs) or [cap.page_url or ""]
    return hashlib.sha1("|".join(keys).encode()).hexdigest()[:20]


def _quality(rec, cap, missing, elapsed) -> dict:
    kinds = {i["kind"] for i in cap.inputs}
    doms = rec["domains"]
    checks = {
        "html": bool(rec["html"]),
        "network_capture": any(r.source.startswith(("har", "live", "mhtml")) for r in cap.resources),
        "redirect_info": bool(rec["redirect_chain"]),
        "landing_url": bool(rec["landing"]["url"]),
        "dns": any((d.get("dns") or {}) and not (d.get("dns") or {}).get("error") and (d.get("dns") or {}).get("a") for d in doms),
        "rdap": any((d.get("rdap") or {}).get("created") or (d.get("rdap") or {}).get("registrar") for d in doms),
        "asn": any(i.get("asn") for i in rec["ips"]),
        "tls": any((d.get("tls") or {}).get("sha256") for d in doms),
        "trackers": bool(rec["trackers"]),
    }
    strong = sum(1 for p in rec["pivots"] if p["weight"] >= 0.7)
    return {
        "input_kinds": sorted(kinds),
        "checks": checks,
        "completeness": round(sum(checks.values()) / len(checks), 2),
        "strong_pivots": strong,
        "total_pivots": len(rec["pivots"]),
        "missing": missing,
        "notes": cap.notes,
        "elapsed_s": round(elapsed, 2),
    }
