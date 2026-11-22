"""Decyzje analityka: potwierdzenie albo odrzucenie przynależności materiału do kampanii.

Decyzja dotyczy par materiałów (material_decisions), więc przetrwa przeliczenie kampanii:
  * potwierdzone – para zawsze trafia do tej samej kampanii (rebuild łączy ją niezależnie od wyniku),
  * odrzucone    – krawędź tej pary nie łączy kampanii i nie daje statusu „do weryfikacji”.
Ostatnia decyzja materiału jest też w materials.decision (do listy i GUI).
"""
from __future__ import annotations

from typing import Any

from .campaigns import connect, refresh_campaign, refresh_operators, _new_campaign

DECISIONS = {"potwierdz": "potwierdzony", "odrzuc": "odrzucony", "cofnij": None}


class DecisionError(ValueError):
    """Decyzji nie da się zastosować (np. materiał sam w kampanii)."""


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def load_pairs(conn=None) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """(potwierdzone, odrzucone) pary materiałów."""
    def _load(c):
        rows = c.execute("SELECT a, b, decision FROM material_decisions").fetchall()
        return ({(a, b) for a, b, d in rows if d == "potwierdzone"},
                {(a, b) for a, b, d in rows if d == "odrzucone"})
    if conn is not None:
        return _load(conn)
    with connect() as c:
        return _load(c)


def apply_labels(conn) -> None:
    """Status przypisania zgodny z decyzją analityka (po przeliczeniu kampanii)."""
    conn.execute("""UPDATE materials SET assignment = 'potwierdzony'
                    WHERE decision = 'potwierdzony' AND campaign_id IS NOT NULL""")
    conn.execute("UPDATE materials SET assignment = 'odrzucony' WHERE decision = 'odrzucony'")


def _members(conn, material_id: str) -> tuple[int | None, list[str]]:
    row = conn.execute("SELECT campaign_id FROM materials WHERE id = %s", (material_id,)).fetchone()
    if row is None:
        raise KeyError(material_id)
    cid = row[0]
    if cid is None:
        return None, []
    others = [r[0] for r in conn.execute("SELECT id FROM materials WHERE campaign_id = %s AND id <> %s",
                                         (cid, material_id)).fetchall()]
    return cid, others


def decide(material_id: str, action: str, note: str = "") -> dict[str, Any]:
    """potwierdz / odrzuc / cofnij. Zwraca kampanię materiału po decyzji."""
    if action not in DECISIONS:
        raise DecisionError(f"Nieznana decyzja {action!r}; dozwolone: {', '.join(DECISIONS)}")
    note = (note or "").strip()[:500]

    if action == "cofnij":
        with connect() as conn, conn.transaction():
            row = conn.execute("SELECT campaign_id FROM materials WHERE id = %s", (material_id,)).fetchone()
            if row is None:
                raise KeyError(material_id)
            conn.execute("DELETE FROM material_decisions WHERE a = %s OR b = %s", (material_id, material_id))
            conn.execute("UPDATE materials SET decision = NULL, decision_note = NULL, decided_at = NULL "
                         "WHERE id = %s", (material_id,))
        from .campaigns import assign_material
        res = assign_material(material_id)
        if row[0] is not None and row[0] != res["campaign_id"]:
            refresh_campaign(row[0])          # pusta kampania znika, pozostałym odświeża się opis
            refresh_operators()
        return {"material_id": material_id, "decision": None, "campaign_id": res["campaign_id"],
                "assignment": res["assignment"], "previous_campaign_id": row[0]}

    with connect() as conn, conn.transaction():
        cid, others = _members(conn, material_id)
        if not others:
            raise DecisionError("Materiał jest sam w kampanii – nie ma czego potwierdzać ani odrzucać.")
        if action == "potwierdz":
            best = conn.execute(
                """SELECT CASE WHEN a = %s THEN b ELSE a END FROM material_links
                   WHERE (a = %s AND b = ANY(%s)) OR (b = %s AND a = ANY(%s)) ORDER BY score DESC LIMIT 1""",
                (material_id, material_id, others, material_id, others)).fetchone()
            partner = best[0] if best else others[0]
            a, b = _pair(material_id, partner)
            conn.execute("""INSERT INTO material_decisions (a, b, decision, note) VALUES (%s, %s, 'potwierdzone', %s)
                            ON CONFLICT (a, b) DO UPDATE SET decision = EXCLUDED.decision, note = EXCLUDED.note,
                                                             decided_at = now()""", (a, b, note))
            conn.execute("""UPDATE materials SET assignment = 'potwierdzony', decision = 'potwierdzony',
                                   decision_note = %s, decided_at = now() WHERE id = %s""", (note, material_id))
            new_cid, previous = cid, None
        else:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO material_decisions (a, b, decision, note) VALUES (%s, %s, 'odrzucone', %s)
                       ON CONFLICT (a, b) DO UPDATE SET decision = EXCLUDED.decision, note = EXCLUDED.note,
                                                        decided_at = now()""",
                    [(*_pair(material_id, o), note) for o in others])
            new_cid, previous = _new_campaign(conn), cid
            conn.execute("""UPDATE materials SET campaign_id = %s, assignment = 'odrzucony', assignment_score = NULL,
                                   assigned_at = now(), decision = 'odrzucony', decision_note = %s, decided_at = now()
                            WHERE id = %s""", (new_cid, note, material_id))
    refresh_campaign(new_cid)
    if previous is not None:
        refresh_campaign(previous)
        refresh_operators()
    return {"material_id": material_id, "decision": DECISIONS[action], "campaign_id": new_cid,
            "previous_campaign_id": previous}
