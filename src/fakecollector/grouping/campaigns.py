"""Grupowanie materiałów w kampanie.

Dwa tryby:
  * assign_material(id) – nowy materiał: kandydaci z bazy -> porównanie -> decyzja:
        przypisany      – wynik >= 0.75 z materiałem kampanii (jeśli łączy kilka kampanii, scalamy je),
        do_weryfikacji  – najlepszy wynik 0.5–0.75: wskazujemy kampanię, decyzję podejmuje człowiek,
        nowy_wzorzec    – nic podobnego: materiał zaczyna nową kampanię,
  * rebuild()           – przeliczenie całej bazy: krawędzie między kandydatami, potem spójne składowe
        grafu krawędzi >= 0.75 (każda kampania to łańcuch pewnych powiązań, każde z przesłankami).

Krawędzie (pary z wynikiem i przesłankami) zapisujemy w material_links, kampanie w campaigns,
przynależność w materials.campaign_id / assignment. Kampanie połączone wspólną infrastrukturą
(ten sam piksel, kit, serwer) dostają wspólne campaigns.operator_group – „ten sam operator”.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import Counter, defaultdict
from typing import Any

from .compare import ASSIGN, MODULES, REVIEW, Context, PairResult, compare_materials, load_context

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS material_links (
    a            TEXT NOT NULL REFERENCES materials (id) ON DELETE CASCADE,
    b            TEXT NOT NULL REFERENCES materials (id) ON DELETE CASCADE,
    score        REAL NOT NULL,
    weighted_avg REAL,
    decision     TEXT NOT NULL,
    modules      JSONB NOT NULL DEFAULT '{}'::jsonb,
    reasons      JSONB NOT NULL DEFAULT '[]'::jsonb,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (a, b),
    CHECK (a < b)
);
CREATE INDEX IF NOT EXISTS material_links_b ON material_links (b);

CREATE TABLE IF NOT EXISTS campaigns (
    id          BIGSERIAL PRIMARY KEY,
    label       TEXT,
    method      TEXT,
    summary     TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS size INT NOT NULL DEFAULT 0;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS persons TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS domains TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS brands TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS first_seen TIMESTAMPTZ;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS operator_group BIGINT;      -- kampanie tego samego operatora
ALTER TABLE material_links ADD COLUMN IF NOT EXISTS operator_score REAL NOT NULL DEFAULT 0;
ALTER TABLE material_links ADD COLUMN IF NOT EXISTS operator_reasons JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS material_decisions (
    a            TEXT NOT NULL REFERENCES materials (id) ON DELETE CASCADE,
    b            TEXT NOT NULL REFERENCES materials (id) ON DELETE CASCADE,
    decision     TEXT NOT NULL,                  -- potwierdzone / odrzucone
    note         TEXT,
    decided_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (a, b),
    CHECK (a < b)
);
ALTER TABLE materials ADD COLUMN IF NOT EXISTS decision TEXT;              -- potwierdzony / odrzucony
ALTER TABLE materials ADD COLUMN IF NOT EXISTS decision_note TEXT;
ALTER TABLE materials ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;
"""
METHOD = "fakecollector-grupowanie-v1"
CANDIDATES_PER_MATERIAL = 25

_ready = False
_ready_lock = threading.Lock()


def connect():
    """Połączenie z bazą z gotowym schematem rekordu i grupowania."""
    from contextlib import contextmanager

    from fakecollector.record.store import connect as record_connect

    @contextmanager
    def _cm():
        global _ready
        with record_connect() as conn:
            if not _ready:
                from fakecollector.record.store import ensure_schema
                with _ready_lock:
                    if not _ready:
                        ensure_schema(conn, SCHEMA_SQL, 7302)
                        _ready = True
            yield conn
    return _cm()


# ============================================================================ krawędzie

def save_link(r: PairResult) -> None:
    a, b = sorted((r.a, r.b))
    j = lambda v: json.dumps(v, ensure_ascii=False, default=str)  # noqa: E731
    with connect() as conn:
        conn.execute(
            """INSERT INTO material_links (a, b, score, weighted_avg, decision, modules, reasons,
                                           operator_score, operator_reasons)
               VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb)
               ON CONFLICT (a, b) DO UPDATE SET score=EXCLUDED.score, weighted_avg=EXCLUDED.weighted_avg,
                   decision=EXCLUDED.decision, modules=EXCLUDED.modules, reasons=EXCLUDED.reasons,
                   operator_score=EXCLUDED.operator_score, operator_reasons=EXCLUDED.operator_reasons,
                   computed_at=now()""",
            (a, b, r.score, r.weighted_avg, r.decision, j(r.modules), j(r.reasons),
             r.operator_score, j(r.operator_reasons)))


def links_of(material_id: str, min_score: float = 0.0) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT CASE WHEN a = %s THEN b ELSE a END, score, decision, reasons, modules
               FROM material_links WHERE (a = %s OR b = %s) AND score >= %s ORDER BY score DESC""",
            (material_id, material_id, material_id, min_score)).fetchall()
    return [{"id": o, "score": s, "decision": d, "reasons": r, "modules": m} for o, s, d, r, m in rows]


def compare_with_candidates(material_id: str, cache: dict | None = None, ctx: Context | None = None,
                            only: set[str] | None = None) -> list[PairResult]:
    """Porównuje materiał z kandydatami z bazy i zapisuje krawędzie."""
    from fakecollector.record import find_candidates
    cache = cache if cache is not None else {}
    ctx = ctx or load_context()
    out = []
    for cand in find_candidates(material_id, limit=CANDIDATES_PER_MATERIAL):
        if only is not None and cand["id"] not in only:
            continue
        r = compare_materials(material_id, cand["id"], cache, ctx)
        save_link(r)
        out.append(r)
    return sorted(out, key=lambda r: -r.score)


# ============================================================================ kampanie

def _campaign_of(conn, ids: list[str]) -> dict[str, int | None]:
    rows = conn.execute("SELECT id, campaign_id FROM materials WHERE id = ANY(%s)", (ids,)).fetchall()
    return dict(rows)


def _new_campaign(conn) -> int:
    return conn.execute("INSERT INTO campaigns (method) VALUES (%s) RETURNING id", (METHOD,)).fetchone()[0]


def _set(conn, ids: list[str], campaign_id: int, assignment: str, score: float | None) -> None:
    conn.execute("""UPDATE materials SET campaign_id=%s, assignment=%s, assignment_score=%s, assigned_at=now()
                    WHERE id = ANY(%s)""", (campaign_id, assignment, score, ids))


def refresh_campaign(campaign_id: int) -> None:
    """Etykieta i podsumowanie kampanii z jej materiałów i krawędzi."""
    with connect() as conn:
        rows = conn.execute("""SELECT id, title, domain, domains, persons, persons_visual, brands, created_at, assignment
                               FROM materials WHERE campaign_id = %s""", (campaign_id,)).fetchall()
        if not rows:
            conn.execute("DELETE FROM campaigns WHERE id = %s", (campaign_id,))
            return
        ids = [r[0] for r in rows]
        persons, domains, brands = Counter(), Counter(), Counter()
        for _, _, dom, doms, pers, pv, br, _, _ in rows:
            persons.update(set(pers or []) | set(pv or []))
            domains.update(set(doms or []) | ({dom} if dom else set()))
            brands.update(br or [])
        links = conn.execute("""SELECT modules, reasons FROM material_links
                                WHERE a = ANY(%s) AND b = ANY(%s) AND score >= %s""", (ids, ids, ASSIGN)).fetchall()
        signals: Counter = Counter()
        sample: list[str] = []
        for modules, reasons in links:
            for name, m in (modules or {}).items():
                if m.get("contribution", 0) > 0:
                    signals[name] += 1
            for r in (reasons or [])[:2]:
                if r not in sample and len(sample) < 8:
                    sample.append(r)
        # bohater kampanii: osoba najczęściej wymieniana w tekście każdego materiału
        # (np. Stanowski / Sekielski, a nie Glapiński, który pojawia się w obu artykułach pobocznie)
        leads: Counter = Counter()
        for (counts,) in conn.execute("""SELECT data->'persons' FROM material_features
                                         WHERE module = 'text' AND material_id = ANY(%s)""", (ids,)).fetchall():
            if counts:
                leads[max(counts.items(), key=lambda kv: kv[1])[0]] += 1
        lead = " / ".join(p for p, _ in leads.most_common(2)) or \
            (persons.most_common(1)[0][0] if persons else None)
        top_title = next((t for _, t, *_ in rows if t), None)
        dom_list = [d for d, _ in domains.most_common()]
        label = " · ".join(x for x in (lead or (top_title or "")[:50],
                                         dom_list[0] if dom_list else None) if x) or f"kampania {campaign_id}"
        if len(dom_list) > 1:
            label += f" (+{len(dom_list) - 1} domen)"
        n_review = sum(1 for r in rows if r[8] == "do_weryfikacji")
        summary = (f"{len(rows)} materiałów ({n_review} do weryfikacji), {len(dom_list)} domen"
                   + (f", osoby: {', '.join(p for p, _ in persons.most_common(3))}" if persons else "")
                   + (f". Łączą je: {', '.join(MODULES.get(n, {}).get('label', n) for n, _ in signals.most_common())}"
                      if signals else ""))
        dates = [r[7] for r in rows if r[7]]
        conn.execute(
            """UPDATE campaigns SET label=%s, summary=%s, size=%s, persons=%s, domains=%s, brands=%s,
                   evidence=%s::jsonb, first_seen=%s, last_seen=%s, updated_at=now(), method=%s WHERE id=%s""",
            (label, summary, len(rows), [p for p, _ in persons.most_common(10)], dom_list[:30],
             [b for b, _ in brands.most_common(10)],
             json.dumps({"signals": dict(signals), "reasons": sample}, ensure_ascii=False),
             min(dates) if dates else None, max(dates) if dates else None, METHOD, campaign_id))


def assign_material(material_id: str) -> dict[str, Any]:
    """Przypisanie nowego materiału do kampanii (porównanie z kandydatami z bazy)."""
    from .decisions import load_pairs
    _, rejected = load_pairs()
    results = [r for r in compare_with_candidates(material_id) if tuple(sorted((r.a, r.b))) not in rejected]
    strong = [r for r in results if r.score >= ASSIGN]
    with connect() as conn, conn.transaction():
        others = [r.b for r in results]
        camp = _campaign_of(conn, others) if others else {}
        best_by_campaign: dict[int, PairResult] = {}
        unassigned_strong = []
        for r in results:
            cid = camp.get(r.b)
            if cid is None:
                if r.score >= ASSIGN:
                    unassigned_strong.append(r.b)
                continue
            if cid not in best_by_campaign or r.score > best_by_campaign[cid].score:
                best_by_campaign[cid] = r
        ranked = sorted(best_by_campaign.items(), key=lambda kv: -kv[1].score)
        merged: list[int] = []
        if ranked and ranked[0][1].score >= ASSIGN:
            cid, best = ranked[0]
            status = "przypisany"
            # materiał pewnie pasuje do kilku kampanii -> to jedna kampania: scalamy
            for other_cid, r in ranked[1:]:
                if r.score >= ASSIGN:
                    conn.execute("UPDATE materials SET campaign_id=%s WHERE campaign_id=%s", (cid, other_cid))
                    conn.execute("DELETE FROM campaigns WHERE id=%s", (other_cid,))
                    merged.append(other_cid)
        elif unassigned_strong:
            cid, best, status = _new_campaign(conn), strong[0], "przypisany"
        elif ranked and ranked[0][1].score >= REVIEW:
            (cid, best), status = ranked[0], "do_weryfikacji"
        else:
            cid, best, status = _new_campaign(conn), (results[0] if results else None), "nowy_wzorzec"
        _set(conn, [material_id], cid, status, best.score if best else None)
        if unassigned_strong:
            _set(conn, unassigned_strong, cid, "przypisany", None)
    refresh_campaign(cid)
    refresh_operators()
    return {"material_id": material_id, "campaign_id": cid, "assignment": status,
            "score": best.score if best else None, "merged_campaigns": merged,
            "matches": [{"id": r.b, "score": r.score, "decision": r.decision, "reasons": r.reasons[:5],
                         "same_operator": r.operator_score >= ASSIGN, "operator_reasons": r.operator_reasons[:3]}
                        for r in results[:5]],
            "campaign": get_campaign(cid, with_members=False)}


def rebuild(ids: list[str] | None = None, write: bool = True, progress=None) -> dict[str, Any]:
    """Przeliczenie kampanii od nowa (dla całej bazy albo podanych materiałów).

    1. dla każdego materiału: kandydaci z bazy i porównanie (krawędzie),
    2. kampanie = spójne składowe grafu krawędzi >= ASSIGN,
    3. materiał bez pewnej krawędzi, ale z krawędzią >= REVIEW – „do weryfikacji” w kampanii sąsiada,
    4. reszta – „nowy wzorzec” (kampania z jednym materiałem).
    """
    with connect() as conn:
        all_ids = [r[0] for r in conn.execute("SELECT id FROM materials ORDER BY created_at").fetchall()]
        previous = dict(conn.execute("SELECT id, campaign_id FROM materials").fetchall())
        dups = dict(conn.execute("SELECT id, duplicate_of FROM materials WHERE duplicate_of IS NOT NULL").fetchall())
    ids = [i for i in (ids or all_ids) if i in set(all_ids)]
    scope = set(ids)
    cache: dict = {}
    ctx = load_context()
    edges: dict[tuple[str, str], PairResult] = {}
    from fakecollector.record import find_candidates
    for n, mid in enumerate(ids, 1):
        for cand in find_candidates(mid, limit=CANDIDATES_PER_MATERIAL):
            key = tuple(sorted((mid, cand["id"])))
            if cand["id"] not in scope or key in edges:      # każdą parę porównujemy raz
                continue
            r = compare_materials(mid, cand["id"], cache, ctx)
            if write:
                save_link(r)
            edges[key] = r
        if progress:
            progress(n, len(ids), mid)

    parent = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    from .decisions import load_pairs
    confirmed, rejected = load_pairs()
    for (a, b), r in edges.items():
        if r.score >= ASSIGN and (a, b) not in rejected:
            parent[find(a)] = find(b)
    for a, b in confirmed:                       # decyzja analityka: para zawsze w jednej kampanii
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    for d, orig in dups.items():                 # duplikat zawsze w kampanii oryginału
        if d in parent and orig in parent:
            parent[find(d)] = find(orig)

    groups: dict[str, list[str]] = defaultdict(list)
    for i in ids:
        groups[find(i)].append(i)
    clusters = [g for g in groups.values() if len(g) > 1]
    singles = [g[0] for g in groups.values() if len(g) == 1]
    review: dict[str, tuple[str, float]] = {}     # pojedynczy materiał -> (sąsiad w kampanii, wynik)
    member_of = {m: tuple(sorted(g)) for g in clusters for m in g}
    for (a, b), r in edges.items():
        if (a, b) in rejected:
            continue
        for x, y in ((a, b), (b, a)):
            if x in singles and y in member_of and REVIEW <= r.score < ASSIGN:
                if x not in review or r.score > review[x][1]:
                    review[x] = (y, r.score)

    op_parent = {i: find(i) for i in ids}          # kampanie + wspólna infrastruktura

    def op_find(x: str) -> str:
        while op_parent[x] != x:
            op_parent[x] = op_parent[op_parent[x]]
            x = op_parent[x]
        return x
    for i in ids:
        op_parent.setdefault(find(i), find(i))
    for (a, b), r in edges.items():
        if r.operator_score >= ASSIGN:
            op_parent[op_find(find(a))] = op_find(find(b))
    op_groups: dict[str, set[str]] = defaultdict(set)
    for i in ids:
        op_groups[op_find(find(i))].add(i)
    operator_clusters = [sorted(g) for g in op_groups.values()
                         if len({find(m) for m in g}) > 1]       # operator z co najmniej 2 kampaniami

    result = {"materials": len(ids), "edges": len(edges),
              "strong_edges": sum(r.score >= ASSIGN for r in edges.values()),
              "review_edges": sum(REVIEW <= r.score < ASSIGN for r in edges.values()),
              "campaigns": len(clusters), "to_review": len(review),
              "new_patterns": len(singles) - len(review),
              "clusters": [sorted(g) for g in clusters],
              "operator_edges": sum(r.operator_score >= ASSIGN and r.score < ASSIGN for r in edges.values()),
              "operator_clusters": operator_clusters}
    if not write:
        result["review"] = review
        return result

    touched: set[int] = set()
    with connect() as conn, conn.transaction():
        used: set[int] = set()

        def stable_id(members: list[str]) -> int:
            # zachowaj numer kampanii, jeśli większość materiałów już w niej była
            votes = Counter(previous.get(m) for m in members if previous.get(m) is not None)
            for cid, _ in votes.most_common():
                if cid not in used:
                    used.add(cid)
                    return cid
            cid = _new_campaign(conn)
            used.add(cid)
            return cid

        cluster_id: dict[tuple, int] = {}
        for g in sorted(clusters, key=len, reverse=True):
            cid = stable_id(g)
            cluster_id[tuple(sorted(g))] = cid
            best = {m: max((r.score for (a, b), r in edges.items() if m in (a, b) and r.score >= ASSIGN),
                           default=None) for m in g}
            for m in g:
                _set(conn, [m], cid, "przypisany", best[m])
            touched.add(cid)
        for m in singles:
            if m in review:
                neighbour, score = review[m]
                cid = cluster_id[member_of[neighbour]]
                _set(conn, [m], cid, "do_weryfikacji", score)
            else:
                cid = stable_id([m])
                _set(conn, [m], cid, "nowy_wzorzec", None)
            touched.add(cid)
        from .decisions import apply_labels
        apply_labels(conn)
        # kampanie, które straciły wszystkie materiały
        conn.execute("""DELETE FROM campaigns c WHERE method = %s
                        AND NOT EXISTS (SELECT 1 FROM materials m WHERE m.campaign_id = c.id)""", (METHOD,))
    for cid in touched:
        refresh_campaign(cid)
    result["operators"] = refresh_operators()
    return result


def refresh_operators() -> list[list[int]]:
    """Kampanie tego samego operatora: spójne składowe krawędzi „wspólna infrastruktura” między kampaniami.

    Każda kampania w grupie dostaje operator_group = najmniejszy numer kampanii w grupie.
    """
    with connect() as conn, conn.transaction():
        camp = dict(conn.execute("SELECT id, campaign_id FROM materials WHERE campaign_id IS NOT NULL").fetchall())
        links = conn.execute("SELECT a, b FROM material_links WHERE operator_score >= %s", (ASSIGN,)).fetchall()
        parent: dict[int, int] = {c: c for c in set(camp.values())}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for a, b in links:
            ca, cb = camp.get(a), camp.get(b)
            if ca is not None and cb is not None and ca != cb:
                parent[find(ca)] = find(cb)
        groups: dict[int, list[int]] = defaultdict(list)
        for c in parent:
            groups[find(c)].append(c)
        conn.execute("UPDATE campaigns SET operator_group = NULL WHERE operator_group IS NOT NULL")
        out = []
        for members in groups.values():
            if len(members) > 1:
                gid = min(members)
                conn.execute("UPDATE campaigns SET operator_group = %s WHERE id = ANY(%s)", (gid, members))
                out.append(sorted(members))
    return sorted(out, key=len, reverse=True)


# ============================================================================ odczyt

_CAMPAIGN_COLS = ("id", "label", "summary", "size", "persons", "domains", "brands", "evidence",
                  "first_seen", "last_seen", "created_at", "updated_at", "operator_group")


def _camp_row(row: tuple) -> dict[str, Any]:
    d = dict(zip(_CAMPAIGN_COLS, row))
    for k in ("first_seen", "last_seen", "created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def list_campaigns(min_size: int = 1) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(f"SELECT {', '.join(_CAMPAIGN_COLS)} FROM campaigns WHERE size >= %s "
                            "ORDER BY size DESC, updated_at DESC", (min_size,)).fetchall()
    return [_camp_row(r) for r in rows]


def get_campaign(campaign_id: int, with_members: bool = True) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(f"SELECT {', '.join(_CAMPAIGN_COLS)} FROM campaigns WHERE id = %s",
                           (campaign_id,)).fetchone()
        if not row:
            return None
        out = _camp_row(row)
        if with_members:
            members = conn.execute(
                """SELECT id, source, title, domain, status, assignment, assignment_score, persons, created_at,
                          decision, decision_note, decided_at
                   FROM materials WHERE campaign_id = %s ORDER BY assignment, created_at""",
                (campaign_id,)).fetchall()
            out["members"] = [{"id": m[0], "source": m[1], "title": m[2], "domain": m[3], "status": m[4],
                               "assignment": m[5], "score": m[6], "persons": m[7],
                               "created_at": m[8].isoformat() if m[8] else None,
                               "decision": m[9], "decision_note": m[10],
                               "decided_at": m[11].isoformat() if m[11] else None} for m in members]
            ids = [m[0] for m in members]
            links = conn.execute(
                """SELECT a, b, score, decision, reasons FROM material_links
                   WHERE a = ANY(%s) AND b = ANY(%s) AND score >= %s ORDER BY score DESC""",
                (ids, ids, REVIEW)).fetchall()
            out["links"] = [{"a": a, "b": b, "score": s, "decision": d, "reasons": r} for a, b, s, d, r in links]
            out["related_campaigns"] = []
            if out.get("operator_group") is not None:
                rel = conn.execute("""SELECT id, label, size FROM campaigns WHERE operator_group = %s AND id <> %s
                                      ORDER BY size DESC""", (out["operator_group"], campaign_id)).fetchall()
                for rid, rlabel, rsize in rel:
                    why = conn.execute(
                        """SELECT l.operator_reasons FROM material_links l
                           JOIN materials ma ON ma.id = l.a JOIN materials mb ON mb.id = l.b
                           WHERE l.operator_score >= %s AND ((ma.campaign_id = %s AND mb.campaign_id = %s)
                                 OR (ma.campaign_id = %s AND mb.campaign_id = %s))
                           ORDER BY l.operator_score DESC LIMIT 1""",
                        (ASSIGN, campaign_id, rid, rid, campaign_id)).fetchone()
                    out["related_campaigns"].append({"id": rid, "label": rlabel, "size": rsize,
                                                     "reasons": (why[0] if why else [])[:4]})
    return out
