"""Główna funkcja preprocessingu: dowolne wejście -> Incident ze składnikami dla modułów.

    from fakecollector.ingest import ingest
    inc = ingest("https://przyklad.live/oferta")      # URL
    inc = ingest("data/DANE/47")                      # folder przypadku z paczki
    inc = ingest("zrzut.png")                         # zrzut ekranu
    inc = ingest_upload(text="...", files=[("a.har", b"...")])   # wejście z API (tekst + pliki)
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import har as har_mod
from .dedup import DedupIndex, fingerprint, sha256_text
from .detect import HTML_EXT, IMAGE_EXT, VIDEO_EXT, AUDIO_EXT, detect_input
from .models import (AUDIO, FRAME, HAR, HTML, IMAGE, JSON, SCREENSHOT, TEXT, URL, VIDEO,
                     Incident)

log = logging.getLogger(__name__)

STORE_ROOT = Path(os.environ.get("STORE_DIR", "data/store"))

# Pierwszeństwo źródeł tekstu dla modułu tekstu (od najlepszego)
TEXT_PRIORITY = ["input", "html", "page_text", "har", "adlib", "json", "transcript", "ocr"]

ERROR_PAGE_RE = re.compile(
    r"bad gateway|error code \d{3}|\b50[234]\b[^\n]{0,40}(?:error|gateway|unavailable)|"
    r"\b40[34]\b[^\n]{0,40}(?:not found|forbidden)|page not found|nie znaleziono strony|"
    r"attention required|just a moment|checking your browser|account (?:has been )?suspended|"
    r"(?:this )?domain (?:is|may be) for sale|domain (?:is )?parked|site can.t be reached|"
    r"err_name_not_resolved|default web page|index of /", re.I)


COMMON_DOMAINS = {"google.com", "facebook.com", "instagram.com", "youtube.com", "cloudflare.com",
                  "amazon.com", "apple.com", "microsoft.com", "tiktok.com", "x.com", "twitter.com",
                  "wikipedia.org", "gazeta.pl", "onet.pl", "wp.pl", "interia.pl"}


def is_common_domain(url: str) -> bool:
    """Popularna, prawdziwa domena znaleziona w tekście (np. „AMAZON.COM” w treści reklamy) – nie
    jest adresem strony scamu, więc jej nie pobieramy ani nie traktujemy jako adresu materiału."""
    from urllib.parse import urlsplit
    host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    try:
        from osint.utils import is_commodity  # type: ignore  # pełna lista z modułu OSINT
        if is_commodity(host):
            return True
    except Exception:
        pass
    return host in COMMON_DOMAINS or any(host.endswith("." + d) for d in COMMON_DOMAINS)


PAGE_ORIGINS = ("html", "page_text", "har")


def _best_per_page(arts: list) -> list:
    """Przypadek może mieć kilka stron (np. DANE/61: strona_1, strona_2, strona_3). Dla każdej
    strony (folderu) bierzemy najlepsze źródło: zapisany HTML > page_text.txt > HTML z HAR-a."""
    by_page: dict[str, list] = {}
    for a in arts:
        by_page.setdefault(str(Path(a.path).parent) if a.path else "", []).append(a)
    out = []
    for group in by_page.values():
        for origin in PAGE_ORIGINS:
            best = [a for a in group if a.source == origin]
            if best:
                out.extend(best)
                break
    return sorted(out, key=lambda a: PAGE_ORIGINS.index(a.source))


@dataclass
class Options:
    fetch: bool = True          # pobierać strony z URL (Playwright)
    ocr: bool = True            # OCR zrzutów ekranu
    ocr_limit: int = 12         # najwyżej tyle obrazów do OCR w jednym materiale
    video: bool = True          # wyciągać dźwięk i klatki z wideo
    assets: bool = True         # dodawać obrazy z folderów *_files (dla modułu obrazu)
    assets_limit: int = 150
    follow_urls: bool = True    # pobierać adresy znalezione w tekście i na zrzutach
    max_fetch: int = 2          # najwyżej tyle stron pobranych na materiał
    dedup: bool = True
    save: bool = True           # zapisywać incident.json w folderze materiału
    store_root: Path = STORE_ROOT


def is_error_page(title: str, text: str) -> bool:
    """Strona błędu serwera, parkingu domeny albo blokady (np. Cloudflare 502)."""
    if title and ERROR_PAGE_RE.search(title):
        return True
    return len(text.split()) < 300 and bool(ERROR_PAGE_RE.search(text[:3000]))


# ============================================================================ wejścia

def ingest(value: str | Path, input_type: str | None = None, **opts: Any) -> Incident:
    """Zamienia jedno wejście na Incident. Błędy nie przerywają pracy – trafiają do incident.errors."""
    o = Options(**opts)
    kind = input_type or detect_input(value)
    source = str(value) if kind != "text" else " ".join(str(value).split())[:200]
    inc = Incident(input_type=kind, source=source)
    inc.store_dir = str(Path(o.store_root) / inc.id)

    handler: Callable = HANDLERS.get(kind, _unsupported)
    try:
        handler(inc, value, o)
    except Exception as exc:  # jeden zły plik nie może zatrzymać całej analizy
        log.exception("Preprocessing %s", source)
        inc.errors.append(f"{kind}: {exc.__class__.__name__}: {exc}")
    finalize(inc, o)
    return inc


def ingest_upload(text: str | None = None, files: list[tuple[str, bytes]] | None = None,
                  **opts: Any) -> Incident:
    """Wejście z API/frontu: opcjonalny tekst (albo link) i dowolna liczba plików.

    Pliki są zapisywane w folderze materiału (data/store/<id>/input/) i traktowane jak folder przypadku.
    """
    o = Options(**opts)
    text = (text or "").strip()
    files = files or []
    if not text and not files:
        raise ValueError("Podaj tekst albo co najmniej jeden plik.")
    if text and not files:
        return ingest(text, **opts)
    if len(files) == 1 and not text:
        inc = Incident(input_type="upload", source=files[0][0])
    else:
        inc = Incident(input_type="upload", source=f"{len(files)} plików" + (" + tekst" if text else ""))
    inc.store_dir = str(Path(o.store_root) / inc.id)
    folder = Path(inc.store_dir) / "input"
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files:
        target = folder / Path(name).name
        target.write_bytes(data)
    try:
        if text:
            kind = detect_input(text)
            (_url if kind == "url" else _text)(inc, text, o)
        _load_folder(inc, folder, o, source="input")
    except Exception as exc:
        log.exception("Preprocessing uploadu")
        inc.errors.append(f"upload: {exc.__class__.__name__}: {exc}")
    finalize(inc, o)
    return inc


def _text(inc: Incident, value: str, o: Options) -> None:
    from .screenshot import find_urls
    inc.add_text(str(value), "input")
    for url in [u for u in find_urls(str(value)) if not is_common_domain(u)][: o.max_fetch]:
        inc.add_url(url, "text")
        if o.fetch and o.follow_urls:
            _fetch_into(inc, url, o)


def _url(inc: Incident, value: str, o: Options) -> None:
    inc.add_url(str(value), "input")
    if o.fetch:
        _fetch_into(inc, str(value), o)
    else:
        inc.missing.append("strona nie została pobrana (wyłączone pobieranie)")


def _image(inc: Incident, value: str | Path, o: Options, source: str = "input") -> None:
    inc.add(SCREENSHOT, value, source=source)
    if o.ocr:
        _ocr_into(inc, Path(value), o)


def _video(inc: Incident, value: str | Path, o: Options, source: str = "input") -> None:
    from . import video as v
    art = inc.add(VIDEO, value, source=source)
    if not o.video:
        return
    info = v.probe(value)
    art.meta.update(info)
    if info.get("error"):
        inc.errors.append(f"wideo {Path(value).name}: {info['error']}")
        return
    out = Path(inc.store_dir) / "av"
    if info.get("has_audio"):
        audio = v.extract_audio(value, out)
        if audio:
            inc.add(AUDIO, audio, source="video", video=str(value))
    for frame in v.extract_frames(value, out, info.get("duration") or 0):
        inc.add(FRAME, frame, source="video", video=str(value))


def _audio(inc: Incident, value: str | Path, o: Options, source: str = "input") -> None:
    inc.add(AUDIO, value, source=source)


def _har(inc: Incident, value: str | Path, o: Options, source: str = "input") -> None:
    try:
        data = har_mod.load_har(value)
    except Exception as exc:
        inc.errors.append(f"HAR {Path(value).name}: {exc}")
        return
    entries = data.get("log", {}).get("entries", [])
    inc.add(HAR, value, source=source, entries=len(entries), hosts=len(har_mod.hosts(data)))
    first = har_mod.first_navigation(data)
    if first:
        inc.add_url(first, "har")
    chain = har_mod.redirect_chain(data)
    if len(chain) > len(inc.redirect_chain):
        inc.redirect_chain = chain
    page_url, html = har_mod.main_document(data)
    if html:  # tekst strony z HAR-a; jeśli jest zapisana strona HTML, ona ma pierwszeństwo
        from fakecollector.features.text import extract_text_from_html
        text, meta = extract_text_from_html(html)
        err = is_error_page(meta.get("title", ""), text)
        inc.add_text(text, "har", path=str(value), title=meta.get("title", ""), error_page=err,
                     url=page_url)


def _html(inc: Incident, value: str | Path, o: Options, source: str = "input") -> None:
    from fakecollector.features.text import extract_text_from_html
    path = Path(value)
    if path.suffix.lower() in {".mhtml", ".mht"}:
        inc.add(HTML, path, source=source, format="mhtml")   # MHTML czyta moduł OSINT
        return
    raw = path.read_bytes()
    html = raw.decode("utf-8", "replace") if b"\x00" not in raw[:1000] else raw.decode("utf-16", "replace")
    text, meta = extract_text_from_html(html)
    err = is_error_page(meta.get("title", ""), text)
    inc.add(HTML, path, source=source, title=meta.get("title", ""), error_page=err)
    inc.add_text(text, "html", path=str(path), title=meta.get("title", ""), error_page=err)
    for key in ("saved_from",):
        if meta.get(key):
            inc.add_url(meta[key], "html")
    m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', html[:20000], re.I) \
        or re.search(r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)', html[:20000], re.I)
    if m and m.group(1).startswith("http"):
        inc.add_url(m.group(1), "html")


def _json(inc: Incident, value: str | Path, o: Options, source: str = "input") -> None:
    import json
    path = Path(value)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        inc.add(JSON, path, source=source, type="invalid")
        return
    if not isinstance(data, dict):
        inc.add(JSON, path, source=source, type="list")
        return
    if "extractor" in data and ("webpage_url" in data or "formats" in data):
        # metadane yt-dlp, np. reklama wideo z Biblioteki Reklam Meta
        inc.add(JSON, path, source=source, type="yt-dlp", extractor=data.get("extractor"))
        if data.get("webpage_url"):
            inc.add_url(data["webpage_url"], "metadata")
        parts = [str(data[k]) for k in ("title", "description") if data.get(k) not in (None, "", "None")]
        extractor = str(data.get("extractor") or "")
        origin = "adlib" if "facebook" in extractor and "ads" in extractor else "json"
        inc.add_text("\n".join(parts), origin, path=str(path), title=str(data.get("title") or ""))
    elif str(data.get("tool", "")).startswith("page-capture"):   # manifest narzędzia page-capture
        inc.add(JSON, path, source=source, type="page_capture", observed_at=data.get("captured_at"),
                title=data.get("title"))
        if data.get("page_url"):
            inc.add_url(data["page_url"], "metadata")
    elif "url" in data and "timestamp_utc" in data:          # archive_info.json z archiwum strony
        inc.add(JSON, path, source=source, type="archive_info", observed_at=data.get("timestamp_utc"))
        inc.add_url(data["url"], "metadata")
    elif "hostname" in data and "ip" in data:                # dns_ssl.json z archiwum strony
        inc.add(JSON, path, source=source, type="dns_ssl", hostname=data.get("hostname"))
    else:
        inc.add(JSON, path, source=source, type="other")


def _textfile(inc: Incident, value: str | Path, o: Options, source: str = "input") -> None:
    text = Path(value).read_text(encoding="utf-8", errors="replace")
    origin = "page_text" if Path(value).name == "page_text.txt" else "input"
    inc.add_text(text, origin, path=str(value), error_page=is_error_page("", text))


def _archive(inc: Incident, value: str | Path, o: Options, source: str = "input") -> None:
    target = Path(inc.store_dir) / "archive" / Path(value).stem
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(value) as z:
        for zi in z.infolist():
            name = Path(zi.filename)
            if zi.is_dir() or name.is_absolute() or ".." in name.parts:
                continue  # ochrona przed ścieżkami wychodzącymi poza folder
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(zi) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
    _load_folder(inc, target, o, source="archive")


def _folder(inc: Incident, value: str | Path, o: Options) -> None:
    _load_folder(inc, Path(value), o, source="folder")


def _unsupported(inc: Incident, value: Any, o: Options) -> None:
    inc.errors.append(f"nieobsługiwany rodzaj wejścia: {value}")


HANDLERS: dict[str, Callable] = {
    "text": _text, "url": _url, "image": _image, "video": _video, "audio": _audio, "har": _har,
    "html": _html, "json": _json, "textfile": _textfile, "archive": _archive, "folder": _folder,
}


# ============================================================================ folder przypadku

def _is_page_capture(manifest: Path) -> bool:
    try:
        return b"page-capture" in manifest.read_bytes()[:400]
    except OSError:
        return False


def _is_mpegts(path: Path) -> bool:
    """Kawałek strumienia wideo (MPEG-TS: bajt 0x47 co 188 bajtów), a nie plik TypeScript."""
    try:
        with open(path, "rb") as f:
            head = f.read(188 * 3 + 1)
        return len(head) > 376 and head[0] == 0x47 and head[188] == 0x47 and head[376] == 0x47
    except OSError:
        return False


def _hls_into(inc: Incident, folder: Path, parts: list[Path], o: Options, source: str) -> None:
    """Sklejamy zapisane kawałki strumienia w jeden plik mp4 dla modułu audio/wideo.

    Narzędzia do zapisu stron często łapią tylko początek strumienia (np. 2 z 105 kawałków) –
    zapisujemy w meta, ile kawałków brakuje, i dopisujemy to do braków.
    """
    import subprocess
    listed = 0
    for m3u8 in folder.glob("*.m3u8"):
        try:
            listed = max(listed, sum(1 for line in m3u8.read_text(errors="replace").splitlines()
                                     if line.strip() and not line.startswith("#")))
        except OSError:
            pass
    out_dir = Path(inc.store_dir) / "av"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"stream_{len(inc.of_kind(VIDEO)) + 1}.mp4"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "concat:" + "|".join(str(p) for p in parts),
                        "-c", "copy", "-bsf:a", "aac_adtstoasc", str(out)], capture_output=True, timeout=300)
    if r.returncode != 0 or not out.exists():
        inc.errors.append(f"sklejanie strumienia wideo {folder.name}: "
                          f"{r.stderr.decode('utf-8', 'replace').strip()[:200]}")
        return
    inc.add(VIDEO, out, source=source, stream=True, segments_saved=len(parts), segments_listed=listed or None,
            origin_folder=str(folder))
    if listed > len(parts):
        inc.missing.append(f"wideo ze strony zapisane częściowo: {len(parts)} z {listed} kawałków strumienia")
    if o.video:
        from . import video as v
        info = v.probe(out)
        if info.get("has_audio"):
            audio = v.extract_audio(out, out_dir)
            if audio:
                inc.add(AUDIO, audio, source="video", video=str(out))
        for frame in v.extract_frames(out, out_dir, info.get("duration") or 0):
            inc.add(FRAME, frame, source="video", video=str(out))


def _load_folder(inc: Incident, root: Path, o: Options, source: str) -> None:
    """Folder przypadku (BACKUP/pX, DANE/<nr>, rozpakowane archiwum albo pliki z uploadu).

    Kolejność: metadane -> strony HTML -> HAR -> teksty -> zrzuty (OCR) -> wideo -> zasoby stron.
    """
    files = sorted(p for p in root.rglob("*") if p.is_file())
    # zapis narzędziem page-capture: manifest.json + resources/ (obrazy, skrypty, kawałki wideo)
    capture_roots = [p.parent for p in files if p.name == "manifest.json" and _is_page_capture(p)]
    in_capture_res = lambda p: any((r / "resources") in p.parents for r in capture_roots)
    in_assets = lambda p: any(part.endswith("_files") for part in p.relative_to(root).parts[:-1]) \
        or in_capture_res(p)
    main = [p for p in files if not in_assets(p)]
    assets = [p for p in files if in_assets(p)]
    # strumień wideo HLS (kawałki .ts + lista .m3u8) – z zasobów strony albo luzem
    ts_files = [p for p in files if p.suffix.lower() == ".ts" and _is_mpegts(p)]
    main = [p for p in main if p not in ts_files and p.suffix.lower() != ".m3u8"]

    extracted = {p.name for p in root.rglob("*") if p.is_dir()}
    by_ext: dict[str, list[Path]] = {}
    for p in main:
        ext = p.suffix.lower() or (p.name.lower() if p.name.startswith(".") else "")  # plik o nazwie „.har”
        if p.name.endswith((".sha256",)) or p.name in {"sha256.json"}:
            continue
        if ext == ".zip" and p.stem in extracted:
            continue  # archiwum już rozpakowane obok – nie czytamy dwa razy
        if ext == ".pdf":
            continue
        by_ext.setdefault(ext, []).append(p)

    for p in by_ext.get(".json", []):
        if p.parent.name == "storage":     # ciasteczka i localStorage – dla OSINT
            inc.add(JSON, p, source=source, type="storage")
        else:
            _json(inc, p, o, source)
    for ext in sorted(HTML_EXT):
        for p in by_ext.get(ext, []):
            if p.parent.name == "html" and p.name == "source.html" and (p.parent / "rendered.html").exists():
                continue  # w archiwum strony rendered.html i source.html to ta sama treść
            _html(inc, p, o, source)
    for p in by_ext.get(".har", []):
        _har(inc, p, o, source)
    for p in by_ext.get(".txt", []):
        _textfile(inc, p, o, source)
    ocr_done = 0
    for ext in sorted(IMAGE_EXT):
        for p in by_ext.get(ext, []):
            inc.add(SCREENSHOT, p, source=source)
            if o.ocr and ocr_done < o.ocr_limit:
                _ocr_into(inc, p, o)
                ocr_done += 1
    for ext in sorted(VIDEO_EXT):
        for p in by_ext.get(ext, []):
            _video(inc, p, o, source)
    for ext in sorted(AUDIO_EXT):
        for p in by_ext.get(ext, []):
            _audio(inc, p, o, source)
    for p in by_ext.get(".zip", []):
        _archive(inc, p, o, source)
    by_dir: dict[Path, list[Path]] = {}
    for p in ts_files:
        by_dir.setdefault(p.parent, []).append(p)
    for folder, parts in sorted(by_dir.items()):
        _hls_into(inc, folder, sorted(parts), o, source)

    if o.assets:
        images = [p for p in assets if p.suffix.lower() in IMAGE_EXT]
        for p in images[: o.assets_limit]:
            inc.add(IMAGE, p, source="page_assets")
        if len(images) > o.assets_limit:
            inc.missing.append(f"pominięto {len(images) - o.assets_limit} obrazów z zasobów strony (limit)")
    inc.case_dir = inc.case_dir or str(root)


# ============================================================================ pomocnicze

def _ocr_into(inc: Incident, path: Path, o: Options) -> None:
    from .screenshot import find_urls, ocr_image
    try:
        text = ocr_image(path)
    except Exception as exc:
        inc.errors.append(f"OCR {path.name}: {exc.__class__.__name__}: {exc}")
        return
    err = is_error_page("", text)
    inc.add_text(text, "ocr", path=str(path), error_page=err)
    if err:
        return  # adresy ze zrzutu strony błędu to zwykle cloudflare.com itp.
    from urllib.parse import urlsplit
    known = {(urlsplit(u if "://" in u else "https://" + u).hostname or "").lower() for u in inc.urls}
    for url in [u for u in find_urls(text) if not is_common_domain(u)][: o.max_fetch]:
        host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()
        if host not in known:  # ten sam serwis co znany adres – nic nowego
            inc.add_url(url, "ocr")


def _fetch_into(inc: Incident, url: str, o: Options) -> None:
    """Pobiera stronę raz i rozdaje składniki: HTML i tekst, HAR, zrzut, przekierowania."""
    from .url import fetch
    n = len(inc.of_kind(HTML))
    if sum(1 for a in inc.artifacts if a.source == "fetch" and a.kind == HTML) >= o.max_fetch:
        return
    res = fetch(url, Path(inc.store_dir) / f"fetch_{n + 1}")
    if res.redirect_chain and len(res.redirect_chain) > len(inc.redirect_chain):
        inc.redirect_chain = res.redirect_chain
    if res.final_url:
        inc.final_url = res.final_url
        inc.add_url(res.final_url, "fetch")
    if res.html_path:
        _html(inc, res.html_path, o, source="fetch")
    if res.har_path:
        inc.add(HAR, res.har_path, source="fetch", method=res.method)
    if res.screenshot_path:
        inc.add(SCREENSHOT, res.screenshot_path, source="fetch")
    if res.error:
        inc.errors.append(f"pobieranie {url}: {res.error}")
    if not res.ok:
        inc.status = "dead"


# ============================================================================ złożenie wyniku

MIN_WORDS = 15   # krótszy tekst (np. sam tytuł reklamy „Czytać”) ustępuje następnemu źródłu


def select_text(inc: Incident) -> None:
    """Wybiera tekst dla modułu tekstu spośród wszystkich składników TEXT materiału.

    Wywoływane w finalize() i ponownie, gdy moduł audio/wideo dopisze transkrypcję.
    """
    parts = [a for a in inc.of_kind(TEXT) if a.value and a.value.strip()]
    # tekst podany przez użytkownika: dołączamy go, o ile to coś więcej niż sam link
    from .screenshot import OCR_URL_RE
    user = [a for a in parts if a.source == "input" and len(OCR_URL_RE.sub(" ", a.value).split()) >= 3]
    good = [a for a in parts if not a.meta.get("error_page") and a.source != "input"]
    chosen_origin, chosen, fallback = None, [], None
    for origin in TEXT_PRIORITY[1:]:
        if origin in PAGE_ORIGINS:
            group = _best_per_page([a for a in good if a.source in PAGE_ORIGINS])
            origin = group[0].source if group else origin
        else:
            group = [a for a in good if a.source == origin]
        if not group:
            continue
        if sum(len(a.value.split()) for a in group) < MIN_WORDS:
            fallback = fallback or (origin, group)
            continue
        chosen_origin, chosen = origin, group
        break
    if not chosen and fallback:
        chosen_origin, chosen = fallback
    errors_only = [a for a in parts if a.source != "input"]
    note = "dostępna tylko strona błędu (np. 502) – tekst niewiarygodny"
    if not chosen and not user and errors_only:  # zostały tylko strony błędów – lepsze to niż nic
        chosen_origin, chosen = "html", errors_only
        if note not in inc.missing:
            inc.missing.append(note)
    if user:
        chosen = user + chosen
        chosen_origin = chosen_origin or "input"
    seen, texts = set(), []
    for a in chosen:
        h = sha256_text(a.value)
        if h not in seen:
            seen.add(h)
            texts.append(a.value)
    inc.text = "\n\n".join(texts)
    inc.text_origin = [{"page_text": "html", "har": "html"}.get(chosen_origin, chosen_origin)] \
        if chosen_origin else []
    if inc.text and "moduł tekstu: brak tekstu" in inc.missing:
        inc.missing.remove("moduł tekstu: brak tekstu")

    titles = [a.meta.get("title") for a in inc.of_kind(HTML, TEXT)
              if a.meta.get("title") and not a.meta.get("error_page")]
    inc.title = titles[0] if titles else next(
        (a.meta.get("title") for a in inc.of_kind(HTML, TEXT) if a.meta.get("title")), "") or ""


def finalize(inc: Incident, o: Options) -> None:
    """Wybiera tekst dla modułu tekstu, ustala status i braki, sprawdza duplikaty, zapisuje wynik."""
    select_text(inc)

    if inc.urls and not inc.final_url:
        inc.final_url = inc.redirect_chain[-1] if inc.redirect_chain else inc.urls[0]

    routes = inc.routes()
    if not inc.text:
        inc.missing.append("moduł tekstu: brak tekstu")
    if not routes["osint"]:
        inc.missing.append("moduł OSINT: brak adresu, HAR-a i strony HTML")
    if not routes["image"]:
        inc.missing.append("moduł obrazu: brak zrzutów i obrazów")
    if not routes["av"]:
        inc.missing.append("moduł audio i wideo: brak nagrań")
    if inc.status == "ok" and (not inc.text or not routes["osint"] or not routes["image"]):
        inc.status = "partial"
    if inc.status == "dead" and (inc.text or routes["image"]):
        inc.missing.append("strona nie działa – analiza oparta na zapisanych materiałach")

    inc.fingerprint = fingerprint(inc)
    if o.dedup:
        inc.duplicate_of = DedupIndex(Path(o.store_root) / "index.json").check_and_add(inc.fingerprint, inc.id)
    if o.save:
        inc.save()


# ============================================================================ przekazanie do OSINT

def run_osint(inc: Incident, enrich: bool = True) -> dict | None:
    """Uruchamia moduł OSINT kolegów (modules/osint) na składnikach materiału.

    Folder przypadku dostaje w całości (OSINT sam czyta też zasoby z *_files), pozostałe
    materiały jako listę plików HAR/HTML/JSON plus adres, jeśli strony nie pobrano.
    """
    try:
        import osint  # type: ignore  # modules/osint – musi być na PYTHONPATH
    except ImportError as exc:
        inc.errors.append(f"moduł OSINT niedostępny: {exc}")
        return None
    folder = inc.case_dir
    if inc.input_type in {"folder", "archive", "upload"} and folder:
        return osint.analyze_path(folder, enrich=enrich)
    files = [(Path(a.path).name, Path(a.path).read_bytes())
             for a in inc.of_kind(HAR, HTML, JSON) if a.path and Path(a.path).exists()]
    fetched = any(a.source == "fetch" for a in inc.artifacts)
    url = inc.urls[0] if inc.urls and not fetched else None
    if not files and not url:
        return None
    return osint.analyze_files(files, url=url, enrich=enrich)
