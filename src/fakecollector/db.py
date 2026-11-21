"""Wspólne połączenia z bazami dla całego zespołu.

    from fakecollector.db import pg, neo, add_link
"""
import os
from contextlib import contextmanager

import psycopg
from neo4j import GraphDatabase
from pgvector.psycopg import register_vector


@contextmanager
def pg():
    """Połączenie z Postgresem z obsługą typu vector (numpy <-> vector)."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    register_vector(conn)
    try:
        yield conn
    finally:
        conn.close()


_driver = None


def neo():
    """Współdzielony sterownik Neo4j."""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        )
    return _driver


def add_link(ad_a: int, ad_b: int, signal: str, score: float, weight: float, reason: str):
    """Zapisuje krawędź z uzasadnieniem jednocześnie w Postgresie i w Neo4j."""
    a, b = sorted((ad_a, ad_b))
    with pg() as c:
        c.execute(
            """INSERT INTO links (ad_a, ad_b, signal, score, weight, reason)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (ad_a, ad_b, signal)
               DO UPDATE SET score=EXCLUDED.score, weight=EXCLUDED.weight, reason=EXCLUDED.reason""",
            (a, b, signal, score, weight, reason),
        )
    with neo().session() as s:
        s.run(
            """MERGE (x:Ad {id:$a}) MERGE (y:Ad {id:$b})
               MERGE (x)-[r:LINKED {signal:$signal}]-(y)
               SET r.score=$score, r.weight=$weight, r.reason=$reason""",
            a=a, b=b, signal=signal, score=score, weight=weight, reason=reason,
        )
