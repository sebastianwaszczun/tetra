"""Pobranie strony z adresu URL w przeglądarce headless (Playwright, Chromium).

Zapisuje w folderze materiału: page.html, network.har, screenshot.png (cała strona)
oraz łańcuch przekierowań. Strony-scamy otwieramy WYŁĄCZNIE tutaj, w kontenerze,
nigdy w przeglądarce na własnym komputerze.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from fakecollector.features.osint import config as osint_config
from fakecollector.features.osint.live import UnsafeTarget, check_target

log = logging.getLogger(__name__)

USER_AGENT = os.environ.get("FETCH_USER_AGENT", osint_config.USER_AGENT)
REFERER = osint_config.REFERER
TIMEOUT_MS = int(os.environ.get("FETCH_TIMEOUT_MS", "30000"))
SETTLE_MS = int(os.environ.get("FETCH_SETTLE_MS", "3000"))


@dataclass
class FetchResult:
    requested_url: str
    final_url: str | None = None
    status: int | None = None
    title: str = ""
    html_path: str | None = None
    har_path: str | None = None
    screenshot_path: str | None = None
    redirect_chain: list[str] = field(default_factory=list)
    error: str | None = None
    method: str = "playwright"

    @property
    def ok(self) -> bool:
        return self.error is None and self.html_path is not None


def normalize_input_url(url: str) -> str:
    url = url.strip()
    return url if "://" in url else "https://" + url


def fetch(url: str, out_dir: str | Path, timeout_ms: int = TIMEOUT_MS) -> FetchResult:
    """Otwiera stronę w Chromium. Gdy Playwright zawiedzie, próbuje zwykłego zapytania HTTP."""
    url = normalize_input_url(url)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    res = FetchResult(requested_url=url)
    try:
        check_target(url)
    except UnsafeTarget as exc:
        res.error = f"niedozwolony adres: {exc}"
        return res
    try:
        _fetch_playwright(url, out, timeout_ms, res)
    except Exception as exc:
        log.warning("Playwright nie pobrał %s: %s", url, exc)
        res.error = f"playwright: {exc.__class__.__name__}: {str(exc).splitlines()[0][:200]}"
        _fetch_httpx(url, out, timeout_ms, res)
    return res


def _fetch_playwright(url: str, out: Path, timeout_ms: int, res: FetchResult) -> None:
    from playwright.sync_api import sync_playwright

    har_path = out / "network.har"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            ctx = browser.new_context(
                user_agent=USER_AGENT, locale="pl-PL", timezone_id="Europe/Warsaw",
                viewport={"width": 390, "height": 844}, is_mobile=True,
                extra_http_headers={"Referer": REFERER}, record_har_path=str(har_path),
                record_har_content="embed", accept_downloads=False, ignore_https_errors=True,
            )
            page = ctx.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
            except Exception:
                pass
            page.wait_for_timeout(SETTLE_MS)

            chain: list[str] = []
            if response is not None:
                req = response.request
                while req is not None:
                    chain.insert(0, req.url)
                    req = req.redirected_from
                res.status = response.status
            if page.url and (not chain or chain[-1] != page.url):
                chain.append(page.url)
            res.redirect_chain = chain
            res.final_url = page.url
            res.title = page.title()

            html_path = out / "page.html"
            html_path.write_text(page.content(), encoding="utf-8")
            res.html_path = str(html_path)
            shot = out / "screenshot.png"
            try:
                page.screenshot(path=str(shot), full_page=True, timeout=20000)
            except Exception:
                page.screenshot(path=str(shot), timeout=20000)
            res.screenshot_path = str(shot)
            ctx.close()
            res.har_path = str(har_path) if har_path.exists() else None
            res.error = None
        finally:
            browser.close()


def _fetch_httpx(url: str, out: Path, timeout_ms: int, res: FetchResult) -> None:
    """Zapas: zwykłe zapytanie HTTP (bez JS, zrzutu i HAR-a)."""
    try:
        import httpx

        with httpx.Client(follow_redirects=True, timeout=timeout_ms / 1000,
                          headers={"User-Agent": USER_AGENT, "Referer": REFERER}, verify=False) as client:
            r = client.get(url)
        res.method = "httpx"
        res.status = r.status_code
        res.final_url = str(r.url)
        res.redirect_chain = [str(h.url) for h in r.history] + [str(r.url)]
        if "html" in r.headers.get("content-type", ""):
            html_path = out / "page.html"
            html_path.write_text(r.text, encoding="utf-8")
            res.html_path = str(html_path)
            res.error = None
    except Exception as exc:
        res.error = (res.error or "") + f" | httpx: {exc.__class__.__name__}: {str(exc)[:200]}"
