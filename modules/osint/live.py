"""Tryb URL: pobranie strony "na żywo".

* `fetch_http`  – httpx bez automatycznych przekierowań: każdy skok łańcucha jest zapisywany
                  (status, Location, nagłówki), potem meta-refresh / JS location; na końcu
                  pobieramy skrypty/style/favicon z domeny aktora, żeby je zahashować.
* `fetch_browser` – (opcjonalnie, render=true) Chromium przez Playwright nagrywa HAR,
                  który przechodzi przez ten sam parser co HAR-y z dysku.
Strony scamowe często stosują cloaking (biała strona dla botów/IP z data center) – stąd
mobilny UA z Facebooka i Referer z l.facebook.com. Wynik "live" może różnić się od tego,
co widziała ofiara; dlatego HAR od zgłaszającego jest źródłem nadrzędnym."""
from __future__ import annotations

import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from . import config
from .capture import Capture, Document, Resource, load_har
from .html_extract import _JS_REDIRECT, _META_REFRESH
from .utils import host_of, is_commodity, is_public_ip, sha256


class UnsafeTarget(ValueError):
    pass


def check_target(url: str) -> None:
    sp = urlsplit(url)
    if sp.scheme not in ("http", "https") or not sp.hostname:
        raise UnsafeTarget("dozwolone tylko adresy http(s)")
    if config.ALLOW_PRIVATE_TARGETS:
        return
    try:
        infos = socket.getaddrinfo(sp.hostname, sp.port or (443 if sp.scheme == "https" else 80))
    except socket.gaierror as e:
        raise UnsafeTarget(f"nie można rozwiązać hosta: {e}") from e
    for info in infos:
        if not is_public_ip(info[4][0]):
            raise UnsafeTarget(f"host wskazuje na adres niepubliczny ({info[4][0]})")


def _headers() -> dict:
    return {"User-Agent": config.USER_AGENT, "Accept-Language": config.ACCEPT_LANGUAGE,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": config.REFERER}


def _peer_ip(resp: httpx.Response) -> str | None:
    try:
        stream = resp.extensions.get("network_stream")
        addr = stream.get_extra_info("server_addr") if stream else None
        return addr[0] if addr else None
    except Exception:  # noqa: BLE001
        return None


def fetch_http(url: str) -> Capture:
    check_target(url)
    now = datetime.now(timezone.utc).isoformat()
    cap = Capture(page_url=url, observed_at=now, inputs=[{"kind": "url", "name": url, "mode": "http"}])
    current = url
    html = None
    with httpx.Client(timeout=config.HTTP_TIMEOUT_S, follow_redirects=False, headers=_headers(),
                      verify=False) as c:
        for hop in range(config.MAX_REDIRECTS + 3):
            check_target(current)
            try:
                with c.stream("GET", current) as r:
                    ip = _peer_ip(r)
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > config.MAX_BODY_BYTES:
                            break
            except httpx.HTTPError as e:
                cap.live_chain.append({"url": current, "host": host_of(current), "status": None,
                                       "type": "error", "error": f"{type(e).__name__}: {e}"[:200]})
                cap.notes.append(f"strona niedostępna: {type(e).__name__}")
                break
            hdrs = [(k.lower(), v) for k, v in r.headers.multi_items()]
            loc = r.headers.get("location")
            mime = (r.headers.get("content-type") or "").split(";")[0]
            res = Resource(url=current, status=r.status_code, mime=mime, resource_type="document",
                           headers=hdrs, ip=ip, redirect_url=urljoin(current, loc) if loc else None,
                           body=body if "html" in mime else None, body_sha256=sha256(body) if body else None,
                           size=len(body), started=datetime.now(timezone.utc).isoformat(), source="live",
                           cookies=[v.split("=", 1)[0] for k, v in hdrs if k == "set-cookie"])
            cap.resources.append(res)
            if r.status_code in (301, 302, 303, 307, 308) and loc:
                cap.live_chain.append({"url": current, "host": host_of(current), "status": r.status_code,
                                       "type": "http_redirect", "location": res.redirect_url, "ip": ip, "source": "live"})
                current = res.redirect_url
                continue
            if "html" in mime and body:
                html = body.decode(r.encoding or "utf-8", "replace")
                nxt = _client_redirect(html, current)
                if nxt and nxt != current and hop < config.MAX_REDIRECTS:
                    cap.live_chain.append({"url": current, "host": host_of(current), "status": r.status_code,
                                           "type": nxt[0], "location": nxt[1], "ip": ip, "source": "live"})
                    current = nxt[1]
                    continue
            cap.live_chain.append({"url": current, "host": host_of(current), "status": r.status_code,
                                   "type": "final", "ip": ip, "source": "live"})
            break

        if html:
            cap.documents.append(Document(current, html, "live", "live"))
            _fetch_subresources(c, cap, html, current)
    cap.page_url = url
    return cap


def _client_redirect(html: str, base: str):
    head = html[:20000]
    soup = BeautifulSoup(head, "lxml")
    m = soup.find("meta", attrs={"http-equiv": lambda v: v and v.lower() == "refresh"})
    if m:
        mm = _META_REFRESH.search(m.get("content") or "")
        if mm:
            return ("meta_refresh", urljoin(base, mm.group(1)))
    # tylko krótkie strony-przekładki – w dużych landingach JS location bywa w handlerach kliknięć
    if len(html) < 6000:
        mm = _JS_REDIRECT.search(html)
        if mm:
            return ("js_redirect", urljoin(base, mm.group(1) or mm.group(2)))
    return None


def _fetch_subresources(c: httpx.Client, cap: Capture, html: str, base: str) -> None:
    soup = BeautifulSoup(html, "lxml")
    refs = []
    for s in soup.find_all("script", src=True):
        refs.append(("script", urljoin(base, s["src"])))
    for ln in soup.find_all("link", href=True):
        rel = " ".join(ln.get("rel") or []).lower()
        if "stylesheet" in rel:
            refs.append(("stylesheet", urljoin(base, ln["href"])))
        elif "icon" in rel:
            refs.append(("image", urljoin(base, ln["href"])))
    refs.append(("image", urljoin(base, "/favicon.ico")))
    seen = set()
    for rtype, u in refs:
        if u in seen or not u.startswith("http") or len(seen) >= config.MAX_LIVE_SUBRESOURCES:
            continue
        seen.add(u)
        h = host_of(u)
        try:
            if is_commodity(h):
                cap.resources.append(Resource(url=u, resource_type=rtype, source="live:ref"))
                continue
            check_target(u)
            r = c.get(u, follow_redirects=True)
        except (httpx.HTTPError, UnsafeTarget):
            continue
        mime = (r.headers.get("content-type") or "").split(";")[0]
        if rtype == "image" and "html" in mime:
            continue
        body = r.content[: config.MAX_BODY_BYTES]
        cap.resources.append(Resource(
            url=u, status=r.status_code, mime=mime, resource_type=rtype, ip=_peer_ip(r),
            headers=[(k.lower(), v) for k, v in r.headers.multi_items()], body=body,
            body_sha256=sha256(body) if body else None, size=len(body), source="live"))


def fetch_browser(url: str, wait_ms: int = 4000) -> Capture:
    """Render w headless Chromium z nagrywaniem HAR (przekierowania JS, zasoby dynamiczne)."""
    check_target(url)
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as td:
        har_path = Path(td) / "live.har"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                record_har_path=str(har_path), record_har_content="embed",
                user_agent=config.USER_AGENT, locale="pl-PL", is_mobile=True,
                viewport={"width": 390, "height": 844}, extra_http_headers={"Referer": config.REFERER})
            page = ctx.new_page()
            dom = None
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=int(config.HTTP_TIMEOUT_S * 3000))
                page.wait_for_timeout(wait_ms)
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1000)
                dom = page.content()
                final = page.url
            except Exception as e:  # noqa: BLE001
                final = None
                err = f"{type(e).__name__}: {str(e)[:150]}"
            ctx.close()
            browser.close()
        cap = load_har(har_path.read_bytes(), "live-browser.har")
    cap.inputs = [{"kind": "url", "name": url, "mode": "browser"}]
    cap.page_url = url
    if dom:
        cap.documents.append(Document(final, dom, "live:dom", "dom"))
        if final and final != url:
            cap.live_chain.append({"url": url, "host": host_of(url), "type": "browser_navigation",
                                   "location": final, "source": "live:browser"})
    else:
        cap.notes.append(f"render nieudany: {err}")
    return cap
