"""Stały kształt rekordu: każdy klucz jest zawsze obecny, niezależnie od typu wejścia.

Zasady:
  * brak wartości skalarnej          -> null
  * brak elementów listy             -> []
  * brak wpisów w mapie              -> {}
  * pod-obiekt sprawdzenia domeny (dns, rdap, tls, ct, lexical) = null, gdy sprawdzenia NIE wykonano
    (enrich=false, nie dotyczy); gdy wykonano, ma pełny zestaw pól, a `error` opisuje porażkę.
"""
from __future__ import annotations

import copy

LANDING = {"url": None, "final_url": None, "hosts": [], "registered_domains": [], "title": None,
           "lang": None, "inferred": False}

URL_PARAMS = {"path": None, "param_names": [], "param_count": 0, "schema": None, "ad_ids": {}, "naming": {},
              "click_ids": {}, "tds_families": [], "tds_ids": {}, "secret_params_redacted": [],
              "secret_hashes": [], "uuid7_timestamps": {}, "naming_scripts": []}

CHAIN_HOP = {"url": None, "host": None, "status": None, "type": None, "location": None, "ip": None,
             "source": None, "error": None}

DNS = {"a": [], "aaaa": [], "cname": [], "ns": [], "mx": [], "txt": [], "soa": None, "nxdomain": False,
       "errors": [], "error": None}
RDAP = {"source": None, "handle": None, "created": None, "updated": None, "expires": None, "status": [],
        "nameservers": [], "registrar": None, "registrar_iana_id": None, "abuse_email": None,
        "registrant_org": None, "registrant_country": None, "privacy_proxy": None,
        "rdap_error": None, "whois_error": None, "error": None}
TLS = {"subject_cn": None, "issuer_cn": None, "issuer_org": None, "not_before": None, "not_after": None,
       "serial": None, "sha256": None, "sans": [], "tls_version": None, "error": None}
CT = {"cert_count": None, "first_seen": None, "last_seen": None, "names": [], "issuers": [], "error": None}
LEXICAL = {"label": None, "length": None, "entropy": None, "digits": None, "hyphens": None, "vowel_ratio": None}

DOMAIN = {"domain": None, "registered_domain": None, "icann_domain": None, "tld": None, "platform": None,
          "role": None, "lexical": None, "dns": None, "rdap": None, "tls": None, "ct": None,
          "age_days_at_observation": None, "age_reference": None, "age_source": None, "status_flags": []}

IP = {"ip": None, "hosts": [], "cdn": None, "asn": None, "asn_name": None, "asn_country": None,
      "asn_cidr": None, "asn_registry": None, "network_name": None, "network_cidr": None,
      "network_country": None, "ptr": None, "note": None, "error": None}

HTTP = {"status": None, "server": None, "x_powered_by": None, "via": None, "cdn": None, "cdn_pop": None,
        "content_type": None, "cache_control": None, "expires_header": None, "header_names": [],
        "header_fingerprint": None, "security_headers": [], "csp_hosts": [], "server_stack": [],
        "cookies": [], "distinct_cookies": []}

FAVICON = {"mmh3": None, "md5": None, "size": None, "url": None, "error": None}
ASSETS = {"kit_dirs": [], "kit_families": [], "path_layout": [], "scripts": [], "stylesheets": [],
          "images": [], "libs": [], "favicon": None, "media_hosts": []}

HTML = {"url": None, "title": None, "lang": None, "description": None, "generator": None, "og": {},
        "canonical": None, "scripts": [], "inline_script_hashes": [], "stylesheets": [], "favicon_refs": [],
        "iframes": [], "forms": [], "outbound_hosts": [], "actor_outbound_hosts": [], "link_count": None,
        "self_link_ratio": None, "phones": [], "emails": [], "messengers": [], "crypto_wallets": [],
        "client_redirects": [], "dom_simhash": None, "dom_tag_count": None, "css_class_count": None,
        "obfuscated_class_ratio": None, "html_style": None, "sources": []}

AD_LIBRARY = {"ad_archive_id": None, "ad_library_url": None, "page_name": None, "page_id": None,
              "title": None, "epoch": None}


def _fill(template: dict, value) -> dict:
    """Szablon uzupełniony wartościami; nieznane klucze są odrzucane, brakujące = domyślne."""
    out = copy.deepcopy(template)
    if isinstance(value, dict):
        for k in template:
            if k in value and value[k] is not None:
                out[k] = value[k]
    return out


def _opt(template: dict, value):
    """Pod-obiekt sprawdzenia: null gdy nie wykonano, w przeciwnym razie pełny kształt."""
    return None if value is None else _fill(template, value)


def normalize(rec: dict) -> dict:
    rec["landing"] = _fill(LANDING, rec.get("landing"))
    rec["url_params"] = _fill(URL_PARAMS, rec.get("url_params"))
    rec["redirect_chain"] = [_fill(CHAIN_HOP, h) for h in rec.get("redirect_chain") or []]
    doms = []
    for d in rec.get("domains") or []:
        nd = _fill(DOMAIN, d)
        nd["lexical"] = _opt(LEXICAL, d.get("lexical"))
        nd["dns"] = _opt(DNS, d.get("dns"))
        nd["rdap"] = _opt(RDAP, d.get("rdap"))
        nd["tls"] = _opt(TLS, d.get("tls"))
        nd["ct"] = _opt(CT, d.get("ct"))
        doms.append(nd)
    rec["domains"] = doms
    rec["ips"] = [_fill(IP, i) for i in rec.get("ips") or []]
    rec["http"] = _fill(HTTP, rec.get("http"))
    assets = _fill(ASSETS, rec.get("assets"))
    assets["favicon"] = _fill(FAVICON, (rec.get("assets") or {}).get("favicon"))
    rec["assets"] = assets
    rec["html"] = _fill(HTML, rec.get("html"))
    rec["ad_library"] = [_fill(AD_LIBRARY, a) for a in rec.get("ad_library") or []]
    for k in ("hosts", "trackers", "endpoints", "html_variants", "kit_traits", "streams", "pivots"):
        rec[k] = rec.get(k) or []
    rec["kit_trait_descriptions"] = rec.get("kit_trait_descriptions") or {}
    return rec
