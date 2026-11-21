"""Sprawdza, czy środowisko działa: biblioteki, Tesseract (PL), Postgres+pgvector, Neo4j.

Uruchom:  docker compose exec app python scripts/check_env.py
"""
import importlib
import os
import shutil
import subprocess

OK, FAIL = "\033[32mOK\033[0m", "\033[31mBŁĄD\033[0m"
failed = []

LIBS = [
    ("torch", "torch"), ("torchaudio", "torchaudio"),
    ("insightface", "insightface"), ("onnxruntime", "onnxruntime"),
    ("facetorch", "facetorch"), ("cv2", "opencv"),
    ("imagehash", "ImageHash"), ("open_clip", "open_clip_torch"),
    ("transformers", "transformers (SigLIP)"),
    ("pytesseract", "pytesseract"), ("sentence_transformers", "sentence-transformers"),
    ("datasketch", "datasketch (MinHash/LSH)"),
    ("speechbrain", "SpeechBrain"),
    ("tldextract", "tldextract"), ("dns", "dnspython"), ("whois", "python-whois"),
    ("ipwhois", "ipwhois (RDAP)"), ("mmh3", "mmh3"), ("playwright", "playwright"),
    ("sklearn", "scikit-learn"), ("networkx", "networkx"), ("neo4j", "neo4j driver"),
    ("psycopg", "psycopg"), ("pgvector", "pgvector"),
]


def check(name, fn):
    try:
        info = fn()
        print(f"  {OK}   {name} {info or ''}")
    except Exception as e:  # noqa: BLE001
        failed.append(name)
        print(f"  {FAIL} {name}: {type(e).__name__}: {e}")


print("== Biblioteki")
for mod, label in LIBS:
    check(label, lambda m=mod: getattr(importlib.import_module(m), "__version__", ""))

print("== Narzędzia systemowe")
for tool in ["tesseract", "ffmpeg", "whois", "dig"]:
    check(tool, lambda t=tool: shutil.which(t) or (_ for _ in ()).throw(FileNotFoundError(t)))
check("tesseract: język polski", lambda: "pol" in subprocess.run(
    ["tesseract", "--list-langs"], capture_output=True, text=True).stdout
    or (_ for _ in ()).throw(RuntimeError("brak 'pol'")))
check("HDBSCAN (sklearn)", lambda: __import__("sklearn.cluster", fromlist=["HDBSCAN"]).HDBSCAN.__name__)

print("== Bazy danych")


def pg():
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        v = c.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
        t = c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()
        c.execute("SELECT '[1,2,3]'::vector <=> '[1,2,4]'::vector")
        return f"pgvector {v[0]}, tabel: {t[0]}"


def neo():
    from neo4j import GraphDatabase
    d = GraphDatabase.driver(os.environ["NEO4J_URI"],
                             auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
    d.verify_connectivity()
    with d.session() as s:
        r = s.run("CALL dbms.components() YIELD versions RETURN versions[0] AS v").single()
    d.close()
    return f"Neo4j {r['v']}"


check("PostgreSQL + pgvector", pg)
check("Neo4j", neo)

print()
print("Wszystko działa ✅" if not failed else f"Problemy: {', '.join(failed)} ❌")
raise SystemExit(1 if failed else 0)
