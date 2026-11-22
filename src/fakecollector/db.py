"""Wspólne połączenia z bazami dla całego zespołu.

    from fakecollector.db import pg, neo
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
