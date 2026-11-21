"""Graf powiązań: kampanie, operatorzy, materiały i wspólne ślady.

    from fakecollector.graph import build_graph, sync_neo4j
    g = build_graph()                      # cały graf (z Postgresa) – {"nodes": […], "edges": […]}
    g = build_graph(campaign_id=1)         # jedna kampania
    g = build_graph(operator_group=1)      # wszystkie kampanie operatora
    g = build_graph(material_id="ab12…")   # otoczenie jednego materiału
    sync_neo4j()                           # kopia całego grafu w Neo4j (Neo4j Browser: localhost:7474)
"""
from __future__ import annotations

from typing import Any

from .build import LEGEND, build_graph


def sync_neo4j(**kwargs: Any) -> dict[str, int]:
    from .neo4j_sync import sync
    return sync(build_graph(**kwargs))


__all__ = ["build_graph", "sync_neo4j", "LEGEND"]
