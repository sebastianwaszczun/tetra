"""Kopia grafu powiązań w Neo4j – do przeglądania w Neo4j Browser (http://localhost:7474) i zapytań Cypher.

Węzły dostają etykietę :FC oraz etykietę typu (:Operator, :Kampania, :Material, :Domena, :Osoba,
:Konto, :Kit, :Infrastruktura). Przykładowe zapytania:

    // wszystkie kampanie operatora 1 i ich domeny
    MATCH (o:Operator {operator_group: 1})<-[:OPERATOR]-(k:Kampania)<-[:W_KAMPANII]-(m:Material)-[:UZYWA]->(d:Domena)
    RETURN o, k, m, d

    // co łączy dwie kampanie
    MATCH p = (k1:Kampania)<-[:W_KAMPANII]-(:Material)-[:UZYWA]->(x)<-[:UZYWA]-(:Material)-[:W_KAMPANII]->(k2:Kampania)
    WHERE k1 <> k2 RETURN p LIMIT 50
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

NODE_LABEL = {"operator": "Operator", "campaign": "Kampania", "material": "Material", "domain": "Domena",
              "person": "Osoba", "account": "Konto", "kit": "Kit", "infra": "Infrastruktura"}
EDGE_TYPE = {"w_kampanii": "W_KAMPANII", "operator": "OPERATOR", "ta_sama_kampania": "TA_SAMA_KAMPANIA",
             "do_weryfikacji": "DO_WERYFIKACJI", "ten_sam_operator": "TEN_SAM_OPERATOR", "uzywa": "UZYWA",
             "wymienia": "WYMIENIA", "pokazuje": "POKAZUJE"}
BATCH = 500


def _props(d: dict[str, Any]) -> dict[str, Any]:
    """Neo4j przyjmuje tylko liczby, tekst, wartości logiczne i listy prostych wartości."""
    out = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
            out[k] = v
        else:
            out[k] = json.dumps(v, ensure_ascii=False, default=str)
    return out


def sync(graph: dict[str, Any], clear: bool = True) -> dict[str, int]:
    """Zapisuje graf w Neo4j (zastępuje poprzednią kopię węzłów :FC)."""
    from fakecollector.db import neo
    by_label: dict[str, list] = defaultdict(list)
    for n in graph["nodes"]:
        by_label[NODE_LABEL[n["type"]]].append(
            {"id": n["id"], "props": _props({"label": n["label"], "type": n["type"], **n.get("data", {})})})
    by_type: dict[str, list] = defaultdict(list)
    for e in graph["edges"]:
        by_type[EDGE_TYPE[e["type"]]].append(
            {"s": e["source"], "t": e["target"],
             "props": _props({"label": e.get("label"), "weight": e.get("weight"), **e.get("data", {})})})
    with neo().session() as s:
        s.run("CREATE CONSTRAINT fc_id IF NOT EXISTS FOR (n:FC) REQUIRE n.id IS UNIQUE")
        if clear:
            s.run("MATCH (n:FC) DETACH DELETE n")
        for label, rows in by_label.items():
            for i in range(0, len(rows), BATCH):
                s.run(f"UNWIND $rows AS r MERGE (n:FC {{id: r.id}}) SET n:{label} SET n += r.props",
                      rows=rows[i:i + BATCH])
        for rtype, rows in by_type.items():
            for i in range(0, len(rows), BATCH):
                s.run(f"UNWIND $rows AS r MATCH (a:FC {{id: r.s}}), (b:FC {{id: r.t}}) "
                      f"MERGE (a)-[e:{rtype}]->(b) SET e += r.props", rows=rows[i:i + BATCH])
    return {"nodes": sum(len(v) for v in by_label.values()), "edges": sum(len(v) for v in by_type.values())}
