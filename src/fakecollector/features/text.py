"""Moduł tekstu: cechy narracji materiału i porównanie dwóch materiałów po tekście.

Najważniejsze funkcje:
    extract_text_from_html(html)      -> (tekst, metadane strony)
    load_text(path)                   -> TextSource z pliku lub folderu przypadku
    analyze_text(text, origin=...)    -> słownik cech (TextFeatures.to_dict())
    compare(a, b)                     -> podobieństwo dwóch materiałów z przesłankami
    extract(incident)                 -> adapter dla potoku (Incident lub dict)

Uruchomienie z linii poleceń (w kontenerze app):
    python -m fakecollector.features.text data/BACKUP/p1 data/BACKUP/p7 --compare
    python -m fakecollector.features.text data/BACKUP data/DANE --compare --top 15
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np

log = logging.getLogger(__name__)

LEXICON_DIR = Path(__file__).with_name("lexicons")

# Model zgodny z kolumną ad_embeddings.text_emb vector(384) i pobierany przez download_models.py.
EMBED_MODEL = os.environ.get(
    "TEXT_EMB_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
EMBEDDINGS_ON = os.environ.get("TEXT_EMBEDDINGS", "1") != "0"

NUM_PERM = 128          # długość podpisu MinHash
SHINGLE_WORDS = 5       # słowa w jednym shinglu
CHUNK_WORDS = 180       # długość fragmentu do embeddingu (MiniLM obcina ok. 128 tokenów)
MAX_CHUNKS = 8          # ile fragmentów od początku tekstu bierzemy do embeddingu
MIN_WORDS = 15          # poniżej tej liczby słów tekst uznajemy za zbyt krótki do porównań

# Wiarygodność tekstu zależnie od źródła: OCR i transkrypcja są zaszumione.
ORIGIN_WEIGHT = {"html": 1.0, "input": 1.0, "adlib": 1.0, "json": 0.9,
                 "transcript": 0.8, "ocr": 0.6}

# Kategorie narracji: opisy do klasyfikacji zero-shot (podobieństwo embeddingów)
# i słowa-wskazówki, które działają także bez modelu.
NARRATIVES: dict[str, dict[str, Any]] = {
    "fake_news_investment": {
        "label": "fałszywy artykuł: znana osoba i sposób na zarabianie",
        "desc": "Artykuł prasowy, w którym znana osoba ujawnia sposób na szybkie zarabianie "
                "pieniędzy, platformę inwestycyjną, kryptowaluty, pasywny dochód i ogromne zyski.",
        "cues": [r"inwest\w*", r"zarab\w*", r"zarob\w*", r"platform\w*", r"kryptowalut\w*",
                 r"bitcoin\w*", r"dochod\w*|dochód", r"zysk\w*", r"bank\w*"],
    },
    "fake_news_scandal": {
        "label": "sensacyjny artykuł: atak, skandal, ujawnienie",
        "desc": "Sensacyjny artykuł o ataku, zamachu, pobiciu albo skandalu wokół znanej osoby, "
                "ujawnione śledztwo, wywiad na żywo, który ktoś próbował ukryć.",
        "cues": [r"zamach\w*", r"napaś\w*|napad\w*", r"atak\w*", r"pilne", r"skandal\w*",
                 r"śledztw\w*", r"ujawni\w*", r"na żywo", r"zniszcz\w*", r"strzel\w*"],
    },
    "health_miracle": {
        "label": "cudowny lek lub suplement",
        "desc": "Reklama suplementu diety lub cudownego leku, który szybko leczy chorobę: "
                "stawy, serce, ciśnienie, cukrzyca, grzybica, prostata, odchudzanie.",
        "cues": [r"cukrzyc\w*", r"staw\w*", r"serc\w*", r"ciśnieni\w*", r"grzybic\w*",
                 r"prostat\w*", r"odchudz\w*", r"lek\b|leki\b|leku\b|lekarstw\w*",
                 r"suplement\w*", r"kapsułk\w*", r"wylecz\w*", r"chorob\w*", r"lekarz\w*"],
    },
    "shop_scam": {
        "label": "sklep-wydmuszka z promocją",
        "desc": "Sklep internetowy z ogromną promocją i rabatem, płatność przy odbiorze, "
                "ostatnie sztuki, darmowa dostawa, produkt przeceniony.",
        "cues": [r"rabat\w*", r"promocj\w*", r"przy odbiorze", r"dostaw\w*", r"zamów\w*",
                 r"koszyk\w*", r"cen\w*", r"ostatni\w* sztuk\w*", r"\bzł\b"],
    },
    "gambling": {
        "label": "hazard lub aplikacja z wygranymi",
        "desc": "Kasyno online, gra hazardowa, automaty, aplikacja z wygranymi pieniędzmi, "
                "bonus powitalny, spiny, jackpot.",
        "cues": [r"kasyn\w*", r"wygran\w*|wygra\w*", r"jackpot\w*", r"bonus\w*", r"spin\w*",
                 r"bonanza", r"zakład\w*", r"slot\w*", r"hazard\w*"],
    },
}

# Znaczniki perswazji: grupy fraz typowych dla scamu. Liczone niezależnie od kategorii.
PERSUASION = {
    "profit": [r"zarab\w*", r"zarob\w*", r"pasywn\w* dochod\w*|pasywny dochód",
               r"gwarantowan\w*", r"bez ryzyka", r"\d+\s?%\s?(?:zysk|zwrot|miesięcz|dzienn)\w*",
               r"zysk\w*", r"\bmilion\w*"],
    "urgency": [r"tylko dziś", r"ostatni\w* (?:sztuk|miejsc|szans)\w*", r"zanim (?:usuną|zniknie)",
                r"pośpiesz\w*|pospiesz\w*", r"ograniczon\w*", r"\bteraz\b", r"natychmiast"],
    "authority": [r"lekarz\w*", r"profesor\w*|\bprof\.", r"ekspert\w*", r"ojc\w*|ksiądz\w*|księdz\w*",
                  r"minister\w*", r"naukowc\w*", r"bank centraln\w*", r"rząd\w*"],
    "secrecy": [r"sekret\w*", r"ukryw\w*|ukryt\w*", r"nie chcą,? (?:żeby|abyś)", r"zakazan\w*",
                r"ujawni\w*", r"usunięt\w*|usuną\w*", r"tajn\w*"],
    "sensational": [r"szok\w*", r"pilne", r"skandal\w*", r"brutaln\w*", r"wstrząsaj\w*",
                    r"niewiarygodn\w*", r"cała polska"],
    "social_proof": [r"tysiąc\w*|tysięc\w*", r"\d[\d\s]*\s?(?:osób|polaków|użytkownik\w*)",
                     r"opini\w*", r"komentarz\w*", r"recenzj\w*"],
}

URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b[a-z0-9][a-z0-9-]{1,62}\.(?:pl|com|net|org|eu|info|"
                    r"shop|online|xyz|top|site|store|app|io|me|ly|live|click|pro|icu|life)\b(?:/\S*)?",
                    re.I)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE_RE = re.compile(r"(?:\+?48[\s-]?)?\b\d{3}[\s-]?\d{3}[\s-]?\d{3}\b")
MONEY_RE = re.compile(r"\d[\d \u00a0.,]*[ \u00a0]?(?:tys\.?[ \u00a0]?|mln[ \u00a0]?|tysięcy[ \u00a0]|"
                      r"milion\w*[ \u00a0])?(?:zł|złotych|złote|pln|eur|euro|€|usd|\$|dolar\w*)(?!\w)", re.I)
PERCENT_RE = re.compile(r"\d+(?:[.,]\d+)?\s?%")
DATE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")
NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
WORD_RE = re.compile(r"[a-ząćęłńóśźż]+", re.I)


# --------------------------------------------------------------------------- słowniki

def _read_lexicon(name: str) -> list[tuple[str, re.Pattern]]:
    out = []
    for line in (LEXICON_DIR / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        label, pattern = (p.strip() for p in line.split("|", 1))
        out.append((label, re.compile(rf"(?<![\w]){pattern}(?![\w])", re.I)))
    return out


@lru_cache(maxsize=1)
def persons_lexicon() -> list[tuple[str, re.Pattern]]:
    return _read_lexicon("osoby.txt")


@lru_cache(maxsize=1)
def brands_lexicon() -> list[tuple[str, re.Pattern]]:
    return _read_lexicon("marki.txt")


@lru_cache(maxsize=1)
def stopwords() -> frozenset[str]:
    words = (LEXICON_DIR / "stopwords_pl.txt").read_text(encoding="utf-8").split()
    return frozenset(w.lower() for w in words)


@lru_cache(maxsize=1)
def _compiled_groups() -> tuple[dict, dict]:
    narr = {k: [re.compile(p, re.I) for p in v["cues"]] for k, v in NARRATIVES.items()}
    pers = {k: [re.compile(p, re.I) for p in v] for k, v in PERSUASION.items()}
    return narr, pers


# --------------------------------------------------------------------------- wejście

@dataclass
class TextSource:
    """Tekst gotowy do analizy wraz z informacją, skąd pochodzi."""
    text: str
    origin: str = "html"                       # html / input / ocr / transcript / adlib / json
    title: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    paths: list[str] = field(default_factory=list)


_DROP_TAGS = ("script", "style", "noscript", "svg", "template", "iframe", "canvas", "form")


def extract_text_from_html(html: str) -> tuple[str, dict[str, Any]]:
    """Zwraca widoczny tekst strony i metadane (tytuł, opis, nagłówki, linki wychodzące)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    meta: dict[str, Any] = {}
    meta["title"] = (soup.title.get_text(" ", strip=True) if soup.title else "")
    for key in ("description", "og:title", "og:description", "og:site_name", "keywords"):
        tag = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
        if tag and tag.get("content"):
            meta[key] = tag["content"].strip()
    meta["headings"] = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2"])][:20]
    meta["outlinks"] = sorted({a["href"] for a in soup.find_all("a", href=True)
                               if a["href"].startswith("http")})[:200]
    # komentarz "saved from url" w stronach zapisanych przez przeglądarkę
    m = re.search(r"saved from url=\(\d+\)(\S+)", html[:3000])
    if m:
        meta["saved_from"] = m.group(1).rstrip(" -->")

    text = ""
    try:  # trafilatura lepiej wycina samą treść artykułu; jest opcjonalna
        import trafilatura  # type: ignore
        text = trafilatura.extract(html, include_comments=True, favor_recall=True) or ""
    except Exception:
        text = ""
    if len(text.split()) < 50:
        for t in soup(_DROP_TAGS):
            t.decompose()
        body = soup.body or soup
        lines = [ln.strip() for ln in body.get_text("\n").splitlines()]
        text = "\n".join(ln for ln in lines if ln)
    return text, meta


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "cp1250", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def load_text(path: str | Path) -> TextSource:
    """Wczytuje tekst z pliku (.html, .htm, .txt, .json) albo z folderu przypadku z paczki.

    W folderze (np. data/BACKUP/p1, data/DANE/61) zbiera wszystkie strony HTML
    (pomija foldery *_files z zasobami) oraz pliki .txt i .info.json.
    """
    path = Path(path)
    files = [path] if path.is_file() else sorted(
        p for p in path.rglob("*")
        if p.is_file() and not any(part.endswith("_files") for part in p.parts)
        and p.suffix.lower() in {".html", ".htm", ".txt", ".json"}
        and p.name != "sha256.json"
    )
    texts, metas, title, origin = [], [], "", "html"
    for f in files:
        suffix = f.suffix.lower()
        if suffix in {".html", ".htm"}:
            t, m = extract_text_from_html(_read_text_file(f))
            title = title or m.get("title", "")
            metas.append(m)
        elif suffix == ".json":
            try:
                data = json.loads(_read_text_file(f))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            parts = [str(data.get(k, "")) for k in ("title", "description", "ad_creative_bodies",
                                                    "page_text", "text") if data.get(k)]
            t = "\n".join(parts)
            title = title or str(data.get("title", ""))
            if not texts:
                origin = "json"
        else:
            t = _read_text_file(f)
        if t.strip():
            texts.append(t)
    meta = metas[0] if len(metas) == 1 else {"pages": metas}
    return TextSource(text="\n\n".join(texts), origin=origin if texts else "html",
                      title=title, meta=meta, paths=[str(f) for f in files])


# --------------------------------------------------------------------------- normalizacja

def normalize(text: str) -> str:
    """Małe litery, ujednolicone cudzysłowy i myślniki, pojedyncze spacje."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[‘’‚‛]", "'", text)
    text = re.sub(r"[“”„«»]", '"', text)
    text = re.sub(r"[‐-―]", "-", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def strip_diacritics(text: str) -> str:
    text = text.replace("ł", "l").replace("Ł", "L")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def tokens(text: str, drop_stopwords: bool = True) -> list[str]:
    sw = stopwords()
    words = [w.lower() for w in WORD_RE.findall(text)]
    return [w for w in words if len(w) > 1 and not (drop_stopwords and w in sw)]


# --------------------------------------------------------------------------- encje i szkielet

def find_entities(text: str) -> dict[str, Any]:
    low = normalize(text)
    persons = Counter(label for label, rx in persons_lexicon() for _ in rx.finditer(low))
    brands = Counter(label for label, rx in brands_lexicon() for _ in rx.finditer(low))
    return {
        "persons": dict(persons.most_common()),
        "brands": dict(brands.most_common()),
        "money": sorted({re.sub(r"\s+", " ", m.group(0)).strip() for m in MONEY_RE.finditer(text)})[:30],
        "percents": sorted({m.group(0).strip() for m in PERCENT_RE.finditer(text)})[:30],
        "urls": sorted({m.group(0).rstrip(".,);") for m in URL_RE.finditer(text)})[:50],
        "emails": sorted({m.group(0) for m in EMAIL_RE.finditer(text)})[:20],
        "phones": sorted({re.sub(r"\D", "", m.group(0))[-9:] for m in PHONE_RE.finditer(text)})[:20],
    }


def skeleton(text: str) -> str:
    """Tekst z podmienionymi elementami, które oszuści zmieniają między odcinkami kampanii.

    Dzięki temu ten sam szablon artykułu z inną osobą, marką, kwotą czy domeną
    daje (prawie) identyczny szkielet.
    """
    s = normalize(text)
    s = URL_RE.sub(" <URL> ", s)
    s = EMAIL_RE.sub(" <EMAIL> ", s)
    s = MONEY_RE.sub(" <KWOTA> ", s)
    s = PERCENT_RE.sub(" <PROCENT> ", s)
    s = DATE_RE.sub(" <DATA> ", s)
    s = PHONE_RE.sub(" <TELEFON> ", s)
    for _, rx in persons_lexicon():
        s = rx.sub(" <OSOBA> ", s)
    for _, rx in brands_lexicon():
        s = rx.sub(" <MARKA> ", s)
    s = NUMBER_RE.sub(" <LICZBA> ", s)
    # imiona przed <OSOBA> (np. "rafała <OSOBA>") też są zmienne między odcinkami
    s = re.sub(r"\b[a-ząćęłńóśźż]{3,}\s+<OSOBA>", "<OSOBA>", s)
    s = re.sub(r"(?:<OSOBA>\s*)+", "<OSOBA> ", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- MinHash

_MERSENNE = np.uint64((1 << 61) - 1)


@lru_cache(maxsize=1)
def _perms() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.RandomState(1)  # stałe ziarno: podpisy są porównywalne między uruchomieniami
    a = rng.randint(1, 2**31 - 1, size=NUM_PERM, dtype=np.int64).astype(np.uint64)
    b = rng.randint(0, 2**31 - 1, size=NUM_PERM, dtype=np.int64).astype(np.uint64)
    return a, b


def shingles(words: list[str], k: int = SHINGLE_WORDS) -> set[str]:
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def minhash(items: Iterable[str]) -> list[int] | None:
    """Podpis MinHash (128 liczb). Ten sam format co hashvalues w datasketch.MinHash,
    więc moduł porównania może wrzucić go do datasketch.MinHashLSH bez przeliczania."""
    hashes = np.array([int.from_bytes(hashlib.blake2b(s.encode(), digest_size=4).digest(), "little")
                       for s in items], dtype=np.uint64)
    if hashes.size == 0:
        return None
    a, b = _perms()
    vals = (np.outer(hashes, a) + b) % _MERSENNE
    return [int(v) for v in vals.min(axis=0) & np.uint64(0xFFFFFFFF)]


def minhash_jaccard(a: list[int] | None, b: list[int] | None) -> float | None:
    if not a or not b:
        return None
    return float(np.mean(np.array(a) == np.array(b)))


# --------------------------------------------------------------------------- embeddingi

@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL, device="cpu")


def _prefix(texts: list[str]) -> list[str]:
    return [f"passage: {t}" for t in texts] if "e5" in EMBED_MODEL.lower() else texts


def embed(text: str) -> list[float] | None:
    """Embedding całego tekstu: średnia z fragmentów po ~180 słów, znormalizowana do długości 1."""
    if not EMBEDDINGS_ON or not text.strip():
        return None
    words = text.split()
    chunks = [" ".join(words[i:i + CHUNK_WORDS]) for i in range(0, len(words), CHUNK_WORDS)][:MAX_CHUNKS]
    try:
        vecs = _model().encode(_prefix(chunks), normalize_embeddings=True, show_progress_bar=False)
    except Exception as exc:  # brak modelu albo biblioteki nie może zatrzymać analizy
        log.warning("Embedding niedostępny: %s", exc)
        return None
    v = np.asarray(vecs).mean(axis=0)
    n = np.linalg.norm(v)
    return (v / n).round(6).tolist() if n else None


@lru_cache(maxsize=1)
def _narrative_vectors() -> dict[str, np.ndarray] | None:
    if not EMBEDDINGS_ON:
        return None
    try:
        names = list(NARRATIVES)
        vecs = _model().encode(_prefix([NARRATIVES[n]["desc"] for n in names]),
                               normalize_embeddings=True, show_progress_bar=False)
        return dict(zip(names, np.asarray(vecs)))
    except Exception as exc:
        log.warning("Klasyfikacja narracji bez modelu: %s", exc)
        return None


def cosine(a: list[float] | None, b: list[float] | None) -> float | None:
    if a is None or b is None:
        return None
    return float(np.dot(np.asarray(a), np.asarray(b)))


# --------------------------------------------------------------------------- słowa kluczowe, narracja

def keywords(text: str, title: str = "", k: int = 15) -> list[str]:
    """Słowa i dwuwyrazowe frazy o największej częstości (tytuł liczy się trzy razy)."""
    words = tokens(f"{title} {title} {title} {text}")
    words = [w for w in words if len(w) >= 4]
    counts = Counter(words)
    bigrams = Counter(f"{a} {b}" for a, b in zip(words, words[1:]))
    for bg, c in bigrams.items():
        if c >= 2:
            counts[bg] = c * 1.5
    return [w for w, _ in counts.most_common(k)]


def persuasion_markers(text: str) -> dict[str, int]:
    low = normalize(text)
    _, groups = _compiled_groups()
    return {g: sum(len(rx.findall(low)) for rx in rxs) for g, rxs in groups.items()}


def classify_narrative(text: str, emb: list[float] | None) -> dict[str, Any]:
    """Kategoria narracji: podobieństwo do opisów kategorii (jeśli jest model) + słowa-wskazówki."""
    low = normalize(text)
    n_words = max(len(low.split()), 1)
    cues, _ = _compiled_groups()
    cue_score = {k: sum(len(rx.findall(low)) for rx in rxs) / math.sqrt(n_words)
                 for k, rxs in cues.items()}
    max_cue = max(cue_score.values()) or 1.0
    scores = {k: 0.5 * v / max_cue for k, v in cue_score.items()}
    vectors = _narrative_vectors() if emb is not None else None
    if vectors:
        e = np.asarray(emb)
        for k, v in vectors.items():
            scores[k] += 0.5 * max(float(np.dot(e, v)), 0.0)
    best = max(scores, key=scores.get)
    return {"category": best if scores[best] > 0.15 else "other",
            "label": NARRATIVES.get(best, {}).get("label", "inne") if scores[best] > 0.15 else "inne",
            "scores": {k: round(v, 3) for k, v in sorted(scores.items(), key=lambda x: -x[1])}}


def detect_language(text: str) -> str:
    words = [w.lower() for w in WORD_RE.findall(text)][:2000]
    if not words:
        return "unknown"
    pl = sum(w in {"i", "w", "na", "się", "nie", "że", "jest", "to", "do", "z"} for w in words)
    en = sum(w in {"the", "and", "of", "to", "is", "in", "for", "you", "with", "on"} for w in words)
    diacritics = sum(any(c in "ąćęłńóśźż" for c in w) for w in words)
    if pl + diacritics > en:
        return "pl"
    return "en" if en else "other"


# --------------------------------------------------------------------------- analiza

@dataclass
class TextFeatures:
    origin: str
    confidence: float                     # 0–1: jak bardzo ufać porównaniom po tekście
    n_words: int
    language: str
    title: str
    keywords: list[str]
    persons: dict[str, int]
    brands: dict[str, int]
    money: list[str]
    percents: list[str]
    urls: list[str]
    emails: list[str]
    phones: list[str]
    persuasion: dict[str, int]
    narrative: dict[str, Any]
    skeleton_preview: str                 # początek szkieletu, do podglądu w GUI
    minhash: list[int] | None
    skeleton_minhash: list[int] | None
    text_emb: list[float] | None
    skeleton_emb: list[float] | None
    text_sha256: str
    missing: list[str] = field(default_factory=list)

    def to_dict(self, with_vectors: bool = True) -> dict[str, Any]:
        d = asdict(self)
        if not with_vectors:
            for k in ("minhash", "skeleton_minhash", "text_emb", "skeleton_emb"):
                d[k] = None if d[k] is None else f"<{len(d[k])} wartości>"
        return d


def _ocr_quality(text: str) -> float:
    """Udział sensownych słów w tekście z OCR (śmieci z OCR obniżają pewność)."""
    words = WORD_RE.findall(text)
    if not words:
        return 0.0
    good = sum(len(w) >= 3 and any(v in w.lower() for v in "aeiouyąęó") for w in words)
    return good / len(words)


def analyze_text(text: str, origin: str = "html", title: str = "") -> TextFeatures:
    text = text or ""
    norm = normalize(text)
    words_all = tokens(strip_diacritics(norm))
    skel = skeleton(text)
    skel_words = tokens(strip_diacritics(skel.replace("<", " ").replace(">", " ")))
    n_words = len(norm.split())
    missing = []

    conf = ORIGIN_WEIGHT.get(origin, 0.8)
    if origin == "ocr":
        conf *= _ocr_quality(text)
    if n_words < MIN_WORDS:
        conf *= n_words / MIN_WORDS
        missing.append("tekst za krótki do pewnych porównań")
    if n_words == 0:
        missing.append("brak tekstu")

    emb = embed(text) if n_words else None
    skel_emb = embed(skel) if n_words else None
    if n_words and emb is None:
        missing.append("embedding tekstu niedostępny")

    ents = find_entities(text)
    return TextFeatures(
        origin=origin,
        confidence=round(conf, 3),
        n_words=n_words,
        language=detect_language(text),
        title=title,
        keywords=keywords(text, title),
        persons=ents["persons"],
        brands=ents["brands"],
        money=ents["money"],
        percents=ents["percents"],
        urls=ents["urls"],
        emails=ents["emails"],
        phones=ents["phones"],
        persuasion=persuasion_markers(text),
        narrative=classify_narrative(text, emb),
        skeleton_preview=skel[:400],
        minhash=minhash(shingles(words_all)),
        skeleton_minhash=minhash(shingles(skel_words)),
        text_emb=emb,
        skeleton_emb=skel_emb,
        text_sha256=hashlib.sha256(norm.encode()).hexdigest(),
        missing=missing,
    )


def extract(incident: Any) -> dict[str, Any] | None:
    """Adapter dla potoku. Przyjmuje obiekt z polami text/text_origin/title albo słownik.

    Zwraca None, gdy materiał nie ma tekstu (potok zapisze wtedy brak warstwy tekstu).
    """
    get = (lambda k, d=None: incident.get(k, d)) if isinstance(incident, dict) \
        else (lambda k, d=None: getattr(incident, k, d))
    text = get("text", "") or ""
    if not text.strip():
        return None
    origins = get("text_origin") or ["html"]
    origin = origins[0] if isinstance(origins, (list, tuple)) else str(origins)
    return analyze_text(text, origin=origin, title=get("title", "") or "").to_dict()


# --------------------------------------------------------------------------- porównanie

def _jaccard(a: Iterable[str], b: Iterable[str]) -> float | None:
    a, b = set(a), set(b)
    if not a or not b:
        return None
    return len(a & b) / len(a | b)


def compare(a: dict[str, Any] | TextFeatures, b: dict[str, Any] | TextFeatures) -> dict[str, Any]:
    """Porównanie dwóch materiałów po tekście.

    Zwraca wartości poszczególnych sygnałów (None = nie da się policzyć), pewność
    (mniejsza z pewności obu stron), łączny wynik tekstowy i przesłanki w zdaniach.
    Wagi łączenia z innymi modułami ustala moduł similarity/; tu jest tylko wynik tekstu.
    """
    a = a.to_dict() if isinstance(a, TextFeatures) else a
    b = b.to_dict() if isinstance(b, TextFeatures) else b
    s = {
        "exact_duplicate": 1.0 if a["text_sha256"] == b["text_sha256"] and a["n_words"] else 0.0,
        "text_minhash": minhash_jaccard(a["minhash"], b["minhash"]),
        "skeleton_minhash": minhash_jaccard(a["skeleton_minhash"], b["skeleton_minhash"]),
        "text_cosine": cosine(a["text_emb"], b["text_emb"]),
        "skeleton_cosine": cosine(a["skeleton_emb"], b["skeleton_emb"]),
        "keywords": _jaccard(a["keywords"], b["keywords"]),
        "same_narrative": 1.0 if a["narrative"]["category"] == b["narrative"]["category"]
                          and a["narrative"]["category"] != "other" else 0.0,
    }
    shared_persons = sorted(set(a["persons"]) & set(b["persons"]))
    shared_brands = sorted(set(a["brands"]) & set(b["brands"]))
    shared_contacts = sorted((set(a["phones"]) & set(b["phones"])) | (set(a["emails"]) & set(b["emails"])))

    # łączny wynik tekstu: ważona średnia z dostępnych sygnałów
    weights = {"text_minhash": 0.30, "skeleton_minhash": 0.25, "skeleton_cosine": 0.20,
               "text_cosine": 0.15, "keywords": 0.05, "same_narrative": 0.05}
    num = sum(w * s[k] for k, w in weights.items() if s[k] is not None)
    den = sum(w for k, w in weights.items() if s[k] is not None)
    score = num / den if den else 0.0
    if s["exact_duplicate"]:
        score = 1.0
    confidence = min(a["confidence"], b["confidence"])

    reasons = []
    if s["exact_duplicate"]:
        reasons.append("identyczny tekst (duplikat)")
    if (s["text_minhash"] or 0) >= 0.5:
        reasons.append(f"{s['text_minhash']:.0%} wspólnych fragmentów tekstu")
    elif (s["skeleton_minhash"] or 0) >= 0.5:
        reasons.append(f"ten sam szablon tekstu ({s['skeleton_minhash']:.0%} wspólnych fragmentów "
                       "po wycięciu nazwisk, marek i kwot)")
    if (s["skeleton_cosine"] or 0) >= 0.85 and (s["text_minhash"] or 0) < 0.5:
        reasons.append(f"bardzo podobna narracja (podobieństwo {s['skeleton_cosine']:.2f})")
    if s["same_narrative"]:
        reasons.append(f"ta sama kategoria narracji: {a['narrative']['label']}")
    if shared_persons:
        reasons.append("te same osoby w tekście: " + ", ".join(shared_persons))
    if shared_brands:
        reasons.append("te same podszywane marki: " + ", ".join(shared_brands))
    if shared_contacts:
        reasons.append("wspólne dane kontaktowe: " + ", ".join(shared_contacts))

    return {
        "signals": {k: (None if v is None else round(v, 3)) for k, v in s.items()},
        "shared_persons": shared_persons,
        "shared_brands": shared_brands,
        "shared_contacts": shared_contacts,
        "text_score": round(score, 3),
        "confidence": round(confidence, 3),
        "reasons": reasons,
    }


# --------------------------------------------------------------------------- CLI

def _case_dirs(paths: list[str]) -> list[Path]:
    """Ścieżka do paczki (np. data/BACKUP) zamienia się na listę jej przypadków."""
    out = []
    for p in map(Path, paths):
        if p.is_dir() and not any(f.suffix.lower() in {".html", ".htm", ".txt", ".json"}
                                  for f in p.iterdir() if f.is_file()) \
                and any(c.is_dir() for c in p.iterdir()):
            out += sorted((c for c in p.iterdir() if c.is_dir()),
                          key=lambda c: (len(c.name), c.name))
        else:
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Moduł tekstu FakeCollector")
    ap.add_argument("paths", nargs="+", help="pliki, foldery przypadków albo całe paczki (data/BACKUP)")
    ap.add_argument("--compare", action="store_true", help="porównaj wszystkie pary")
    ap.add_argument("--top", type=int, default=20, help="ile najpodobniejszych par pokazać")
    ap.add_argument("--json", help="zapisz pełne cechy do pliku JSON")
    ap.add_argument("--no-embeddings", action="store_true", help="bez modelu (szybki test)")
    args = ap.parse_args(argv)

    global EMBEDDINGS_ON
    if args.no_embeddings:
        EMBEDDINGS_ON = False

    results: dict[str, dict[str, Any]] = {}
    for case in _case_dirs(args.paths):
        src = load_text(case)
        feats = analyze_text(src.text, origin=src.origin, title=src.title)
        name = f"{case.parent.name}/{case.name}"
        results[name] = feats.to_dict()
        n = feats.narrative
        print(f"\n== {name}  ({feats.n_words} słów, pewność {feats.confidence})")
        print(f"   tytuł:     {src.title[:110]}")
        print(f"   narracja:  {n['label']}")
        print(f"   osoby:     {', '.join(feats.persons) or '-'}")
        print(f"   marki:     {', '.join(feats.brands) or '-'}")
        print(f"   kwoty:     {', '.join(feats.money[:5]) or '-'}")
        print(f"   perswazja: {', '.join(f'{k}={v}' for k, v in feats.persuasion.items() if v) or '-'}")
        print(f"   słowa:     {', '.join(feats.keywords[:10])}")
        if feats.missing:
            print(f"   braki:     {'; '.join(feats.missing)}")

    if args.compare and len(results) > 1:
        names = list(results)
        pairs = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                pairs.append((names[i], names[j], compare(results[names[i]], results[names[j]])))
        pairs.sort(key=lambda p: -p[2]["text_score"])
        print(f"\n== Najbardziej podobne pary (top {args.top})")
        for a, b, c in pairs[:args.top]:
            sig = c["signals"]
            print(f"{c['text_score']:.2f}  {a}  <->  {b}   "
                  f"[tekst {sig['text_minhash']}, szablon {sig['skeleton_minhash']}, "
                  f"cos {sig['text_cosine']}, szablon-cos {sig['skeleton_cosine']}]")
            for r in c["reasons"]:
                print(f"        - {r}")

    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nZapisano cechy do {args.json}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
