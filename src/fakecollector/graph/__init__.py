"""Graf powiązań: kampanie, operatorzy, materiały i wspólne ślady.

    from fakecollector.graph import build_graph
    g = build_graph()                      # cały graf (z Postgresa) – {"nodes": […], "edges": […]}
    g = build_graph(campaign_id=1)         # jedna kampania
    g = build_graph(operator_group=1)      # wszystkie kampanie operatora
    g = build_graph(material_id="ab12…")   # otoczenie jednego materiału

Kopia grafu w Neo4j: python -m fakecollector.graph sync (Neo4j Browser: localhost:7474).
"""
from __future__ import annotations

from .build import LEGEND, build_graph

__all__ = ["build_graph", "LEGEND"]
