"""Przygotowanie systemu przy starcie (usługi `init` i `seed` w docker-compose).

    python -m fakecollector.bootstrap            # schemat bazy + modele (usługa init)
    python -m fakecollector.bootstrap --seed     # wczytanie danych z SEED_DIRS, gdy baza jest pusta (usługa seed)

Zasady:
  * każdy krok jest idempotentny – kolejne `docker compose up` niczego nie liczą od nowa,
  * brak internetu albo nieudane pobranie jednego modelu nie zatrzymuje systemu: odpowiedni
    moduł zgłosi brak w wyniku analizy (np. „brak wag CLIP”), a reszta działa,
  * baza referencyjna osób (data/reference_people) jest budowana, jeśli są zdjęcia, a brak wektorów,
  * błąd kończy proces kodem 1 tylko wtedy, gdy nie ma połączenia z bazą – bez niej API nie ma sensu.

Zmienne środowiskowe:
  IMAGE_MODELS   modele obrazu do pobrania (domyślnie wszystkie: siglip2,clip,buffalo_l,yunet_sface,magface,facetorch)
  PRELOAD_MODELS 1 = pobierz od razu modele tekstu i audio z Hugging Face (domyślnie 1)
  SEED_DIRS      foldery z materiałami do wczytania, rozdzielone spacją (domyślnie „data/BACKUP data/DANE”)
  SEED_ASR       1 = transkrypcja filmów przy wczytywaniu (domyślnie 1; 0 = szybciej)
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("fakecollector.bootstrap")

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = Path("/models/obraz")
ALL_IMAGE_MODELS = "siglip2,clip,buffalo_l,yunet_sface,magface,facetorch"
HF_REPOS = {
    "tekst (embeddingi)": os.environ.get("TEXT_EMB_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
    "audio (transkrypcja)": os.environ.get("WHISPER_MODEL", "openai/whisper-small"),
    "audio (odcisk głosu)": "speechbrain/spkrec-ecapa-voxceleb",
}


def wait_for_db(timeout: int = 120) -> None:
    import psycopg
    end = time.time() + timeout
    while True:
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as conn:
                conn.execute("SELECT 1")
            return
        except Exception as exc:  # noqa: BLE001
            if time.time() > end:
                raise RuntimeError(f"Brak połączenia z bazą: {exc}") from exc
            log.info("Czekam na bazę… (%s)", exc.__class__.__name__)
            time.sleep(3)


def ensure_schema() -> None:
    """Tabele tworzy kod przy pierwszym połączeniu – wywołujemy je raz, przed startem API."""
    from fakecollector.grouping.campaigns import connect as grouping_connect
    from fakecollector.record.store import connect as record_connect
    with record_connect() as conn:
        conn.execute("SELECT 1")
    with grouping_connect() as conn:
        conn.execute("SELECT 1")
    try:
        from fakecollector.adlib.magazyn import Magazyn
        m = Magazyn()
        m.przygotuj()
        m.zamknij()
    except Exception as exc:  # noqa: BLE001
        log.warning("Schemat skanera Biblioteki Reklam: %s", exc)
    log.info("Schemat bazy gotowy")


def count_materials() -> int:
    from fakecollector.record import count_materials as count
    return count()


def download_image_models() -> None:
    wanted = [m.strip() for m in os.environ.get("IMAGE_MODELS", ALL_IMAGE_MODELS).split(",") if m.strip()]
    script = ROOT / "scripts" / "image" / "pobierz_modele_obrazu.py"
    for name in wanted:
        if (MODELS_DIR / f"{name}.json").is_file():
            log.info("Model obrazu %s – już pobrany", name)
            continue
        log.info("Pobieram model obrazu %s…", name)
        r = subprocess.run([sys.executable, str(script), "--modele", name], cwd=ROOT)
        if r.returncode != 0:
            log.warning("Nie udało się pobrać modelu %s – moduł obrazu zadziała bez niego "
                        "(ponów: docker compose run --rm init)", name)


def preload_hf_models() -> None:
    if os.environ.get("PRELOAD_MODELS", "1") != "1":
        return
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        log.warning("Brak huggingface_hub – modele tekstu i audio pobiorą się przy pierwszej analizie")
        return
    for what, repo in HF_REPOS.items():
        try:
            snapshot_download(repo, allow_patterns=["*.json", "*.txt", "*.model", "*.safetensors", "*.ckpt",
                                                    "*.yaml", "tokenizer*", "*.py"])
            log.info("Model %s (%s) gotowy", what, repo)
        except Exception as exc:  # noqa: BLE001
            log.warning("Model %s (%s) nie pobrany: %s – pobierze się przy pierwszej analizie", what, repo, exc)


def build_reference_faces() -> None:
    """Baza twarzy z data/reference_people/<osoba>/ (zdjęcia + zrodla.json) – budowana raz, gdy brak wektorów."""
    ref = ROOT / "data" / "reference_people"
    people = [d for d in ref.iterdir() if d.is_dir()] if ref.is_dir() else []
    if not people:
        log.info("Brak bazy referencyjnej osób (data/reference_people) – twarze bez nazwisk")
        return
    if list(ref.glob("wektory_*.sqlite3")):
        log.info("Baza referencyjna osób gotowa (%d osób)", len(people))
        return
    log.info("Buduję bazę referencyjną twarzy (%d osób)…", len(people))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "image" / "build_reference.py")], cwd=ROOT)
    if r.returncode != 0:
        log.warning("Nie udało się zbudować bazy twarzy – sprawdź data/reference_people/*/zrodla.json")


def seed() -> None:
    dirs = [d for d in os.environ.get("SEED_DIRS", "data/BACKUP data/DANE").split() if (ROOT / d).is_dir()]
    if not dirs:
        log.info("Brak folderów z materiałami (SEED_DIRS) – pomijam wczytywanie. Materiały dodasz przez front "
                 "albo: docker compose exec api python -m fakecollector.record add <folder>")
        return
    n = count_materials()
    if n:
        log.info("W bazie jest już %d materiałów – pomijam wczytywanie (czysta baza: docker compose down -v)", n)
        return
    log.info("Wczytuję materiały z %s – to potrwa kilkanaście minut", ", ".join(dirs))
    add = [sys.executable, "-m", "fakecollector.record", "add", *dirs]
    if os.environ.get("SEED_ASR", "1") != "1":
        add.append("--no-asr")
    steps = [
        ("analiza i zapis materiałów", add),
        ("składanie kampanii", [sys.executable, "-m", "fakecollector.grouping", "rebuild"]),
        ("kopia grafu w Neo4j", [sys.executable, "-m", "fakecollector.graph", "sync"]),
    ]
    if (ROOT / "data" / "labels.csv").is_file():
        steps.append(("ewaluacja (data/labels.csv)", [sys.executable, "-m", "fakecollector.eval", "run"]))
    for what, cmd in steps:
        log.info("→ %s", what)
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            log.warning("Krok „%s” zakończył się błędem (kod %s)", what, r.returncode)
    log.info("Wczytywanie zakończone: %d materiałów", count_materials())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fakecollector.bootstrap", description=__doc__.split("\n")[0])
    ap.add_argument("--seed", action="store_true", help="wczytaj materiały z SEED_DIRS, gdy baza jest pusta")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        wait_for_db()
        ensure_schema()
    except Exception as exc:  # noqa: BLE001
        log.error("%s", exc)
        return 1
    if a.seed:
        seed()
        return 0
    download_image_models()
    build_reference_faces()
    preload_hf_models()
    log.info("System gotowy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
