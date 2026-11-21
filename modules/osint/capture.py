"""Wczytywanie materiałów do jednej, wspólnej reprezentacji `Capture`.

Obsługiwane wejścia:
  * .har            – zapis ruchu z DevTools / Playwright / "page-capture"
  * .html/.htm      – strona zapisana w przeglądarce ("saved from url=..." w komentarzu)
  * .mhtml/.mht     – archiwum strony (Chrome "Zapisz jako jeden plik")
  * manifest.json   – manifest narzędzia page-capture (URL, czas, strumienie wideo)
  * *.info.json     – metadane yt-dlp z Biblioteki Reklam Meta (ID strony reklamodawcy)
  * .zip / katalog  – cały folder incydentu (wszystko powyżej + pliki z *_files/)
Każdy loader zwraca zasoby (Resource) i dokumenty HTML (Document) – dalsze ekstraktory
nie muszą wiedzieć, skąd dane przyszły."""
from __future__ import annotations

import base64
import email
import email.policy
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from . import config
from .utils import sha256

_SAVED_FROM = re.compile(r"<!--\s*saved from url=\(\d+\)(\S+?)\s*-->", re.I)
_SAVED_SUFFIX = re.compile(r"\.(pobrane|download|descarga|herunterladen|téléchargement)$", re.I)


@dataclass
class Resource:
    url: str
    method: str = "GET"
    status: int | None = None
    mime: str = ""
    resource_type: str = ""          # document / script / stylesheet / image / xhr / fetch / media / ...
    headers: list[tuple[str, str]] = field(default_factory=list)   # nazwy małymi literami
    req_headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes | None = None
    body_sha256: str | None = None
    size: int = 0
    ip: str | None = None
    redirect_url: str | None = None
    started: str | None = None
    initiator: str | None = None
    source: str = ""                 # skąd: har:<plik>, mhtml:<plik>, saved_files:<plik>, live
    cookies: list[str] = field(default_factory=list)

    def header(self, name: str) -> str | None:
        name = name.lower()
        for k, v in self.headers:
            if k == name:
                return v
        return None


@dataclass
class Document:
    url: str | None
    html: str
    source: str
    kind: str                        # har / saved_html / mhtml / live / dom


@dataclass
class Capture:
    page_url: str | None = None
    observed_at: str | None = None
    title: str | None = None
    resources: list[Resource] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    inputs: list[dict] = field(default_factory=list)
    streams: list[str] = field(default_factory=list)
    ad_library: list[dict] = field(default_factory=list)
    live_chain: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def merge(self, other: "Capture") -> None:
        self.page_url = self.page_url or other.page_url
        self.observed_at = min(filter(None, [self.observed_at, other.observed_at]), default=None)
        self.title = self.title or other.title
        self.resources += other.resources
        self.documents += other.documents
        self.inputs += other.inputs
        self.streams += other.streams
        self.ad_library += other.ad_library
        self.live_chain += other.live_chain
        self.notes += other.notes


def _keep_body(mime: str, url: str) -> bool:
    m = (mime or "").lower()
    if any(x in m for x in ("html", "javascript", "ecmascript", "css", "json", "text/plain", "xml", "svg")):
        return True
    if "image" in m and ("icon" in m or url.lower().split("?")[0].endswith(".ico")):
        return True
    return False


# --------------------------------------------------------------------------- HAR
def load_har(data: bytes, name: str) -> Capture:
    har = json.loads(data.decode("utf-8-sig", "replace"))
    log = har.get("log", har)
    cap = Capture(inputs=[{"kind": "har", "name": name, "sha256": sha256(data),
                           "creator": (log.get("creator") or {}).get("name")}])
    pages = log.get("pages") or []
    for pg in pages:
        t = pg.get("title") or ""
        if t.startswith(("http://", "https://")) and not cap.page_url:
            cap.page_url = t
        elif t and not t.startswith("file:") and not cap.title:
            cap.title = t
        cap.observed_at = cap.observed_at or pg.get("startedDateTime")

    for e in log.get("entries") or []:
        req, resp = e.get("request") or {}, e.get("response") or {}
        url = req.get("url") or ""
        if not url.startswith(("http://", "https://")):
            continue
        content = resp.get("content") or {}
        mime = (content.get("mimeType") or "").split(";")[0].strip()
        raw = content.get("text")
        body = None
        if raw:
            try:
                body = base64.b64decode(raw) if content.get("encoding") == "base64" else raw.encode("utf-8", "replace")
            except (ValueError, TypeError):
                body = None
        hdrs = [(h.get("name", "").lower(), h.get("value", "")) for h in resp.get("headers") or []]
        cookies = [c.get("name") for c in resp.get("cookies") or [] if c.get("name")]
        cookies += [v.split("=", 1)[0].strip() for k, v in hdrs if k == "set-cookie"]
        init = e.get("_initiator") or {}
        init_url = init.get("url")
        if not init_url and isinstance(init.get("stack"), dict):
            frames = init["stack"].get("callFrames") or []
            init_url = frames[0].get("url") if frames else None
        r = Resource(
            url=url, method=req.get("method", "GET"), status=resp.get("status"), mime=mime,
            resource_type=(e.get("_resourceType") or "").lower(), headers=hdrs,
            req_headers=[(h.get("name", "").lower(), h.get("value", "")) for h in req.get("headers") or []],
            body_sha256=sha256(body) if body else None, size=len(body) if body else 0,
            body=body if body and _keep_body(mime, url) and len(body) <= config.MAX_BODY_BYTES else None,
            ip=(e.get("serverIPAddress") or "").strip("[]") or None,
            redirect_url=resp.get("redirectURL") or None, started=e.get("startedDateTime"),
            initiator=init_url, source=f"har:{name}", cookies=[c for c in cookies if c],
        )
        cap.resources.append(r)
        if r.resource_type == "document" or (not r.resource_type and "html" in mime):
            if body and "html" in mime:
                cap.documents.append(Document(url, body.decode("utf-8", "replace"), f"har:{name}", "har"))
        cap.observed_at = cap.observed_at or r.started

    if not cap.page_url:
        docs = [r for r in cap.resources if r.resource_type == "document" and (r.status or 0) < 400 and r.status]
        if docs:
            cap.page_url = docs[0].url
    return cap


# --------------------------------------------------------------------------- HTML
def load_html(data: bytes, name: str) -> Capture:
    html = data.decode("utf-8", "replace")
    m = _SAVED_FROM.search(html[:5000])
    url = m.group(1) if m and m.group(1).startswith("http") else None
    cap = Capture(page_url=url, inputs=[{"kind": "html", "name": name, "sha256": sha256(data)}])
    if m and not url:
        cap.notes.append(f"{name}: zapisany z '{m.group(1)}' – brak oryginalnego URL")
    cap.documents.append(Document(url, html, f"html:{name}", "saved_html"))
    return cap


# --------------------------------------------------------------------------- MHTML
def load_mhtml(data: bytes, name: str) -> Capture:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    cap = Capture(inputs=[{"kind": "mhtml", "name": name, "sha256": sha256(data)}])
    cap.page_url = msg.get("Snapshot-Content-Location")
    cap.title = str(msg.get("Subject") or "") or None
    for part in msg.walk():
        if part.is_multipart():
            continue
        loc = part.get("Content-Location") or ""
        ctype = part.get_content_type()
        try:
            body = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001 – uszkodzona część archiwum nie może wywrócić analizy
            continue
        if ctype == "text/html" and not cap.documents:
            cap.documents.append(Document(loc or cap.page_url, body.decode("utf-8", "replace"), f"mhtml:{name}", "mhtml"))
            cap.page_url = cap.page_url or (loc if loc.startswith("http") else None)
        if loc.startswith("http"):
            cap.resources.append(Resource(
                url=loc, mime=ctype, status=200, source=f"mhtml:{name}", size=len(body),
                body_sha256=sha256(body) if body else None,
                body=body if _keep_body(ctype, loc) and len(body) <= config.MAX_BODY_BYTES else None,
                resource_type=_guess_type(ctype, loc)))
    return cap


def _guess_type(mime: str, url: str) -> str:
    m, u = mime.lower(), url.lower().split("?")[0]
    if "javascript" in m or u.endswith(".js"):
        return "script"
    if "css" in m or u.endswith(".css"):
        return "stylesheet"
    if m.startswith("image/"):
        return "image"
    if m.startswith(("video/", "audio/")):
        return "media"
    if "html" in m:
        return "document"
    return "other"


# --------------------------------------------------------------------------- manifest / yt-dlp
def load_json(data: bytes, name: str) -> Capture | None:
    try:
        j = json.loads(data.decode("utf-8-sig", "replace"))
    except json.JSONDecodeError:
        return None
    if not isinstance(j, dict):
        return None
    if "page_url" in j and "artifacts" in j:            # page-capture manifest
        return Capture(page_url=j.get("page_url"), observed_at=j.get("captured_at"), title=j.get("title"),
                       streams=list(j.get("streams") or []),
                       inputs=[{"kind": "capture_manifest", "name": name, "tool": j.get("tool")}])
    if j.get("extractor_key") == "FacebookAds" or "ads/library" in str(j.get("webpage_url", "")):
        info = {
            "ad_archive_id": str(j.get("playlist_id") or j.get("id") or "").split("_")[0] or None,
            "ad_library_url": j.get("playlist_webpage_url") or j.get("webpage_url"),
            "page_name": j.get("playlist_uploader") or j.get("uploader"),
            "page_id": str(j.get("playlist_uploader_id") or j.get("uploader_id") or "") or None,
            "title": j.get("title"),
            "epoch": j.get("epoch"),
        }
        return Capture(ad_library=[info], inputs=[{"kind": "ad_library_info", "name": name}])
    return None


# --------------------------------------------------------------------------- dispatcher
def load_bytes(data: bytes, name: str) -> Capture | None:
    low = name.lower()
    if low.endswith(".har"):
        return load_har(data, name)
    if low.endswith((".html", ".htm", ".xhtml")):
        return load_html(data, name)
    if low.endswith((".mhtml", ".mht")):
        return load_mhtml(data, name)
    if low.endswith(".json"):
        return load_json(data, name)
    if low.endswith(".zip"):
        return load_zip(data, name)
    head = data[:2048].lstrip()
    if head.startswith(b"{") and b'"log"' in data[:4096]:
        return load_har(data, name)
    if head[:15].lower().startswith((b"<!doctype", b"<html", b"<!--")):
        return load_html(data, name)
    if b"MIME-Version" in head[:500] and b"multipart/related" in data[:2000]:
        return load_mhtml(data, name)
    return None


def _saved_file_resource(data: bytes, rel: str) -> Resource | None:
    """Plik z katalogu *_files/ (zapis strony w przeglądarce). Brak oryginalnego URL – zostaje
    nazwa pliku i hash treści, co wystarcza do porównania skryptów/stylów między stronami."""
    fname = _SAVED_SUFFIX.sub("", rel.rsplit("/", 1)[-1])
    low = fname.lower()
    if low.endswith(".js"):
        rtype, mime = "script", "application/javascript"
    elif low.endswith(".css"):
        rtype, mime = "stylesheet", "text/css"
    elif low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico")):
        rtype, mime = "image", "image/" + low.rsplit(".", 1)[-1]
    else:
        return None
    return Resource(url=f"saved:///{fname}", mime=mime, status=200, resource_type=rtype,
                    body=data if rtype != "image" and len(data) <= config.MAX_BODY_BYTES else None,
                    body_sha256=sha256(data), size=len(data), source=f"saved_files:{rel}")


def _load_tree(items: list[tuple[str, bytes]], label: str) -> Capture:
    cap = Capture()
    for rel, data in items:
        parts = rel.replace("\\", "/").split("/")
        if any(p.endswith("_files") for p in parts[:-1]):
            if rel.lower().endswith((".html", ".htm")):
                continue  # iframe'y/embed z _files – pomijamy, żeby nie mylić z głównym dokumentem
            r = _saved_file_resource(data, rel)
            if r:
                cap.resources.append(r)
            continue
        if "/resources/" in "/" + "/".join(parts):   # page-capture: treść jest też w HAR
            continue
        sub = load_bytes(data, rel)
        if sub:
            cap.merge(sub)
    cap.inputs.insert(0, {"kind": "bundle", "name": label, "files": len(items)})
    return cap


def load_zip(data: bytes, name: str) -> Capture:
    items = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        total = 0
        for zi in z.infolist():
            if zi.is_dir():
                continue
            zn = _zip_name(zi)
            if zn.lower().endswith(_SKIP_EXT) and "_files/" not in zn:
                continue
            total += zi.file_size
            if total > config.MAX_UPLOAD_BYTES:
                break
            items.append((zn, z.read(zi)))
    return _load_tree(items, name)


def _zip_name(zi: zipfile.ZipInfo) -> str:
    n = zi.filename
    if not zi.flag_bits & 0x800:       # bez flagi UTF-8 – próbujemy odzyskać polskie znaki
        try:
            n = n.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return unquote(n)


_SKIP_EXT = (".png", ".jpg", ".jpeg", ".mp4", ".mov", ".webm", ".m4a", ".mp3", ".ts")


def load_dir(path: Path) -> Capture:
    items = []
    for p in sorted(path.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(path))
        in_files = any(x.endswith("_files") for x in p.parts)
        if p.suffix.lower() in _SKIP_EXT and not in_files:
            continue  # zrzuty ekranu i wideo to robota innych modułów
        if p.stat().st_size > config.MAX_UPLOAD_BYTES:
            continue
        items.append((rel, p.read_bytes()))
    return _load_tree(items, path.name)
