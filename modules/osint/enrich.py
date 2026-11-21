"""Wzbogacanie o publiczne metadane infrastruktury: DNS, RDAP/WHOIS, IP→ASN, certyfikat TLS,
Certificate Transparency (crt.sh), favicon. Każde źródło może zawieść (domena już nie
istnieje, timeout, brak RDAP dla TLD) – wtedy zapisujemy błąd i idziemy dalej."""
from __future__ import annotations

import codecs
import hashlib
import json
import socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path

import dns.exception
import dns.resolver
import httpx
import mmh3

from . import config
from .utils import is_public_ip, split_host


# --------------------------------------------------------------------------- cache
def _cache_path(kind: str, key: str) -> Path:
    h = hashlib.sha1(key.encode()).hexdigest()
    return config.CACHE_DIR / kind / f"{h}.json"


def cached(kind: str, key: str, fn):
    p = _cache_path(kind, key)
    try:
        if p.exists() and time.time() - p.stat().st_mtime < config.CACHE_TTL_S:
            return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    val = fn()
    if val is not None and not (isinstance(val, dict) and val.get("error")):   # błędów nie cache'ujemy
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(val, default=str))
        except OSError:
            pass
    return val


def _err(e: Exception) -> dict:
    return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


# --------------------------------------------------------------------------- DNS
def dns_lookup(host: str) -> dict:
    def run():
        r = dns.resolver.Resolver()
        r.timeout = r.lifetime = config.ENRICH_TIMEOUT_S / 2
        out: dict = {}
        for rtype in ("A", "AAAA", "CNAME", "NS", "MX", "TXT", "SOA"):
            name = host if rtype in ("A", "AAAA", "CNAME") else split_host(host)["icann_domain"]
            try:
                ans = r.resolve(name, rtype)
            except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
                continue
            except dns.resolver.NXDOMAIN:
                out["nxdomain"] = True
                break
            except dns.exception.DNSException as e:
                out.setdefault("errors", []).append(f"{rtype}: {type(e).__name__}")
                continue
            if rtype == "MX":
                out["mx"] = sorted(str(a.exchange).rstrip(".").lower() for a in ans)
            elif rtype == "TXT":
                out["txt"] = sorted(b"".join(a.strings).decode("utf-8", "replace")[:300] for a in ans)
            elif rtype == "SOA":
                a = ans[0]
                out["soa"] = {"mname": str(a.mname).rstrip(".").lower(), "rname": str(a.rname).rstrip(".").lower()}
            else:
                out[rtype.lower()] = sorted(str(a).rstrip(".").lower() for a in ans)
        return out
    try:
        return cached("dns", host, run)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# --------------------------------------------------------------------------- RDAP / WHOIS
def _vcard_get(entity: dict, field: str) -> str | None:
    for item in (entity.get("vcardArray") or [None, []])[1]:
        if item and item[0] == field:
            v = item[3]
            return " ".join(v) if isinstance(v, list) else str(v)
    return None


def _parse_rdap(j: dict) -> dict:
    ev = {e.get("eventAction"): e.get("eventDate") for e in j.get("events") or []}
    out = {
        "source": "rdap",
        "handle": j.get("handle"),
        "created": ev.get("registration"),
        "updated": ev.get("last changed"),
        "expires": ev.get("expiration"),
        "status": j.get("status") or [],
        "nameservers": sorted((n.get("ldhName") or "").lower().rstrip(".") for n in j.get("nameservers") or []),
        "registrar": None, "registrar_iana_id": None, "abuse_email": None,
        "registrant_org": None, "registrant_country": None, "privacy_proxy": False,
    }

    def walk(ents):
        for en in ents or []:
            roles = en.get("roles") or []
            if "registrar" in roles:
                out["registrar"] = _vcard_get(en, "fn") or out["registrar"]
                for pid in en.get("publicIds") or []:
                    if "IANA" in (pid.get("type") or ""):
                        out["registrar_iana_id"] = pid.get("identifier")
            if "abuse" in roles:
                out["abuse_email"] = _vcard_get(en, "email") or out["abuse_email"]
            if "registrant" in roles:
                org = _vcard_get(en, "org") or _vcard_get(en, "fn")
                out["registrant_org"] = org
                for item in (en.get("vcardArray") or [None, []])[1]:
                    if item and item[0] == "adr":
                        cc = (item[1] or {}).get("cc") if isinstance(item[1], dict) else None
                        last = item[3][-1] if isinstance(item[3], list) and item[3] else None
                        out["registrant_country"] = cc or (last if isinstance(last, str) and last else None)
                if org and any(x in org.lower() for x in ("privacy", "redacted", "proxy", "whoisguard", "withheld", "data protected")):
                    out["privacy_proxy"] = True
            walk(en.get("entities"))
    walk(j.get("entities"))
    return out


def _whois_fallback(domain: str) -> dict:
    import whois  # python-whois
    w = whois.whois(domain)

    def first(v):
        if isinstance(v, list):
            v = v[0] if v else None
        return v.isoformat() if isinstance(v, datetime) else (str(v) if v else None)
    ns = w.get("name_servers") or []
    ns = [ns] if isinstance(ns, str) else ns
    return {
        "source": "whois",
        "created": first(w.get("creation_date")), "updated": first(w.get("updated_date")),
        "expires": first(w.get("expiration_date")),
        "status": [str(s).split(" ")[0] for s in (w.get("status") or [])] if isinstance(w.get("status"), list) else ([w["status"]] if w.get("status") else []),
        "nameservers": sorted({str(n).lower().rstrip(".") for n in ns}),
        "registrar": first(w.get("registrar")), "registrar_iana_id": None,
        "abuse_email": None, "registrant_org": first(w.get("org")), "registrant_country": first(w.get("country")),
        "privacy_proxy": "privacy" in str(w.get("org") or "").lower() or "redacted" in str(w.get("org") or "").lower(),
    }


def rdap_domain(domain: str) -> dict:
    def run():
        try:
            with httpx.Client(timeout=config.ENRICH_TIMEOUT_S, follow_redirects=True,
                              headers={"Accept": "application/rdap+json"}) as c:
                r = c.get(f"https://rdap.org/domain/{domain}")
            if r.status_code == 200:
                return _parse_rdap(r.json())
            if r.status_code == 404 and "nic" not in str(r.url):
                rd = {"error": "rdap 404 (brak domeny lub brak RDAP dla TLD)"}
            else:
                rd = {"error": f"rdap http {r.status_code}"}
        except Exception as e:  # noqa: BLE001
            rd = _err(e)
        try:
            w = _whois_fallback(domain)
            if w.get("created") or w.get("registrar"):
                w["rdap_error"] = rd.get("error")
                return w
        except Exception as e:  # noqa: BLE001
            rd["whois_error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return rd
    return cached("rdap", domain, run)


# --------------------------------------------------------------------------- IP -> ASN
def ip_info(ip: str) -> dict:
    if not is_public_ip(ip):
        return {"ip": ip, "error": "adres niepubliczny"}

    def run():
        from ipwhois import IPWhois
        try:
            w = IPWhois(ip, timeout=int(config.ENRICH_TIMEOUT_S)).lookup_rdap(depth=0, retry_count=1)
        except Exception as e:  # noqa: BLE001
            return {"ip": ip, **_err(e)}
        net = w.get("network") or {}
        return {
            "ip": ip, "asn": str(w.get("asn") or "") or None, "asn_name": w.get("asn_description"),
            "asn_country": w.get("asn_country_code"), "asn_cidr": w.get("asn_cidr"),
            "asn_registry": w.get("asn_registry"), "network_name": net.get("name"),
            "network_cidr": net.get("cidr"), "network_country": net.get("country"),
        }
    return cached("ip", ip, run)


def reverse_dns(ip: str) -> str | None:
    def run():
        try:
            ans = dns.resolver.resolve_address(ip, lifetime=config.ENRICH_TIMEOUT_S / 2)
            return {"ptr": str(ans[0]).rstrip(".").lower()}
        except Exception:  # noqa: BLE001
            return {"ptr": None}
    return (cached("ptr", ip, run) or {}).get("ptr")


# --------------------------------------------------------------------------- TLS
def tls_cert(host: str, port: int = 443) -> dict:
    def run():
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.x509.oid import ExtensionOID, NameOID
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            with socket.create_connection((host, port), timeout=config.ENRICH_TIMEOUT_S) as s:
                with ctx.wrap_socket(s, server_hostname=host) as ss:
                    der = ss.getpeercert(binary_form=True)
                    tls_ver = ss.version()
        except Exception as e:  # noqa: BLE001
            return _err(e)
        cert = x509.load_der_x509_certificate(der)

        def name_attr(name, oid):
            v = name.get_attributes_for_oid(oid)
            return v[0].value if v else None
        try:
            sans = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound:
            sans = []
        return {
            "subject_cn": name_attr(cert.subject, NameOID.COMMON_NAME),
            "issuer_cn": name_attr(cert.issuer, NameOID.COMMON_NAME),
            "issuer_org": name_attr(cert.issuer, NameOID.ORGANIZATION_NAME),
            "not_before": cert.not_valid_before_utc.isoformat(),
            "not_after": cert.not_valid_after_utc.isoformat(),
            "serial": format(cert.serial_number, "x"),
            "sha256": cert.fingerprint(hashes.SHA256()).hex(),
            "sans": sorted(s.lower() for s in sans)[:100],
            "tls_version": tls_ver,
        }
    return cached("tls", f"{host}:{port}", run)


# --------------------------------------------------------------------------- Certificate Transparency
def crtsh(domain: str) -> dict:
    def run():
        try:
            with httpx.Client(timeout=config.ENRICH_TIMEOUT_S + 4) as c:
                r = c.get("https://crt.sh/", params={"q": domain, "output": "json"})
            if r.status_code != 200:
                return {"error": f"crt.sh http {r.status_code}"}
            rows = r.json()
        except Exception as e:  # noqa: BLE001
            return _err(e)
        names, issuers, dates = set(), set(), []
        for row in rows:
            for n in (row.get("name_value") or "").split("\n"):
                names.add(n.strip().lower().lstrip("*."))
            issuers.add(row.get("issuer_name", "")[:120])
            if row.get("not_before"):
                dates.append(row["not_before"])
        return {
            "cert_count": len(rows),
            "first_seen": min(dates) if dates else None,
            "last_seen": max(dates) if dates else None,
            "names": sorted(n for n in names if n)[:100],
            "issuers": sorted(i for i in issuers if i)[:10],
        }
    return cached("crtsh", domain, run)


# --------------------------------------------------------------------------- favicon
def favicon_hashes(data: bytes) -> dict:
    """mmh3 w wariancie Shodana (base64 z łamaniem linii) – można od razu szukać w Shodan/Censys:
    http.favicon.hash:<mmh3>."""
    return {"mmh3": mmh3.hash(codecs.encode(data, "base64")), "md5": hashlib.md5(data).hexdigest(),
            "size": len(data)}


def fetch_favicon(host: str, refs: list[str]) -> dict | None:
    def run():
        urls = [u for u in refs if u.startswith("http")] + [f"https://{host}/favicon.ico"]
        try:
            with httpx.Client(timeout=config.ENRICH_TIMEOUT_S, follow_redirects=True,
                              headers={"User-Agent": config.USER_AGENT}) as c:
                for u in urls[:3]:
                    try:
                        r = c.get(u)
                    except httpx.HTTPError:
                        continue
                    ct = r.headers.get("content-type", "")
                    if r.status_code == 200 and r.content and "html" not in ct and len(r.content) < 512_000:
                        return {**favicon_hashes(r.content), "url": u}
        except Exception as e:  # noqa: BLE001
            return _err(e)
        return {"error": "brak favicony"}
    return cached("favicon", host, run)


# --------------------------------------------------------------------------- stan domeny
_PARKING_NS = ("dyna-ns.net", "registrar-servers.com", "sedoparking.com", "bodis.com", "parkingcrew.net",
               "above.com", "dan.com", "afternic.com", "suspended-domain", "namebrightdns.com",
               "domaincontrol.com", "parklogic.com", "uniregistrymarket.link", "huge-domains.com")
_SINKHOLE_IPS = {"1.1.1.1", "0.0.0.0", "127.0.0.1", "127.0.53.53", "8.8.8.8"}


def cert_covers(host: str, sans: list[str], cn: str | None) -> bool:
    names = set(sans or []) | ({cn.lower()} if cn else set())
    for n in names:
        if n == host or (n.startswith("*.") and host.endswith(n[1:]) and host.count(".") == n.count(".")):
            return True
    return False


def domain_flags(dns_rec: dict | None, rdap: dict | None, tls: dict | None, host: str) -> list[str]:
    """Stan domeny w chwili wzbogacania – ważne, bo domeny scamowe żyją krótko i po
    zgłoszeniu są zawieszane/parkowane; wtedy DNS/TLS opisują rejestratora, nie aktora."""
    flags = []
    d, r, t = dns_rec or {}, rdap or {}, tls or {}
    if d.get("nxdomain"):
        flags.append("nxdomain")
    st = " ".join(r.get("status") or []).lower()
    if "hold" in st:
        flags.append("on_hold")              # clientHold/serverHold – zawieszona (często po zgłoszeniu)
    if "pending delete" in st or "redemption" in st:
        flags.append("pending_delete")
    ns = " ".join((r.get("nameservers") or []) + (d.get("ns") or []))
    if any(p in ns for p in _PARKING_NS):
        flags.append("registrar_or_parking_ns")
    if set(d.get("a") or []) & _SINKHOLE_IPS:
        flags.append("sinkhole_ip")
    if t.get("sha256") and not cert_covers(host, t.get("sans") or [], t.get("subject_cn")):
        flags.append("tls_cert_mismatch")
    if flags and "nxdomain" not in flags and ("on_hold" in flags or "sinkhole_ip" in flags
                                              or "registrar_or_parking_ns" in flags):
        flags.append("likely_taken_down")
    return flags


# --------------------------------------------------------------------------- pomocnicze
def domain_age_days(created: str | None, at: str | None) -> int | None:
    if not created:
        return None
    try:
        c = datetime.fromisoformat(created.replace("Z", "+00:00"))
        a = datetime.fromisoformat(at.replace("Z", "+00:00")) if at else datetime.now(timezone.utc)
        if c.tzinfo is None:
            c = c.replace(tzinfo=timezone.utc)
        if a.tzinfo is None:
            a = a.replace(tzinfo=timezone.utc)
        return (a - c).days
    except (ValueError, TypeError):
        return None
