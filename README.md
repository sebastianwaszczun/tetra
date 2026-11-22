# FakeCollector – Kolekcjoner fejków (HackNation · NASK)

System, który przyjmuje podejrzaną reklamę w dowolnej postaci (link, zrzut ekranu, film, zapis strony,
tekst), rozpoznaje, do której **kampanii** oszustów należy i **dlaczego**, oraz pokazuje, które kampanie
prowadzi ten sam **operator** (wspólna infrastruktura).

```
wejście ─► preprocessing ─► moduły cech ─► rekord materiału ─► porównanie i grupowanie ─► graf + API + front
(link, plik, folder)        tekst · OSINT · obraz · audio/wideo   (Postgres+pgvector)   kampanie · operatorzy
```

| Etap | Kod | Co robi |
|---|---|---|
| Preprocessing | `src/fakecollector/ingest/` | rozpoznaje wejście, otwiera link **w kontenerze** (Chromium), rozkłada materiał na części dla modułów (tekst, HAR/HTML, obrazy, nagrania), wykrywa duplikaty |
| Moduł tekstu | `src/fakecollector/features/text/` | osoby, marki, kwoty, perswazja, szkielet tekstu, odciski: sha256, MinHash, embeddingi |
| Moduł OSINT | `src/fakecollector/features/osint/` | domeny, przekierowania, piksele i konta reklamowe, kit, serwery, DNS/WHOIS/TLS → pivoty |
| Moduł obrazu | `src/fakecollector/features/image/` | hashe percepcyjne, CLIP, twarze (ArcFace), logo, QR |
| Moduł audio i wideo | `src/fakecollector/features/av/` | transkrypcja (Whisper), odcisk głosu (ECAPA), odciski dźwięku i obrazu filmu, napisy z ekranu |
| Rekord materiału | `src/fakecollector/record/` | wszystkie moduły → jeden rekord → Postgres (pgvector) → wyszukiwanie podobnych |
| Grupowanie | `src/fakecollector/grouping/` | porównanie par modułami, kampanie i operatorzy, przesłanki |
| Graf | `src/fakecollector/graph/` | graf powiązań dla frontu (`/graph`) i kopia w Neo4j |
| Skaner Biblioteki Reklam | `src/fakecollector/adlib/` | skan reklam Meta co 3 h, filtr wstępny, tropy kampanii, wysyłka podejrzanych do `/analyze` (usługa `scheduler`) |
| Ewaluacja | `src/fakecollector/eval/` | jakość grupowania względem etykiet: pary (precyzja, czułość, F1), ARI, przypisywanie nowych materiałów, ablacja modułów, dobór progu |
| API | `src/fakecollector/api/main.py` | FastAPI, dokumentacja: http://localhost:8000/docs |
| Front | `front/` | React (Vite), http://localhost:5173 – opis w `front/README.md` |

Każdy moduł ma opis w swoim folderze (`README.md`), np. `src/fakecollector/features/text/README.md`.

## Usługi

| Usługa | Co robi | Adres |
|---|---|---|
| **api** | FastAPI – wejście dla frontu | http://localhost:8000/docs |
| **app** | Python 3.11 + modele ML + JupyterLab, tu uruchamiamy CLI | http://localhost:8888 (token `fake`) |
| **postgres** | PostgreSQL 16 + pgvector – rekordy, wektory, kampanie | `localhost:5432` (fake / fake) |
| **neo4j** | kopia grafu powiązań do przeglądania | http://localhost:7474 (neo4j / fakecollector) |

## Start od zera

Wymagany Docker Desktop (Windows/macOS) albo Docker Engine (Linux), ok. 16 GB RAM i 25 GB miejsca.

```bash
cp .env.example .env                      # Windows: copy .env.example .env
docker login                              # zalecane: Docker Hub bez logowania ma limit pobrań (błąd 429)
docker compose build app                  # pierwszy build 15–30 min
docker compose up -d
docker compose exec app python scripts/image/pobierz_modele_obrazu.py --modele siglip2,clip,buffalo_l,yunet_sface,magface,facetorch
docker compose exec app python scripts/smoke_test.py      # 23 testy, ok. 5 min
```

Whisper i SpeechBrain pobierają się same przy pierwszym użyciu (ok. 1 GB) do wspólnego wolumenu `models`.

### Dane z paczki

Dane organizatora **nie trafiają do repo** (regulamin). Rozpakuj je do `data/` tak, żeby powstało
`data/BACKUP/p1…p15` i `data/DANE/1…61`. Używaj **7-Zip** (archiwum jest szyfrowane AES), a folder
projektu dodaj do wyjątków Windows Defendera – inaczej Defender usuwa zapisane strony HTML.

### Wypełnienie bazy (ok. 15 minut)

```bash
docker compose exec app python -m fakecollector.record add data/BACKUP data/DANE --no-asr
docker compose exec app python -m fakecollector.grouping rebuild
docker compose exec app python -m fakecollector.graph sync
```

`record add` analizuje i zapisuje materiały, `rebuild` składa kampanie, `graph sync` odświeża Neo4j.
`--no-asr` pomija transkrypcję (szybciej); odcisk głosu i dźwięku liczy się nadal.

Czysta baza od nowa (np. po ponownym `record add` tych samych folderów, które tworzy duplikaty):
```bash
docker compose exec postgres psql -U fake -d fakecollector -c "TRUNCATE materials, campaigns RESTART IDENTITY CASCADE"
docker compose exec app rm -f data/store/index.json
```

### Front
```bash
cd front
npm install
npm run dev                               # http://localhost:5173 (adres API: VITE_API_URL, domyślnie :8000)
```

## API (najważniejsze)

| Adres | Zwraca |
|---|---|
| `POST /analyze` (multipart: `text`, `files`) | analiza materiału, zapis, `assignment` (kampania, status, wynik, przesłanki), `candidates` |
| `GET /stats` | liczby: materiały, kampanie, operatorzy, do weryfikacji |
| `GET /campaigns`, `GET /campaigns/{id}` | kampanie; szczegóły z materiałami, powiązaniami i kampaniami tego samego operatora |
| `GET /materials/{id}`, `GET /materials/{id}/images` | rekord materiału (bez wektorów), zrzuty i klatki |
| `POST /materials/{id}/decision` | decyzja analityka: `potwierdz` / `odrzuc` / `cofnij` |
| `GET /graph` (`?campaign_id=`, `?operator=`, `?material_id=`) | graf `{nodes, edges, stats, legend}` (front rysuje go przez react-force-graph) |
| `GET /compare?a=&b=` | porównanie dwóch materiałów wszystkimi modułami |
| `GET /adlib/status`, `GET /adlib/ads` | stan skanera Biblioteki Reklam i znalezione reklamy |
| `POST /text/analyze`, `/image/analyze`, `/image/compare`, `/av/compare` | pojedyncze moduły (do testów) |

Operacje administracyjne są w wierszu poleceń (kontener `app`, `api` albo `scheduler`), nie w publicznym API:

```bash
python -m fakecollector.grouping rebuild          # przeliczenie kampanii dla całej bazy
python -m fakecollector.graph sync                # kopia grafu w Neo4j
python -m fakecollector.eval run | show           # ewaluacja jakości grupowania
python -m fakecollector.adlib --raz | --ponow    # skan Biblioteki Reklam teraz / ponowna analiza reklam bez rekordu
python -m fakecollector.record delete <id>        # usunięcie materiału
```

`POST /analyze` trwa od kilku sekund do 1–2 minut (OSINT z DNS/WHOIS, modele obrazu, transkrypcja filmu) –
front musi czekać na odpowiedź. Przełączniki: `fetch`, `osint`, `av`, `asr`, `image`, `save`, `group`.

## Jak powstaje kampania

1. **Kandydaci** – rekord zawiera klucze (hash tekstu, pasma MinHash, domena, piksel, hash obrazu, QR,
   odcisk dźwięku) i wektory (tekst, obraz, twarze, głos). Baza zwraca materiały, z którymi coś się zgadza.
2. **Porównanie** – każdy moduł porównuje swoje cechy (`compare`). Wynik poniżej progu szumu modułu nic nie
   wnosi; wkłady łączymy wzorem **1 − Π(1 − wkład)**: jeden pewny dowód wystarcza, brak modułu nie obniża wyniku.
3. **Dwa poziomy**:
   * **kampania** – ta sama treść: szablon tekstu, te same kreacje, twarz lektora, to samo nagranie,
     ta sama domena (przy tym samym zestawie stron) lub ta sama reklama Meta,
   * **operator** – wspólna infrastruktura: Pixel Meta, GA/GTM, katalog kitu, serwer aktora, certyfikat,
     wspólne grafiki stron. Łączy kampanie, ale ich nie scala.
4. **Decyzja**: ≥ 0.75 *przypisany*, 0.5–0.75 *do weryfikacji* (decyduje analityk), poniżej *nowy wzorzec*.
5. **Szum**: ślady skopiowane z podszywanego serwisu (favicon, CSS/JS, beacony Gazeta.pl/TVN24) obecne w > 30%
   bazy nie łączą materiałów; kopie skryptów Facebooka/Google z zapisanych stron nie są śladem kitu.

Wynik na paczce: kampania Brzoski (p1, p2, p7, p15, DANE/1, DANE/2), Stanowski/Sekielski (p3, p4),
Nawrocki (p8, p11) oraz osobne kampanie Olejnik, „37 minut po emisji” i Glowpixel – wszystkie pod jednym
operatorem (ten sam Pixel Meta i serwer aktora). Drugi operator: suplementy Kardivix, Metabo RX, Uromexil
i biała strona „Cactus Recipe”.

## Ewaluacja

Sprawdza, jak dobrze grupowanie odtwarza kampanie przypisane ręcznie w `data/labels.csv`
(kolumny: `source`, `kampania`, `operator`, `sprawdzone`, `uwagi`).

```bash
docker compose exec app python -m fakecollector.eval labels    # szkic etykiet z obecnych kampanii
#   → przejrzyj data/labels.csv: popraw „kampania” tam, gdzie system się mylił, wpisz „tak” w „sprawdzone”
docker compose exec app python -m fakecollector.eval run       # raport: data/eval/raport.md i raport.json
```

| Co mierzymy | Jak | Punkt walidacji z zadania |
|---|---|---|
| Jakość grupowania | pary materiałów: precyzja, czułość, F1; ARI podziału | identyfikowanie wariantów tej samej kampanii |
| Nowy materiał | „wyjmij jeden”: każdy materiał udaje nowy i jest przypisywany do reszty bazy | przypisywanie nowych materiałów do istniejących grup |
| Nowa kampania | „wyjmij kampanię”: cała kampania udaje nieznaną – nie może się dokleić | materiały, które nie powinny zostać połączone |
| Ablacja | wynik bez każdego modułu i z samym modułem | przydatność cech; odporność na brak strony / nagrania |
| Dobór progu | F1 i ARI dla progów 0.50–0.95 | uzasadnienie progu 0.75 |

Wyniki par są zapisywane w `data/eval/pary.json`, więc kolejne uruchomienia (np. po poprawie etykiet)
trwają sekundy; `--fresh` porównuje od nowa (po zmianie modułów lub bazy). Szkic etykiet to kopia wyniku
systemu – dopóki wiersze nie są sprawdzone, raport ostrzega, że metryki są zawyżone.

## Narzędzia wiersza poleceń (w kontenerze `app`)

```bash
python -m fakecollector.ingest data/DANE/47 --no-fetch               # preprocessing jednego przypadku
python -m fakecollector.features.text data/BACKUP/p3 data/BACKUP/p4 --compare
python -m fakecollector.features.av data/DANE/5 data/DANE/14 --compare
python -m fakecollector.record list | show <id> | candidates <id>
python -m fakecollector.grouping campaigns | show <nr> | compare <id_a> <id_b> | assign <id>
python -m fakecollector.graph stats | export data/graf.json | sync
python -m fakecollector.eval labels | run [--fresh] | show
python scripts/smoke_test.py [--fast] [--enrich]                     # test całego potoku
```

Neo4j Browser (http://localhost:7474), przykład: `MATCH (n:FC) RETURN n LIMIT 300` albo
`MATCH (o:Operator)<-[:OPERATOR]-(k:Kampania) RETURN o, k`.

## Codzienna praca

```bash
docker compose up -d          # start
docker compose down           # stop (dane zostają)
docker compose logs -f api    # logi API
```

- Kod jest zamontowany w kontenerach (`src/`, `config/`), API przeładowuje się samo – po zmianie
  kodu nie trzeba builda. Build tylko po zmianie `requirements.txt`, `constraints.txt` lub `Dockerfile`.
- Biblioteka doinstalowana ręcznie (`docker compose exec app pip install …`) znika przy odtworzeniu kontenera.
- Po merge'u cudzego kodu: `python scripts/smoke_test.py`.

## Problemy

- **`429 Too Many Requests` przy buildzie** → `docker login` (limit Docker Hub dla niezalogowanych).
- **`Form data requires "python-multipart"`** albo brak `open_clip_torch` 2.32 → obraz sprzed zmian w
  `requirements.txt`: `docker compose build app` (albo doraźnie `pip install` w kontenerach `app` i `api`).
- **Moduł obrazu bez wektorów CLIP** → nie pobrano modeli: `python scripts/image/pobierz_modele_obrazu.py …`.
- **Materiał z 0 słów tekstu** → brak pliku HTML (Windows Defender) – rozpakuj paczkę ponownie przez 7-Zip.
- **Port zajęty** → zmień porty w `.env`.
- ⚠️ Linków ze scamów nie otwieraj w swojej przeglądarce – system otwiera je tylko w kontenerze.
