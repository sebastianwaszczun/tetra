"""Zapis i odczyt rekordów materiału w Postgresie (pgvector) oraz wyszukiwanie kandydatów.

Tabele (tworzone automatycznie przy pierwszym użyciu, także w istniejącej bazie):
  materials          – jeden wiersz na materiał: podsumowanie, teczka, miejsce na kampanię,
  material_features  – pełne cechy każdego modułu (JSONB, z wektorami) do porównań,
  material_vectors   – wektory do wyszukiwania podobnych (pgvector, cosinus),
  material_keys      – klucze dokładnych trafień (hash tekstu, pasmo MinHash, domena, piksel, QR…).
"""
from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Iterator

from fakecollector.features.osint.utils import hamming_hex

from .build import KEY_WEIGHTS, MaterialRecord

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS materials (
    id               TEXT PRIMARY KEY,               -- id z preprocessingu (Incident.id)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    source           TEXT,
    input_type       TEXT,
    status           TEXT,                           -- ok / partial / dead
    fingerprint      TEXT,
    duplicate_of     TEXT,
    title            TEXT,
    final_url        TEXT,
    domain           TEXT,
    domains          TEXT[] NOT NULL DEFAULT '{}',
    text_origin      TEXT,
    n_words          INT NOT NULL DEFAULT 0,
    text             TEXT,
    persons          TEXT[] NOT NULL DEFAULT '{}',
    persons_visual   TEXT[] NOT NULL DEFAULT '{}',
    brands           TEXT[] NOT NULL DEFAULT '{}',
    modules          JSONB NOT NULL DEFAULT '{}'::jsonb,   -- stan modułów: ok / błąd / pominięty
    missing          JSONB NOT NULL DEFAULT '[]'::jsonb,
    errors           JSONB NOT NULL DEFAULT '[]'::jsonb,
    incident         JSONB,                                -- teczka z preprocessingu (bez tekstów)
    store_dir        TEXT,
    schema_version   INT NOT NULL DEFAULT 1,
    -- wypełnia etap grupowania:
    campaign_id      BIGINT,
    assignment       TEXT,                           -- przypisany / do_weryfikacji / nowy_wzorzec
    assignment_score REAL,
    assigned_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS materials_fingerprint ON materials (fingerprint);
CREATE INDEX IF NOT EXISTS materials_campaign ON materials (campaign_id);
CREATE INDEX IF NOT EXISTS materials_domain ON materials (domain);

CREATE TABLE IF NOT EXISTS material_features (
    material_id  TEXT NOT NULL REFERENCES materials (id) ON DELETE CASCADE,
    module       TEXT NOT NULL,                      -- text / av / osint / image
    version      TEXT,
    data         JSONB NOT NULL,
    PRIMARY KEY (material_id, module)
);

CREATE TABLE IF NOT EXISTS material_vectors (
    material_id  TEXT NOT NULL REFERENCES materials (id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,                      -- text / text_skeleton / image / face / voice
    model        TEXT NOT NULL,
    item         INT NOT NULL DEFAULT 0,             -- numer obrazu, twarzy albo nagrania w materiale
    dim          INT NOT NULL,
    emb          vector NOT NULL,
    PRIMARY KEY (material_id, kind, model, item)
);
CREATE INDEX IF NOT EXISTS material_vectors_kind ON material_vectors (kind, model, dim);

CREATE TABLE IF NOT EXISTS material_keys (
    material_id  TEXT NOT NULL REFERENCES materials (id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    value        TEXT NOT NULL,
    weight       REAL NOT NULL DEFAULT 1,
    PRIMARY KEY (material_id, kind, value)
);
CREATE INDEX IF NOT EXISTS material_keys_lookup ON material_keys (kind, value);
"""

VECTOR_THRESHOLDS = {"text": 0.85, "text_skeleton": 0.88, "voice": 0.70}
VECTOR_WEIGHTS = {"text": 0.6, "text_skeleton": 0.6, "voice": 0.7, "image": 0.6, "face": 0.7}
VECTOR_TOP_K = 15
PHASH_MAX_DISTANCE = 8
AUDIO_MIN_SHARED = 15
FUZZY_KEYS = {"img_phash", "audio_lm"}

_schema_ready = False
_schema_lock = threading.Lock()


def ensure_schema(conn: Any, sql: str, key: int) -> None:
    """Schemat tworzony raz naraz: równoległe CREATE TABLE IF NOT EXISTS w Postgresie potrafią się
    wywrócić (UniqueViolation na pg_type), gdy front, API i skaner łączą się jednocześnie."""
    conn.execute("SELECT pg_advisory_lock(%s)", (key,))
    try:
        conn.execute(sql)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (key,))


@contextmanager
def connect() -> Iterator[Any]:
    """Połączenie z bazą (DATABASE_URL) z gotowym schematem."""
    global _schema_ready
    from fakecollector.db import pg
    with pg() as conn:
        if not _schema_ready:
            with _schema_lock:
                if not _schema_ready:
                    ensure_schema(conn, SCHEMA_SQL, 7301)
                    _schema_ready = True
        yield conn


def _vec(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.6g}" for v in values) + "]"


def save_record(rec: MaterialRecord) -> str:
    """Zapisuje (albo nadpisuje) rekord materiału razem z cechami, wektorami i kluczami."""
    s = rec.summary
    j = lambda v: json.dumps(v, ensure_ascii=False, default=str)  # noqa: E731
    with connect() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO materials (id, source, input_type, status, fingerprint, duplicate_of, title, final_url,
                   domain, domains, text_origin, n_words, text, persons, persons_visual, brands, modules,
                   missing, errors, incident, store_dir, schema_version)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
               ON CONFLICT (id) DO UPDATE SET
                   source=EXCLUDED.source, input_type=EXCLUDED.input_type, status=EXCLUDED.status,
                   fingerprint=EXCLUDED.fingerprint, duplicate_of=EXCLUDED.duplicate_of, title=EXCLUDED.title,
                   final_url=EXCLUDED.final_url, domain=EXCLUDED.domain, domains=EXCLUDED.domains,
                   text_origin=EXCLUDED.text_origin, n_words=EXCLUDED.n_words, text=EXCLUDED.text,
                   persons=EXCLUDED.persons, persons_visual=EXCLUDED.persons_visual, brands=EXCLUDED.brands,
                   modules=EXCLUDED.modules, missing=EXCLUDED.missing, errors=EXCLUDED.errors,
                   incident=EXCLUDED.incident, store_dir=EXCLUDED.store_dir,
                   schema_version=EXCLUDED.schema_version""",
            (rec.id, s["source"], s["input_type"], s["status"], s["fingerprint"], s["duplicate_of"], s["title"],
             s["final_url"], s["domain"], s["domains"], s["text_origin"], s["n_words"], s["text"], s["persons"],
             s["persons_visual"], s["brands"], j(s["modules"]), j(s["missing"]), j(s["errors"]),
             j(s["incident"]), s["store_dir"], 1))
        for table in ("material_features", "material_vectors", "material_keys"):
            conn.execute(f"DELETE FROM {table} WHERE material_id = %s", (rec.id,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO material_features (material_id, module, version, data) VALUES (%s,%s,%s,%s::jsonb)",
                [(rec.id, m, rec.versions.get(m), j(d)) for m, d in rec.features.items()])
            cur.executemany(
                "INSERT INTO material_vectors (material_id, kind, model, item, dim, emb) "
                "VALUES (%s,%s,%s,%s,%s,%s::vector) ON CONFLICT DO NOTHING",
                [(rec.id, kind, model, item, len(v), _vec(v)) for kind, model, item, v in rec.vectors if v])
            cur.executemany(
                "INSERT INTO material_keys (material_id, kind, value, weight) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                [(rec.id, k, v, w) for k, v, w in rec.keys])
    return rec.id


_SUMMARY_COLS = ("id", "created_at", "source", "input_type", "status", "fingerprint", "duplicate_of", "title",
                 "final_url", "domain", "domains", "text_origin", "n_words", "persons", "persons_visual",
                 "brands", "modules", "missing", "errors", "store_dir", "campaign_id", "assignment",
                 "assignment_score")


def _row(cols: tuple[str, ...], row: tuple) -> dict[str, Any]:
    d = dict(zip(cols, row))
    if d.get("created_at") is not None:
        d["created_at"] = d["created_at"].isoformat()
    return d


def load_summary(material_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(f"SELECT {', '.join(_SUMMARY_COLS)} FROM materials WHERE id = %s",
                           (material_id,)).fetchone()
    return _row(_SUMMARY_COLS, row) if row else None


def load_features(material_id: str, modules: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Pełne cechy modułów (z wektorami) – wejście dla compare() modułów."""
    with connect() as conn:
        rows = conn.execute("SELECT module, data FROM material_features WHERE material_id = %s",
                            (material_id,)).fetchall()
    return {m: d for m, d in rows if modules is None or m in modules}


def load_record(material_id: str) -> MaterialRecord | None:
    s = load_summary(material_id)
    if not s:
        return None
    with connect() as conn:
        extra = conn.execute("SELECT text, incident FROM materials WHERE id = %s", (material_id,)).fetchone()
        feats = conn.execute("SELECT module, version, data FROM material_features WHERE material_id = %s",
                             (material_id,)).fetchall()
        keys = conn.execute("SELECT kind, value, weight FROM material_keys WHERE material_id = %s",
                            (material_id,)).fetchall()
    s["text"], s["incident"] = extra
    rec = MaterialRecord(id=material_id, summary=s)
    for m, v, d in feats:
        rec.features[m], rec.versions[m] = d, v
    rec.keys = [tuple(k) for k in keys]
    return rec


def list_materials(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(f"SELECT {', '.join(_SUMMARY_COLS)} FROM materials ORDER BY created_at DESC "
                            "LIMIT %s OFFSET %s", (limit, offset)).fetchall()
    return [_row(_SUMMARY_COLS, r) for r in rows]


def delete_material(material_id: str) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM materials WHERE id = %s", (material_id,)).rowcount > 0


def count_materials() -> int:
    with connect() as conn:
        return conn.execute("SELECT count(*) FROM materials").fetchone()[0]


def find_candidates(material_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Materiały z bazy, które mogą należeć do tej samej kampanii, z powodami trafienia.

    Trzy drogi:
      1. wspólne klucze (ten sam hash tekstu, pasmo MinHash, domena, piksel, hash obrazu, QR…),
      2. bliskie hashe percepcyjne obrazów i wspólna próbka odcisku dźwięku,
      3. najbliższe wektory w pgvector (tekst, szkielet, obraz, twarze, głos).
    To wstępne wyszukiwanie: wynik porównania modułami liczy dopiero etap grupowania.
    """
    hits: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with connect() as conn:
        rows = conn.execute(
            """SELECT k2.material_id, k1.kind, count(*), max(k1.weight), (array_agg(k1.value))[1:3]
               FROM material_keys k1
               JOIN material_keys k2 ON k2.kind = k1.kind AND k2.value = k1.value AND k2.material_id <> k1.material_id
               WHERE k1.material_id = %s AND k1.kind <> ALL(%s)
               GROUP BY k2.material_id, k1.kind""",
            (material_id, list(FUZZY_KEYS))).fetchall()
        for other, kind, n, w, sample in rows:
            hits[other].append({"signal": kind, "matches": n, "weight": float(w), "values": sample})

        rows = conn.execute(
            """SELECT k2.material_id, count(*) FROM material_keys k1
               JOIN material_keys k2 ON k2.kind = 'audio_lm' AND k2.value = k1.value AND k2.material_id <> k1.material_id
               WHERE k1.material_id = %s AND k1.kind = 'audio_lm'
               GROUP BY k2.material_id HAVING count(*) >= %s""",
            (material_id, AUDIO_MIN_SHARED)).fetchall()
        for other, n in rows:
            hits[other].append({"signal": "audio_lm", "matches": n, "weight": KEY_WEIGHTS["audio_lm"]})

        own = [v for (v,) in conn.execute(
            "SELECT value FROM material_keys WHERE material_id = %s AND kind = 'img_phash'", (material_id,))]
        if own:
            others = conn.execute("SELECT material_id, value FROM material_keys "
                                  "WHERE kind = 'img_phash' AND material_id <> %s", (material_id,)).fetchall()
            best: dict[str, int] = {}
            for other, value in others:
                d = min(hamming_hex(value, h) for h in own)
                if d <= PHASH_MAX_DISTANCE and d < best.get(other, 65):
                    best[other] = d
            for other, d in best.items():
                hits[other].append({"signal": "img_phash", "distance": d,
                                    "weight": KEY_WEIGHTS["img_phash"] * (1 - d / 16)})

        vecs = conn.execute("SELECT kind, model, item, dim, emb::text FROM material_vectors WHERE material_id = %s",
                            (material_id,)).fetchall()
        for kind, model, item, dim, emb in vecs:
            thr = _vector_threshold(kind, model)
            rows = conn.execute(
                """SELECT material_id, max(1 - (emb <=> %s::vector)) AS sim
                   FROM (SELECT material_id, emb FROM material_vectors
                         WHERE kind = %s AND model = %s AND dim = %s AND material_id <> %s
                         ORDER BY emb <=> %s::vector LIMIT %s) t
                   GROUP BY material_id""",
                (emb, kind, model, dim, material_id, emb, VECTOR_TOP_K)).fetchall()
            for other, sim in rows:
                if sim is not None and sim >= thr:
                    hits[other].append({"signal": f"vec:{kind}", "similarity": round(float(sim), 3),
                                        "model": model, "weight": VECTOR_WEIGHTS.get(kind, 0.5)})

        if not hits:
            return []
        info = conn.execute("SELECT id, title, source, domain, status, campaign_id, assignment FROM materials "
                            "WHERE id = ANY(%s)", (list(hits),)).fetchall()
    meta = {r[0]: dict(zip(("id", "title", "source", "domain", "status", "campaign_id", "assignment"), r))
            for r in info}

    out = []
    for other, signals in hits.items():
        merged: dict[str, dict] = {}
        for sgn in signals:
            prev = merged.get(sgn["signal"])
            if prev is None or sgn.get("weight", 0) > prev.get("weight", 0):
                merged[sgn["signal"]] = sgn
        weights = sorted((s.get("weight", 0) for s in merged.values()), reverse=True)
        rank = 1.0
        for w in weights:
            rank *= (1 - min(w, 0.99))
        out.append({**meta.get(other, {"id": other}), "rank": round(1 - rank, 3),
                    "signals": sorted(merged.values(), key=lambda s: -s.get("weight", 0))})
    out.sort(key=lambda c: -c["rank"])
    return out[:limit]


def _vector_threshold(kind: str, model: str) -> float:
    if kind in VECTOR_THRESHOLDS:
        return VECTOR_THRESHOLDS[kind]
    try:
        from .image_bridge import appearance_threshold, face_threshold
        return face_threshold(model) if kind == "face" else appearance_threshold(model)
    except Exception:  # noqa: BLE001
        return 0.7
