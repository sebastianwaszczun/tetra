"""Wspólny format materiału: Incident i jego składniki (Artifact).

Preprocessing zamienia każde wejście (tekst, URL, zrzut, wideo, HAR, HTML, folder
z paczki) na jeden Incident. Moduły cech biorą z niego tylko to, czego potrzebują.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Rodzaje składników materiału
HTML = "html"              # strona internetowa (zapisana albo pobrana)
HAR = "har"                # zapis ruchu sieciowego
SCREENSHOT = "screenshot"  # zrzut ekranu reklamy albo strony
IMAGE = "image"            # obraz z zasobów strony (zdjęcie, baner)
VIDEO = "video"            # plik wideo
AUDIO = "audio"            # ścieżka dźwiękowa wyciągnięta z wideo
FRAME = "frame"            # klatka wyciągnięta z wideo
URL = "url"                # adres strony (podany, znaleziony w tekście, OCR, HAR, metadanych)
TEXT = "text"              # fragment tekstu z określonego źródła
JSON = "json"              # metadane (metadata.json, .info.json, dns_ssl.json…)

# Który moduł cech dostaje które składniki
ROUTES: dict[str, set[str]] = {
    "text": {TEXT},
    "osint": {URL, HAR, HTML, JSON},   # moduł OSINT (modules/osint) sam czyta też zasoby z *_files
    "image": {SCREENSHOT, IMAGE, FRAME},
    "av": {VIDEO, AUDIO},
}


@dataclass
class Artifact:
    """Jeden składnik materiału."""
    kind: str                         # jeden z rodzajów powyżej
    path: str | None = None           # plik na dysku (jeśli jest)
    value: str | None = None          # wartość bez pliku: adres URL albo tekst
    source: str = ""                  # skąd się wziął: input, folder, fetch, ocr, har, metadata, video, archive
    meta: dict[str, Any] = field(default_factory=dict)
    sha256: str | None = None


@dataclass
class Incident:
    """Jeden analizowany materiał – niezależnie od tego, w jakiej formie przyszedł."""
    input_type: str                   # text / url / image / video / har / html / folder / archive / json
    source: str                       # to, co podano na wejściu (ścieżka, URL albo początek tekstu)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    store_dir: str | None = None      # folder z plikami pobranymi i wytworzonymi dla tego materiału
    case_dir: str | None = None       # folder przypadku, z którego wczytano pliki (dla modułu OSINT)

    # dla modułu tekstu (zgodne z fakecollector.features.text.extract)
    text: str = ""
    text_origin: list[str] = field(default_factory=list)
    title: str = ""

    # adresy i przekierowania
    urls: list[str] = field(default_factory=list)          # wszystkie znane adresy, pierwszy = główny
    final_url: str | None = None
    redirect_chain: list[str] = field(default_factory=list)

    artifacts: list[Artifact] = field(default_factory=list)
    status: str = "ok"                # ok / partial (czegoś brakuje) / dead (strona nie działa)
    fingerprint: str | None = None    # odcisk wejścia do wykrywania duplikatów
    duplicate_of: str | None = None   # id wcześniejszego materiału z tym samym odciskiem
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- dodawanie składników
    def add(self, kind: str, path: str | Path | None = None, value: str | None = None,
            source: str = "", **meta: Any) -> Artifact:
        art = Artifact(kind=kind, path=str(path) if path else None, value=value, source=source, meta=meta)
        if art.path and kind not in {AUDIO, FRAME}:
            from .dedup import sha256_file
            try:
                art.sha256 = sha256_file(art.path)
            except OSError as exc:
                self.errors.append(f"nie można odczytać {art.path}: {exc}")
        self.artifacts.append(art)
        return art

    def add_url(self, url: str, source: str) -> None:
        from .dedup import normalize_url
        url = url.strip()
        if not url:
            return
        if normalize_url(url) in {normalize_url(u) for u in self.urls}:
            return
        self.urls.append(url)
        self.add(URL, value=url, source=source)

    def add_text(self, text: str, origin: str, path: str | None = None, **meta: Any) -> None:
        if text and text.strip():
            self.add(TEXT, path=path, value=text, source=origin, **meta)

    # ---------------------------------------------------------------- odczyt
    def of_kind(self, *kinds: str) -> list[Artifact]:
        return [a for a in self.artifacts if a.kind in kinds]

    def for_module(self, module: str) -> list[Artifact]:
        """Składniki dla modułu: text, osint, image, av."""
        return self.of_kind(*ROUTES[module])

    def routes(self) -> dict[str, int]:
        """Ile składników dostaje każdy moduł (do podglądu i w GUI)."""
        return {m: len(self.for_module(m)) for m in ROUTES}

    def to_dict(self, with_text: bool = True) -> dict[str, Any]:
        d = asdict(self)
        if not with_text:
            d["text"] = f"<{len(self.text.split())} słów>"
            for a in d["artifacts"]:
                if a["kind"] == TEXT and a["value"]:
                    a["value"] = f"<{len(a['value'].split())} słów>"
        return d

    def save(self, path: str | Path | None = None) -> Path:
        """Zapisuje incident.json w folderze materiału."""
        target = Path(path) if path else Path(self.store_dir or ".") / "incident.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "Incident":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data["artifacts"] = [Artifact(**a) for a in data.get("artifacts", [])]
        return cls(**data)
