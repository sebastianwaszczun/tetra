"""Etykiety do ewaluacji: data/labels.csv – która kampania (i który operator) to który materiał.

Kolumny:  source, kampania, operator, sprawdzone, uwagi
  * source     – ścieżka materiału z paczki (data/DANE/1, data/BACKUP/p3) – stała między przeliczeniami,
  * kampania   – dowolna nazwa; materiały z tą samą nazwą to jedna kampania,
  * operator   – opcjonalnie: materiały z tą samą nazwą mają wspólnego operatora (infrastrukturę),
  * sprawdzone – „tak”, gdy człowiek potwierdził wiersz (raport liczy, ile etykiet sprawdzono),
  * uwagi      – podpowiedzi przy szkicu, np. „ta sama osoba co w …”.

Szkic (`python -m fakecollector.eval labels`) powstaje z obecnych kampanii systemu. UWAGA: ewaluacja
na niesprawdzonym szkicu da 100% – szkic trzeba przejrzeć i poprawić ręcznie.
"""
from __future__ import annotations

import csv
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

LABELS_FILE = Path(os.environ.get("LABELS_FILE", "data/labels.csv"))
COLUMNS = ["source", "kampania", "operator", "sprawdzone", "uwagi"]
YES = {"tak", "t", "1", "yes", "y", "x", "ok"}


@dataclass
class Label:
    source: str
    campaign: str
    operator: str | None
    verified: bool
    note: str = ""


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or "x"


def load_labels(path: Path | str = LABELS_FILE) -> dict[str, Label]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Brak pliku etykiet {path} – utwórz szkic: python -m fakecollector.eval labels")
    out: dict[str, Label] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            src, camp = (row.get("source") or "").strip(), (row.get("kampania") or "").strip()
            if not src or not camp or src.startswith("#"):
                continue
            out[src] = Label(src, camp, (row.get("operator") or "").strip() or None,
                             (row.get("sprawdzone") or "").strip().lower() in YES, (row.get("uwagi") or "").strip())
    return out


def draft_labels(path: Path | str = LABELS_FILE, force: bool = False) -> dict:
    """Szkic etykiet z obecnych kampanii systemu (do ręcznego przejrzenia)."""
    from fakecollector.record.store import connect
    path = Path(path)
    if path.exists() and not force:
        raise FileExistsError(f"{path} już istnieje – użyj --force, żeby nadpisać (stracisz ręczne poprawki).")
    with connect() as conn:
        rows = conn.execute(
            """SELECT m.source, m.campaign_id, c.label, c.operator_group, m.assignment, m.title
               FROM materials m LEFT JOIN campaigns c ON c.id = m.campaign_id
               WHERE m.source LIKE 'data/%%' ORDER BY m.source, m.created_at""").fetchall()
    seen: set[str] = set()
    items = []
    for src, cid, label, op, assignment, title in rows:
        if src in seen:            # ten sam folder dodany dwa razy – liczymy raz
            continue
        seen.add(src)
        lead = (label or title or src).split(" · ")[0]
        items.append({"source": src, "cid": cid, "lead": lead, "op": op, "assignment": assignment})
    # podpowiedzi: ta sama osoba w różnych kampaniach systemu – człowiek decyduje, czy to jedna kampania
    by_lead = defaultdict(set)
    for it in items:
        by_lead[slug(it["lead"])].add(it["cid"])
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for it in sorted(items, key=lambda x: (str(x["cid"]), x["source"])):
            notes = []
            others = sorted(c for c in by_lead[slug(it["lead"])] if c != it["cid"])
            if others:
                notes.append(f"ta sama osoba co w kampaniach systemu {others} – czy to jedna kampania?")
            if it["assignment"] == "do_weryfikacji":
                notes.append("system: do weryfikacji")
            camp = f"{slug(it['lead'])}-k{it['cid']}"
            w.writerow({"source": it["source"], "kampania": camp,
                        "operator": f"op{it['op']}" if it["op"] is not None else camp,
                        "sprawdzone": "", "uwagi": "; ".join(notes)})
    return {"path": str(path), "rows": len(items), "hints": sum(1 for it in items if len(by_lead[slug(it["lead"])]) > 1)}
