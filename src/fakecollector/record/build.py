"""Rekord materiału: jedno miejsce na wszystko, co moduły wiedzą o materiale.

Rekord ma cztery części:
  summary   – krótki opis do listy i GUI (tytuł, domena, osoby, status, braki),
  features  – pełne cechy każdego modułu (z wektorami), żeby etap grupowania mógł wywołać
              compare() modułów bez ponownej analizy,
  vectors   – wektory do szukania podobnych w pgvector (tekst, szkielet, obraz, twarze, głos),
  keys      – klucze do szukania dokładnych trafień (hash tekstu, pasma MinHash, domena,
              piksel FB, hash obrazu, QR, odcisk dźwięku…).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .pipeline import ModuleResults

SCHEMA_VERSION = 1

# Pasma MinHash (LSH): 32 pasma po 4 liczby. Dwa teksty o podobieństwie Jaccarda 0,5 mają
# wspólne pasmo z prawdopodobieństwem ~87%, przy 0,2 – ~5%. Wystarczy do wyszukania kandydatów.
LSH_BANDS, LSH_ROWS = 32, 4
MIN_WORDS_FOR_TEXT_KEYS = 15
OSINT_MIN_WEIGHT = 0.5            # słabsze pivoty (rejestrator, TLD, CDN) nie służą do wyszukiwania
AUDIO_SAMPLE_MOD = 16             # co 16. hash odcisku dźwięku jako klucz (deterministyczna próbka)

# Waga klucza = jak mocno samo trafienie wskazuje na powiązanie (do kolejności kandydatów).
KEY_WEIGHTS = {
    "text_sha256": 1.0, "text_lsh": 0.6, "skel_lsh": 0.5, "url": 1.0, "domain": 0.9,
    "person": 0.3, "person_visual": 0.4, "phone": 0.9, "email": 0.9,
    "img_sha256": 1.0, "img_phash": 0.8, "qr": 0.9, "audio_lm": 0.9,
}

VECTOR_FIELDS = {"clip_emb", "text_emb", "skeleton_emb", "minhash", "skeleton_minhash", "voice_emb", "audio_fp_hashes",
                 "audio_fp_times", "video_fp", "wektor", "wektor_drugi", "wektory", "wektor_sredni"}


@dataclass
class MaterialRecord:
    id: str
    summary: dict[str, Any]
    features: dict[str, Any] = field(default_factory=dict)        # moduł -> pełne cechy
    versions: dict[str, str] = field(default_factory=dict)        # moduł -> wersja modelu/schematu
    vectors: list[tuple[str, str, int, list[float]]] = field(default_factory=list)   # rodzaj, model, nr, wartości
    keys: list[tuple[str, str, float]] = field(default_factory=list)                 # rodzaj, wartość, waga


# ============================================================================ pomocnicze

def _registered_domain(url: str | None) -> str | None:
    """Domena rejestrowalna adresu (example.co.uk), liczona tak samo jak w module OSINT."""
    from fakecollector.features.osint.utils import host_of, split_host
    host = host_of(url if url and "://" in url else f"https://{url}") if url else ""
    return split_host(host)["registered_domain"] if host else None


def lsh_bands(signature: list[int] | None) -> list[str]:
    if not signature or len(signature) < LSH_BANDS * LSH_ROWS:
        return []
    out = []
    for b in range(LSH_BANDS):
        chunk = ",".join(str(v) for v in signature[b * LSH_ROWS:(b + 1) * LSH_ROWS])
        out.append(f"{b}:{hashlib.blake2b(chunk.encode(), digest_size=8).hexdigest()}")
    return out


def strip_vectors(obj: Any) -> Any:
    """Kopia bez długich wektorów (dla frontu i podglądu)."""
    if isinstance(obj, dict):
        return {k: (f"<{len(v)} wartości>" if k in VECTOR_FIELDS and isinstance(v, list)
                    else ({"model": v.get("model"), "wymiar": v.get("wymiar")} if k in VECTOR_FIELDS
                          and isinstance(v, dict) else strip_vectors(v)))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_vectors(v) for v in obj]
    return obj


# ============================================================================ budowa

def build_record(inc: Any, res: ModuleResults) -> MaterialRecord:
    t, a, o, im = res.text, res.av, res.osint, res.image
    from fakecollector.ingest.core import is_common_domain
    domains = sorted(d for d in set((o or {}).get("landing", {}).get("registered_domains") or [])
                     if not is_common_domain(d))
    final_url = inc.final_url or (inc.urls[0] if inc.urls else None)
    main_domain = domains[0] if domains else _registered_domain(final_url)
    if main_domain and is_common_domain(main_domain):
        main_domain = None        # np. facebook.com z linku do Biblioteki Reklam – nie jest domeną scamu

    modules = {}
    for name, data in (("text", t), ("av", a), ("osint", o), ("image", im)):
        if name in res.errors:
            modules[name] = {"status": "błąd", "error": res.errors[name]}
        elif data is not None:
            conf = data.get("confidence") if isinstance(data, dict) else None
            modules[name] = {"status": "ok", "confidence": conf}
        else:
            modules[name] = {"status": "pominięty", "reason": res.skipped.get(name, "brak danych")}
        modules[name]["seconds"] = res.timings.get(name)

    missing = list(dict.fromkeys(inc.missing + (t or {}).get("missing", []) + (a or {}).get("missing", [])))
    summary = {
        "source": inc.source, "input_type": inc.input_type, "status": inc.status,
        "fingerprint": inc.fingerprint, "duplicate_of": inc.duplicate_of,
        "title": inc.title or (o or {}).get("landing", {}).get("title"),
        "final_url": final_url, "domain": main_domain, "domains": domains,
        "text_origin": inc.text_origin[0] if inc.text_origin else None,
        "n_words": (t or {}).get("n_words") or 0, "text": inc.text,
        "persons": sorted((t or {}).get("persons", {})),
        "persons_visual": (im or {}).get("persons_visual", []),
        "brands": sorted((t or {}).get("brands", {})),
        "modules": modules, "missing": missing,
        "errors": inc.errors + [f"{k}: {v}" for k, v in res.errors.items()],
        "incident": inc.to_dict(with_text=False), "store_dir": inc.store_dir,
    }
    rec = MaterialRecord(id=inc.id, summary=summary)

    # ---- pełne cechy i wersje
    if t:
        from fakecollector.features import text as text_module
        rec.features["text"], rec.versions["text"] = t, text_module.EMBED_MODEL
    if a:
        from fakecollector.features import av as av_module
        rec.features["av"] = a
        rec.versions["av"] = f"{av_module.WHISPER_MODEL}+{av_module.VOICE_MODEL}"
    if o:
        rec.features["osint"], rec.versions["osint"] = o, str(o.get("schema_version") or "osint")
    if im:
        rec.features["image"], rec.versions["image"] = im, "obraz-v1"

    _add_vectors(rec)
    _add_keys(rec, inc)
    return rec


def _add_vectors(rec: MaterialRecord) -> None:
    t, a, im = rec.features.get("text"), rec.features.get("av"), rec.features.get("image")
    model = rec.versions.get("text", "")
    if t and t.get("text_emb"):
        rec.vectors.append(("text", model, 0, t["text_emb"]))
    if t and t.get("skeleton_emb"):
        rec.vectors.append(("text_skeleton", model, 0, t["skeleton_emb"]))
    if a:
        for i, item in enumerate(a.get("items", [])):
            if item.get("voice_emb"):
                rec.vectors.append(("voice", "ecapa-voxceleb", i, item["voice_emb"]))
    if im:
        for i, item in enumerate(im.get("items", [])):
            w = item["cechy"].get("wektor")
            if w and w.get("wartosci"):
                rec.vectors.append(("image", w["model"], i, w["wartosci"]))
        try:
            from .image_bridge import face_vectors
            for face_model, nr, values in face_vectors(im):
                rec.vectors.append(("face", face_model, nr, values))
        except Exception as exc:  # noqa: BLE001
            rec.summary["errors"].append(f"wektory twarzy: {exc}")


def _add_keys(rec: MaterialRecord, inc: Any) -> None:
    keys: dict[tuple[str, str], float] = {}

    def add(kind: str, value: Any, weight: float | None = None):
        if value is None or value == "":
            return
        keys[(kind, str(value))] = max(keys.get((kind, str(value)), 0.0),
                                       weight if weight is not None else KEY_WEIGHTS.get(kind, 0.5))

    # adresy
    from fakecollector.ingest.dedup import normalize_url
    for u in {rec.summary["final_url"], *(inc.urls[:3])}:
        if u:
            add("url", normalize_url(u))
    for d in rec.summary["domains"] or ([rec.summary["domain"]] if rec.summary["domain"] else []):
        add("domain", d)

    # tekst
    t = rec.features.get("text")
    if t and (t.get("n_words") or 0) >= MIN_WORDS_FOR_TEXT_KEYS:
        add("text_sha256", t.get("text_sha256"))
        for band in lsh_bands(t.get("minhash")):
            add("text_lsh", band)
        for band in lsh_bands(t.get("skeleton_minhash")):
            add("skel_lsh", band)
    for p in rec.summary["persons"]:
        add("person", p)
    for p in rec.summary["persons_visual"]:
        add("person_visual", p)
    for ph in (t or {}).get("phones", []):
        add("phone", ph)
    for em in (t or {}).get("emails", []):
        add("email", em.lower())

    # OSINT: pivoty o wadze >= 0.5 (domena, piksele, kit, certyfikat, IP poza CDN…)
    for p in (rec.features.get("osint") or {}).get("pivots", []):
        w = float(p.get("weight") or 0)
        if w >= OSINT_MIN_WEIGHT and p.get("value") is not None and p.get("kind") != "dom_simhash":
            add(f"osint:{p['kind']}", p["value"], w)

    # obraz
    for item in (rec.features.get("image") or {}).get("items", []):
        c = item["cechy"]
        if item["rola"] == "obrazek_strony" and c.get("ubogi_w_tresc"):
            continue
        add("img_sha256", c.get("sha256") or None)
        for h in (c.get("hashe") or {}).get("phash", [])[:1]:    # wariant „cały”
            if not (c.get("ubogi_w_tresc") or c.get("za_maly")):
                add("img_phash", h)
        for q in c.get("qr", []):
            add("qr", (q.get("tekst") or "").strip().lower())

    # audio: deterministyczna próbka odcisku (ta sama ścieżka => te same hashe)
    for item in (rec.features.get("av") or {}).get("items", []):
        for h in set(item.get("audio_fp_hashes") or []):
            if h % AUDIO_SAMPLE_MOD == 0:
                add("audio_lm", h)

    rec.keys = [(k, v, w) for (k, v), w in keys.items()]


def front_view(rec: MaterialRecord) -> dict[str, Any]:
    """Rekord do API/GUI: bez wektorów i surowych odcisków."""
    return {"id": rec.id, **{k: v for k, v in rec.summary.items() if k not in {"incident", "text"}},
            "text_preview": (rec.summary.get("text") or "")[:1000],
            "features": strip_vectors(rec.features), "versions": rec.versions,
            "counts": {"vectors": len(rec.vectors), "keys": len(rec.keys)}}
