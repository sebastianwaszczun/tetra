"""Referencyjne łączenie rekordów OSINT po pivotach (do użycia przez moduł korelacji).

Ocena pary (a, b):
  1. wspólne pivoty (kind, value) + rozmyte dopasowanie simhash DOM (Hamming ≤ 6),
  2. wkład pivotu  c = waga_rodzaju × rzadkość(df)   gdzie rzadkość = min(1, 1.5 / log2(df+1)),
     df = w ilu rekordach korpusu występuje ta wartość (IP Cloudflare'a -> wysoki df -> ~0),
  3. w obrębie rodzaju bierzemy maksimum (10 wspólnych obrazków ≠ 10 niezależnych dowodów),
  4. między rodzajami noisy-OR:  score = 1 − Π(1 − c_kind).
Wynik jest w pełni wyjaśnialny: lista wspólnych pivotów z wkładem i opisem.

    python -m osint.correlate records.jsonl --threshold 0.75
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict

from .pivots import WEIGHTS
from .utils import hamming_hex

LABELS = [(0.97, "duplikat / ta sama reklama"), (0.85, "ta sama kampania (silne powiązanie)"),
          (0.6, "prawdopodobnie ta sama kampania"), (0.3, "podobny kit / słabe powiązanie"),
          (0.0, "brak istotnego powiązania")]


def doc_freq(records: list[dict]) -> Counter:
    df = Counter()
    for r in records:
        for k in {(p["kind"], p["value"]) for p in r.get("pivots", [])}:
            df[k] += 1
    return df


def rarity(df: int) -> float:
    return min(1.0, 1.5 / math.log2(df + 1)) if df > 0 else 1.0


def compare(a: dict, b: dict, df: Counter | None = None) -> dict:
    df = df or Counter()
    pa = {(p["kind"], p["value"]): p for p in a.get("pivots", [])}
    pb = {(p["kind"], p["value"]): p for p in b.get("pivots", [])}
    shared = []
    for key in pa.keys() & pb.keys():
        p = pa[key]
        r = rarity(df.get(key, 2))
        shared.append({**p, "rarity": round(r, 3), "contribution": round(p["weight"] * r, 3),
                       "description": WEIGHTS.get(p["kind"], (0, "", ""))[2]})
    # rozmyte: szkielet DOM
    da = [p for p in a.get("pivots", []) if p["kind"] == "dom_simhash"]
    db = [p for p in b.get("pivots", []) if p["kind"] == "dom_simhash"]
    for x in da:
        for y in db:
            if x["value"] == y["value"]:
                continue
            h = hamming_hex(x["value"], y["value"])
            if h <= 6:
                w = WEIGHTS["dom_simhash"][0] * (1 - h / 8)
                shared.append({**x, "value": f"{x['value']}~{y['value']} (Hamming {h})", "rarity": 1.0,
                               "contribution": round(w, 3), "description": WEIGHTS["dom_simhash"][2]})
    best: dict[str, float] = defaultdict(float)
    for s in shared:
        best[s["kind"]] = max(best[s["kind"]], s["contribution"])
    score = 1 - math.prod(1 - c for c in best.values()) if best else 0.0
    shared.sort(key=lambda s: -s["contribution"])
    identity = [s for s in shared if s["scope"] == "identity"]
    label = next(lbl for thr, lbl in LABELS if score >= thr)
    if label.startswith("duplikat") and not identity:
        label = LABELS[1][1]       # bez wspólnej domeny/ID reklamy to nie duplikat, tylko ta sama kampania
    return {"a": a.get("record_id"), "b": b.get("record_id"), "score": round(score, 3), "label": label,
            "duplicate": bool(identity) and score >= 0.97,
            "scopes": sorted({s["scope"] for s in shared if s["contribution"] >= 0.3}),
            "evidence": shared}


def cluster(records: list[dict], threshold: float = 0.75) -> dict:
    df = doc_freq(records)
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    edges = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            c = compare(records[i], records[j], df)
            if c["score"] >= 0.3:
                edges.append({"i": i, "j": j, **c})
            if c["score"] >= threshold:
                parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in range(len(records)):
        groups[find(i)].append(i)
    return {"clusters": [g for g in groups.values()], "edges": edges}


def _main():
    ap = argparse.ArgumentParser(description="Łączenie rekordów OSINT w kampanie (referencyjnie)")
    ap.add_argument("jsonl")
    ap.add_argument("--threshold", type=float, default=0.75)
    ap.add_argument("--evidence", type=int, default=6, help="ile przesłanek pokazać na krawędź")
    a = ap.parse_args()
    recs = [json.loads(line) for line in open(a.jsonl, encoding="utf-8") if line.strip()]
    out = cluster(recs, a.threshold)
    name = lambda i: f"{recs[i].get('_label') or recs[i]['record_id']} ({', '.join(recs[i]['landing']['registered_domains']) or '?'})"  # noqa: E731
    print(f"== {len(recs)} rekordów, {len(out['clusters'])} grup (próg {a.threshold})")
    for g in sorted(out["clusters"], key=len, reverse=True):
        print("  •", " | ".join(name(i) for i in g))
    print("\n== krawędzie (score ≥ 0.3)")
    for e in sorted(out["edges"], key=lambda e: -e["score"]):
        print(f"\n{name(e['i'])}  <->  {name(e['j'])}\n   score={e['score']}  {e['label']}  zakresy={e['scopes']}")
        for s in e["evidence"][: a.evidence]:
            print(f"     +{s['contribution']:.2f}  {s['kind']:<22} {s['value'][:90]}")


if __name__ == "__main__":
    _main()
