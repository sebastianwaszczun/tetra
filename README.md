# FakeCollector – środowisko zespołu (HackNation · Kolekcjoner fejków)

Jeden `docker compose up` i każdy ma to samo środowisko:

| Usługa | Co robi | Adres |
|---|---|---|
| **postgres** | PostgreSQL 16 + **pgvector** – reklamy, metadane, embeddingi | `localhost:5432` (fake / fake) |
| **neo4j** | Graf powiązań reklam → kampanii, uzasadnienie na każdej krawędzi | http://localhost:7474 (neo4j / fakecollector) |
| **app** | Python 3.11 + ML + JupyterLab | http://localhost:8888 (token: `fake`) |

W kontenerze `app`:
- **Twarze:** InsightFace (ArcFace), facetorch (deepfake, emocje), MagFace (ręcznie, niżej)
- **Obraz:** ImageHash (pHash), CLIP (open_clip), SigLIP (transformers)
- **Tekst:** Tesseract OCR (PL + EN), sentence-transformers, datasketch (MinHash/LSH)
- **Głos:** SpeechBrain (ECAPA – embeddingi mówcy), ffmpeg, librosa
- **Infrastruktura:** requests/httpx, Playwright (Chromium – przekierowania JS, HAR), dnspython, python-whois, ipwhois (RDAP), mmh3 (hash favicony), tldextract, `whois`, `dig`
- **Łączenie:** scikit-learn (HDBSCAN), networkx, sterownik Neo4j, psycopg + pgvector

## Start (pierwszy raz)

Wymagany Docker Desktop (Windows/macOS) lub Docker Engine (Linux). Najlepiej 16 GB RAM i ~20 GB wolnego miejsca.

```bash
cp .env.example .env                  # Windows PowerShell: copy .env.example .env
docker compose up -d --build          # pierwszy build trwa 10–20 min
docker compose exec app python scripts/check_env.py      # wszystko na zielono?
docker compose exec app python scripts/download_models.py   # modele do wspólnego cache
```

Potem otwórz http://localhost:8888 (token `fake`).

## Codzienna praca

```bash
docker compose up -d                  # start
docker compose down                   # stop (dane zostają)
docker compose exec app bash          # terminal w kontenerze
docker compose logs -f app            # logi
```

- Kod piszemy w `src/` (pakiet `fakecollector`, jest na `PYTHONPATH`). Folder jest zamontowany w kontenerze, więc zmiany widać od razu.
- Dane wrzucamy do `data/` (np. rozpakowany `___BACKUP.zip`). Nie trafiają do gita.
- Połączenia z bazami:

```python
from fakecollector.db import pg, neo, add_link

with pg() as c:
    print(c.execute("SELECT count(*) FROM ads").fetchone())

add_link(1, 2, "face", 0.83, 0.7,
         "Ta sama twarz (ArcFace cos=0.83, MagFace q=27) – wizerunek Rafała Brzoski")
```

## Schemat bazy (`db/init.sql`)

`ads` · `faces` (ArcFace 512 + jakość MagFace + deepfake) · `ad_embeddings` (CLIP 512, SigLIP 768, tekst 384, głos 192) · `infra` (przekierowania, IP, NS, rejestrator, certyfikaty, favicon, trackery, Pixel ID) · `links` (krawędź + **uzasadnienie**) · `campaigns` · `reference_people`. Indeksy HNSW (cosine) na wszystkich wektorach.

Skrypt wykonuje się tylko przy **pustej** bazie. Reset bazy (usuwa dane!):
```bash
docker compose down -v && docker compose up -d
```

## MagFace

Nie ma pakietu pip:
```bash
docker compose exec app bash
cd /workspace/src && git clone https://github.com/IrvingMeng/MagFace.git
mkdir -p /models/magface   # wrzuć tu wagi magface_epoch_00025.pth (link w README MagFace)
```
Szybki zamiennik na start: norma surowego embeddingu z InsightFace (`face.embedding_norm`) też koreluje z jakością twarzy.

## GPU (opcjonalnie, NVIDIA + Linux/WSL2)

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

## Problemy

- **Port zajęty** → zmień `POSTGRES_PORT` / `JUPYTER_PORT` / `NEO4J_*_PORT` w `.env`.
- **Build pada na insightface** → zwiększ RAM dla Dockera (Docker Desktop → Settings → Resources).
- **Mac M1/M2/M3** → działa (obrazy są dla arm64), tylko na CPU.
- ⚠️ Linków ze scamów nie otwieraj w swojej przeglądarce. Analizuj je w kontenerze (Playwright headless).
