"""Zrzut kreacji z podglądu Biblioteki Reklam i wysyłka reklamy do potoku /analyze.

Podgląd (render_ad) otwiera się bez logowania, ale wymaga tokenu w adresie. Token jest tylko w pamięci
przeglądarki, nigdy w zapisanych plikach. Na stronę docelową reklamy nie wchodzimy (fetch=false).
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from .model import FiltrTokenu, ReklamaMeta, bez_tokenu

log = logging.getLogger(__name__)
log.addFilter(FiltrTokenu())

PRZYCISK_COOKIES = re.compile(r"Odrzuć opcjonalne|Decline optional|Only allow essential", re.I)


class Przechwyt:
    """Jedna przeglądarka na cały skan (with Przechwyt(...) as p: p.zrzut(reklama))."""

    def __init__(self, token: str | None, cfg: dict[str, Any], katalog: Path) -> None:
        self.token = token
        self.cfg = cfg
        self.katalog = katalog / "kreacje"
        self._pw = self._przegladarka = None

    def __enter__(self) -> Przechwyt:
        return self

    def __exit__(self, *_: Any) -> None:
        if self._przegladarka:
            self._przegladarka.close()
        if self._pw:
            self._pw.stop()

    def _strona(self):
        if not self._przegladarka:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._przegladarka = self._pw.chromium.launch()
        return self._przegladarka.new_page(viewport={"width": self.cfg["szerokosc"], "height": 1000},
                                           locale="pl-PL")

    def zrzut(self, reklama: ReklamaMeta) -> bytes | None:
        """PNG największego obrazu albo filmu w karcie reklamy. None, gdy podgląd się nie otworzył.

        Zapisany wcześniej zrzut jest używany ponownie, także w trybie odtwarzania bez tokenu."""
        zapisany = self.katalog / f"{reklama.id}.png"
        if zapisany.is_file():
            return zapisany.read_bytes()
        if not self.token:
            return None
        strona = self._strona()
        try:
            adres = f"https://www.facebook.com/ads/archive/render_ad/?id={reklama.id}&access_token={self.token}"
            strona.goto(adres, timeout=self.cfg["limit_czasu_ms"], wait_until="networkidle")
            try:
                strona.get_by_role("button", name=PRZYCISK_COOKIES).first.click(timeout=3000)
                strona.wait_for_timeout(500)
            except Exception:
                pass  # baner nie zawsze się pokazuje
            najwiekszy, pole = None, 0.0
            for element in strona.locator("img, video").all():
                ramka = element.bounding_box()
                if not ramka or min(ramka["width"], ramka["height"]) < self.cfg["min_bok_px"]:
                    continue
                if ramka["width"] * ramka["height"] > pole:
                    najwiekszy, pole = element, ramka["width"] * ramka["height"]
            png = najwiekszy.screenshot() if najwiekszy else strona.screenshot(full_page=True)
        except Exception as exc:
            log.warning("Zrzut reklamy %s nieudany: %s", reklama.id, bez_tokenu(str(exc)).splitlines()[0][:200])
            return None
        finally:
            strona.close()
        self.katalog.mkdir(parents=True, exist_ok=True)
        (self.katalog / f"{reklama.id}.png").write_bytes(png)
        return png


def pakiet(reklama: ReklamaMeta, rekord: dict[str, Any], png: bytes | None) -> tuple[str, list[tuple[str, bytes]]]:
    """Tekst i pliki dla /analyze. Plik .info.json ma kształt, który moduł OSINT już rozpoznaje
    (ślady ad_archive_id i fb_page_id); drugi plik to pełny rekord z API bez tokenu."""
    linie = [f"Reklama z Biblioteki Reklam Meta, strona: {reklama.page_name or reklama.page_id}", reklama.tekst]
    if reklama.domena:
        linie.append(f"https://{reklama.domena}")
    info = {
        "extractor_key": "FacebookAds",
        "id": reklama.id,
        "webpage_url": reklama.adres_biblioteki,
        "uploader": reklama.page_name,
        "uploader_id": reklama.page_id,
        "title": (reklama.tytuly or [reklama.page_name or ""])[0],
        "epoch": int(time.time()),
    }
    pliki = [
        (f"reklama_{reklama.id}.info.json", json.dumps(info, ensure_ascii=False).encode()),
        (f"reklama_{reklama.id}_rekord.json", bez_tokenu(json.dumps(rekord, ensure_ascii=False)).encode()),
    ]
    if png:
        pliki.insert(0, (f"reklama_{reklama.id}.png", png))
    return "\n\n".join(l for l in linie if l), pliki


def wyslij(tekst: str, pliki: list[tuple[str, bytes]], url: str, limit_czasu_s: float,
           transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    typy = {".png": "image/png", ".json": "application/json"}
    with httpx.Client(timeout=limit_czasu_s, transport=transport) as http:
        r = http.post(url, data={"text": tekst, "fetch": "false", "osint": "true", "av": "false",
                                 "image": "true", "text_features": "true"},
                      files=[("files", (n, d, typy.get(Path(n).suffix, "application/octet-stream")))
                             for n, d in pliki])
    r.raise_for_status()
    return r.json()


def podsumuj(odp: dict[str, Any]) -> dict[str, Any]:
    """Najważniejsze wyniki potoku do listy reklam w API (pełny wynik jest w zgłoszeniu)."""
    obraz = odp.get("image_features") or {}
    tekst = odp.get("text_features") or {}
    osint = odp.get("osint") or {}
    return {
        "id_zgloszenia": (odp.get("incident") or {}).get("id"),
        "osoby_na_obrazie": obraz.get("persons_visual") or [],
        "deepfake_orientacyjnie": obraz.get("deepfake_score"),
        "logo": obraz.get("logo") or [],
        "narracja": (tekst.get("narrative") or {}).get("label"),
        "presja": {k: v for k, v in (tekst.get("persuasion") or {}).items() if v},
        "slady_osint": len(osint.get("pivots") or []),
        "bledy": [odp[k] for k in ("image_error", "osint_error", "av_error") if odp.get(k)],
    }
