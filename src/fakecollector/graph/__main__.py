"""CLI grafu powiązań.

    python -m fakecollector.graph stats                    # ile węzłów i krawędzi
    python -m fakecollector.graph export data/graf.json    # graf do pliku (np. dla frontu)
    python -m fakecollector.graph export data/k1.json --campaign 1
    python -m fakecollector.graph sync                     # kopia w Neo4j (http://localhost:7474)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fakecollector.graph", description="Graf powiązań")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("stats", "export", "sync"):
        p = sub.add_parser(name)
        if name == "export":
            p.add_argument("out")
        p.add_argument("--campaign", type=int)
        p.add_argument("--operator", type=int)
        p.add_argument("--material")
        p.add_argument("--no-entities", action="store_true")
        p.add_argument("--all-entities", action="store_true", help="także ślady występujące w jednym materiale")
    a = ap.parse_args(argv)
    from . import build_graph
    g = build_graph(campaign_id=a.campaign, operator_group=a.operator, material_id=a.material,
                    entities=not a.no_entities, shared_only=not a.all_entities)
    print(f"Węzły: {g['stats'].get('nodes', 0)} {g['stats'].get('by_node_type', {})}")
    print(f"Krawędzie: {g['stats'].get('edges', 0)} {g['stats'].get('by_edge_type', {})}")
    if a.cmd == "export":
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(g, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"Zapisano {a.out}")
    elif a.cmd == "sync":
        from .neo4j_sync import sync
        r = sync(g)
        print(f"Neo4j: zapisano {r['nodes']} węzłów i {r['edges']} krawędzi. "
              f"Podgląd: http://localhost:7474 → MATCH (n:FC) RETURN n LIMIT 300")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
