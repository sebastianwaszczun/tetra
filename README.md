# TETRA – Kolekcjoner fejków

**HackNation 2026 · wyzwanie NASK „Follow the fake”**

TETRA przyjmuje podejrzaną reklamę w dowolnej postaci – link, zrzut ekranu, film, zapis strony, tekst – i
odpowiada na trzy pytania: **do której kampanii oszustów należy**, **dlaczego** (przesłanki przy każdym
połączeniu) i **kto za nią stoi** (kampanie tego samego operatora, czyli ze wspólną infrastrukturą).
Skaner Biblioteki Reklam Meta sam wyszukuje nowe podejrzane reklamy i przepuszcza je przez ten sam proces.

```
wejście ─► preprocessing ─► moduły cech ─► rekord materiału ─► porównanie par ─► kampanie i operatorzy ─► graf · API · front
link, plik,   (w kontenerze)  tekst · OSINT ·   Postgres + pgvector   przesłanki       przypisany / do weryfikacji /
folder, tekst                 obraz · audio/wideo                                       nowy wzorzec
```

## Spis treści

1. [Uruchomienie – jedna komenda](#uruchomienie--jedna-komenda)
2. [Dokumentacja](#dokumentacja)
3. [Jak spełniamy wymagania zadania](#jak-spełniamy-wymagania-zadania)
4. [Jak korzystać z systemu](#jak-korzystać-z-systemu)
5. [Jak powstaje kampania](#jak-powstaje-kampania)
6. [Wyniki](#wyniki)
7. [Architektura i kod](#architektura-i-kod)
8. [API](#api)
9. [Narzędzia wiersza poleceń](#narzędzia-wiersza-poleceń)
10. [Praca zespołu](#praca-zespołu)
11. [Problemy](#problemy)
12. [Licencje i dane](#licencje-i-dane)

## Uruchomienie – jedna komenda

Wymagany **Docker Desktop** (Windows/macOS) albo **Docker Engine z Compose ≥ 2.20** (Linux), ok. 12 GB RAM
dla Dockera, 25 GB miejsca i internet przy pierwszym starcie. Nie trzeba instalować Pythona ani Node.js.

```bash
git clone <adres repozytorium> tetra
cd tetra
docker compose up -d
```

Następnie otwórz **http://localhost:8080**. Plik `.env` jest opcjonalny – wszystko ma wartości domyślne.

Pierwszy start buduje obraz z modelami ML i pobiera modele (razem 20–40 min, zależnie od łącza);
kolejne starty trwają kilkanaście sekund. Postęp:

```bash
docker compose logs -f init     # schemat bazy i modele – na końcu „System gotowy”
docker compose ps               # stan usług (init i seed kończą się „Exited (0)”)
```

| Usługa | Co robi | Adres |
|---|---|---|
| **web** | front (GUI); `/api/*` przekazuje do API | **http://localhost:8080** |
| **api** | FastAPI – analiza materiałów, kampanie, graf, skaner | http://localhost:8000/docs |
| **init** | jednorazowo przy starcie: schemat bazy, modele, baza twarzy | – |
| **seed** | jednorazowo: wczytuje `data/BACKUP` i `data/DANE` do pustej bazy | – |
| **scheduler** | skaner Biblioteki Reklam Meta co 3 h (wymaga `META_ACCESS_TOKEN`) | – |
| **postgres** | PostgreSQL 16 + pgvector – rekordy, wektory, kampanie | `localhost:5432` (fake / fake) |
| **neo4j** | kopia grafu powiązań do przeglądania | http://localhost:7474 (neo4j / fakecollector) |
| **app** | ten sam obraz z JupyterLab – narzędzia wiersza poleceń | http://localhost:8888 (token `fake`) |

**Paczka organizatora (opcjonalnie).** Dane NASK nie są w repozytorium (regulamin). Żeby od razu mieć
kartotekę kampanii, rozpakuj paczkę **przed** pierwszym startem tak, by powstały `data/BACKUP/p1…p15` i
`data/DANE/…` – usługa `seed` wczyta ją sama (15–25 min, `docker compose logs -f seed`). Archiwum jest
szyfrowane (7-Zip), a folder projektu trzeba dodać do wyjątków antywirusa, bo antywirusy usuwają zapisane
strony scamów.

Szczegóły – wymagania, konfiguracja `.env`, token Meta, baza twarzy, czysta baza: **[`docs/INSTALACJA.pdf`](docs/INSTALACJA.pdf)**.

## Dokumentacja

| Dokument | Zawartość |
|---|---|
| [`docs/INSTALACJA.pdf`](docs/INSTALACJA.pdf) | wymagania, uruchomienie, usługi, dane, konfiguracja, token Meta, testy, problemy |
| [`docs/METODYKA.pdf`](docs/METODYKA.pdf) | jak pozyskujemy cechy, liczymy podobieństwo i grupujemy; braki danych; skaner; ewaluacja z wynikami; ograniczenia |
| [`docs/KAMPANIE.pdf`](docs/KAMPANIE.pdf) | kampanie i operatorzy znalezieni w paczce NASK, wzorce, przydatne źródła metadanych |
| [`docs/LICENCJE.pdf`](docs/LICENCJE.pdf) | biblioteki, modele, usługi i dane wraz z licencjami i ograniczeniami |

Opisy modułów: `src/fakecollector/ingest/README.md`, `features/text/README.md`, `features/av/README.md`,
front: `front/README.md`.

## Jak spełniamy wymagania zadania

| Wymaganie (treść zadania) | Gdzie i jak |
|---|---|
| przyjmowanie materiałów: URL, zrzut, log komunikacji, plik multimedialny, dane ze zbioru | front i `POST /analyze`: link, tekst, zrzut, HTML, HAR, film, audio, JSON, ZIP, folder przypadku |
| automatyczne pozyskanie informacji: tekst, słowa kluczowe, osoby, logotypy, cechy wizualne, domeny, przekierowania, struktura stron, metadane infrastruktury | moduły tekstu, OSINT (HAR, DNS, RDAP/WHOIS, TLS, crt.sh), obrazu (hashe, SigLIP2/CLIP, twarze, logo, QR) i audio/wideo (transkrypcja, głos, odciski) |
| zamiana na cechy porównywalne | rekord materiału: cechy, wektory pgvector, klucze wyszukiwania (MinHash LSH, hashe, pivoty) |
| identyfikacja duplikatów i wariantów | odcisk wejścia (duplikat = 1,0); szkielet tekstu z zamaskowanymi nazwiskami i kwotami; hashe percepcyjne; odciski dźwięku |
| metoda podobieństwa i grupowania w kampanie | wkłady modułów z progiem szumu, `S = 1 − Π(1 − w)`, kampanie jako spójne składowe ≥ 0,75, operatorzy przez wspólną infrastrukturę |
| analiza nowego materiału względem bazy, nowe kampanie | przyrostowe przypisanie: *przypisany* / *do weryfikacji* / *nowy wzorzec* |
| czytelna prezentacja zależności | front: kampanie, operatorzy, materiały, graf powiązań; Neo4j |
| cechy i przesłanki powiązań | przesłanka przy każdej parze („ten sam Pixel Meta …”, „100% wspólnych fragmentów tekstu”) |
| prosty interfejs graficzny | front pod http://localhost:8080 |
| dokumentacja metodyki | `docs/METODYKA.pdf` |
| niekompletne dane, niedostępne strony | analiza zapisanych materiałów, braki opisane przy materiale, brak modułu nie obniża wyniku |
| *dodatkowo:* automatyczne wyszukiwanie reklam w bibliotekach, okresowa aktualizacja | skaner Biblioteki Reklam Meta co 3 h, filtr wstępny, Lista Ostrzeżeń CERT Polska, sprawdzanie zdjętych reklam |
| instrukcja instalacji, kod w Git, kontener | ten plik, `docs/INSTALACJA.pdf`, `docker compose up -d` |
| biblioteki, modele, usługi z licencjami | `docs/LICENCJE.pdf` |

## Jak korzystać z systemu

**Sprawdź** (strona główna):

1. Wklej link, tekst reklamy albo dodaj pliki (spinacz): zrzut, film, HTML, HAR, ZIP.
2. **Szukaj** – analiza trwa od kilku sekund (tekst) do ok. minuty (film z transkrypcją).
3. Wynik: *Pasuje do znanej kampanii* (z wynikiem 0–1 i przesłankami „Dlaczego”), *Do weryfikacji* albo
   *Nowy wzorzec*; „Co znalazł detektyw” pokazuje cechy z modułów, w tym dane OSINT domeny.

**Przegląd kampanii**: liczniki, lista kampanii (filtr „co najmniej 2 materiały”, „do weryfikacji”,
wyszukiwarka po osobie, domenie, marce), mapa powiązań całej bazy oraz sekcja **Nowe z Biblioteki Reklam**
z reklamami znalezionymi przez skaner.

**Kampania** → graf, materiały, najmocniejsze powiązania z przesłankami, kampanie tego samego operatora.
**Operator** → wszystkie kampanie ze wspólną infrastrukturą. **Materiał** → cechy, miniatury, tekst,
otoczenie w grafie i **decyzja analityka** (*Potwierdź* / *Wyłącz z kampanii* / *Cofnij decyzję*).

> ⚠️ Linków ze scamów nie otwieraj we własnej przeglądarce – system otwiera je wyłącznie w kontenerze.
> Antywirus hosta może blokować te strony; system analizuje wtedy zapisane materiały.

## Jak powstaje kampania

1. **Preprocessing** rozpoznaje wejście, otwiera link w kontenerze (Playwright), rozkłada materiał na
   składniki dla modułów, wybiera najlepszy tekst, wykrywa duplikaty i zapisuje braki.
2. **Moduły cech** – tekst (osoby, marki, kwoty, perswazja, narracja, *szkielet*, MinHash, embeddingi), OSINT
   (pivoty: piksele, konta, kit, hosty, certyfikaty, domeny), obraz (hashe, SigLIP2/CLIP, twarze ArcFace/SFace,
   logo, QR), audio i wideo (Whisper, odcisk głosu ECAPA, odcisk dźwięku, dHash klatek).
3. **Kandydaci** z bazy: wspólne klucze (hash tekstu, pasma LSH, domena, piksel, hash obrazu, QR, odcisk
   dźwięku) i najbliższe wektory.
4. **Porównanie pary**: wynik modułu poniżej progu szumu nic nie wnosi, wkład = rzetelność × pewność × wynik,
   razem `S = 1 − Π(1 − wkład)` – jeden pewny dowód wystarcza, brak modułu nie obniża wyniku.
5. **Dwa poziomy**: *kampania* = ta sama treść (szkielet tekstu, kreacje, nagranie, ta sama domena przy tym
   samym zestawie stron); *operator* = wspólna infrastruktura (Pixel Meta, GA/GTM, kit, host aktora,
   certyfikat). Operator łączy kampanie, ale ich nie scala.
6. **Decyzja**: ≥ 0,75 *przypisany*, 0,5–0,75 *do weryfikacji*, poniżej *nowy wzorzec*. Ślady kopiowane z
   podszywanego serwisu (favicona, beacony Gazeta.pl/TVN24) obecne w > 30% bazy nie łączą materiałów.

Pełny opis z progami i ich źródłami: [`docs/METODYKA.pdf`](docs/METODYKA.pdf).

## Wyniki

**Paczka NASK (25 przypadków):** 3 kampanie wieloelementowe i 2 operatorów.

- **Operator A** – fałszywe artykuły z celebrytami (ten sam Pixel Meta, host aktora na AWS, kit
  `/_cdn/production/landing-cdn/`): kampania Brzoski (6 materiałów na 6 domenach), Stanowski/Sekielski,
  Nawrocki/Sikorski, dwie kampanie z Moniką Olejnik, „37 minut po emisji” i strona-przykrywka Glowpixel.
- **Operator B** – suplementy i cudowne leki: Kardivix, Metabo RX, Uromexil Forte, Cactus Recipe.

**Ewaluacja** (`python -m fakecollector.eval run`, etykiety przejrzane ręcznie):

| Miara | Wynik |
|---|---|
| precyzja / czułość / F1 par | 100% / 100% / 100% |
| nowy wariant znanej kampanii trafia do niej („wyjmij jeden”) | 10/10 |
| materiał z nowej kampanii dostaje „nowy wzorzec” | 15/15 |
| nieznana kampania nie dokleja się do innych („wyjmij kampanię”) | 100% |
| zakres progów z identycznym wynikiem | 0,50–0,85 (wybrany 0,75) |
| sam tekst / sam obraz (ablacja, F1) | 100% / 94% |

**Skaner Biblioteki Reklam Meta** (22.11.2026): 1001 reklam → filtr odrzucił 984 → 17 przeanalizowanych.

Opis kampanii i wzorców: [`docs/KAMPANIE.pdf`](docs/KAMPANIE.pdf).

## Architektura i kod

| Etap | Kod | Co robi |
|---|---|---|
| Preprocessing | `src/fakecollector/ingest/` | rozpoznanie wejścia, pobranie strony w kontenerze, składniki dla modułów, duplikaty, braki |
| Moduł tekstu | `src/fakecollector/features/text/` | osoby, marki, kwoty, perswazja, narracja, szkielet, MinHash, embeddingi |
| Moduł OSINT | `src/fakecollector/features/osint/` | HAR/HTML → pivoty: piksele, konta, kit, hosty, DNS/WHOIS/TLS/CT |
| Moduł obrazu | `src/fakecollector/features/image/` | hashe percepcyjne, SigLIP2/CLIP, twarze, logo, QR, deepfake |
| Moduł audio i wideo | `src/fakecollector/features/av/` | Whisper, odcisk głosu, odciski dźwięku i obrazu, napisy z ekranu |
| Rekord materiału | `src/fakecollector/record/` | rekord w Postgresie (pgvector), wyszukiwanie kandydatów |
| Grupowanie | `src/fakecollector/grouping/` | porównanie par, kampanie, operatorzy, decyzje analityka |
| Graf | `src/fakecollector/graph/` | graf dla frontu (`/graph`) i kopia w Neo4j |
| Skaner | `src/fakecollector/adlib/` | Biblioteka Reklam Meta: harmonogram, filtr, tropy, wysyłka do analizy |
| Ewaluacja | `src/fakecollector/eval/` | etykiety, metryki par, ARI, „wyjmij jeden/kampanię”, ablacja, progi |
| Start systemu | `src/fakecollector/bootstrap.py` | usługi `init` (schemat, modele, baza twarzy) i `seed` (wczytanie danych) |
| API | `src/fakecollector/api/main.py` | FastAPI |
| Front | `front/` | React + Vite, graf react-force-graph; serwowany przez Caddy (`deploy/`) |
| Konfiguracja | `config/obraz.toml`, `config/adlib.toml` | progi z opisem źródła każdej wartości |

```
├── docker-compose.yml        cały system (dev/demo); docker-compose.prod.yml – serwer; docker-compose.gpu.yml – NVIDIA
├── Dockerfile                obraz Python 3.11 + modele ML + Playwright
├── deploy/                   obraz frontu (Caddy) i jego konfiguracja
├── config/                   progi modułu obrazu i skanera
├── db/init.sql               rozszerzenia bazy (tabele tworzy kod)
├── docs/                     dokumentacja (PDF)
├── front/                    interfejs graficzny
├── scripts/                  smoke test, narzędzia modułu obrazu
├── src/fakecollector/        kod systemu
├── tests/                    testy jednostkowe (pytest)
└── data/                     dane lokalne – nie trafiają do repo (paczka NASK, baza twarzy, magazyn materiałów)
```

## API

Dokumentacja interaktywna: http://localhost:8000/docs (z frontu: `/api/…`).

| Adres | Zwraca |
|---|---|
| `POST /analyze` (multipart: `text`, `files`) | analiza materiału, zapis, `assignment` (kampania, status, wynik, przesłanki), cechy modułów |
| `GET /stats` | liczby: materiały, kampanie, operatorzy, do weryfikacji |
| `GET /campaigns`, `GET /campaigns/{id}` | kampanie; szczegóły z materiałami, powiązaniami i kampaniami tego samego operatora |
| `GET /materials/{id}`, `GET /materials/{id}/images` | rekord materiału (bez wektorów), zrzuty i klatki |
| `POST /materials/{id}/decision` | decyzja analityka: `potwierdz` / `odrzuc` / `cofnij` |
| `GET /graph` (`?campaign_id=`, `?operator=`, `?material_id=`) | graf `{nodes, edges, stats, legend}` |
| `GET /compare?a=&b=` | porównanie dwóch materiałów wszystkimi modułami |
| `GET /adlib/status`, `GET /adlib/ads` | stan skanera Biblioteki Reklam i znalezione reklamy |
| `POST /text/analyze`, `/image/analyze`, `/image/compare`, `/av/compare` | pojedyncze moduły (do testów) |

`POST /analyze` trwa od kilku sekund do 1–2 minut. Przełączniki: `fetch`, `osint`, `av`, `asr`, `image`,
`save`, `group`.

## Narzędzia wiersza poleceń

Uruchamiane w kontenerze, np. `docker compose exec api <polecenie>`:

```bash
python -m fakecollector.record add data/MOJ_FOLDER                  # analiza i zapis materiałów (plik, folder, zbiór)
python -m fakecollector.record list | show <id> | candidates <id> | delete <id>
python -m fakecollector.grouping rebuild                            # kampanie od nowa dla całej bazy
python -m fakecollector.grouping campaigns | show <nr> | compare <id_a> <id_b>
python -m fakecollector.graph stats | export data/graf.json | sync  # graf; sync = kopia w Neo4j
python -m fakecollector.eval labels | run [--fresh] | show          # ewaluacja (data/labels.csv)
python -m fakecollector.adlib --raz | --ponow                       # skan Biblioteki Reklam teraz / ponowna analiza
python -m fakecollector.ingest data/DANE/47 --no-fetch              # sam preprocessing
python -m fakecollector.features.text data/BACKUP/p3 data/BACKUP/p4 --compare
python -m fakecollector.features.av data/DANE/5 data/DANE/14 --compare
python scripts/smoke_test.py [--fast] [--enrich]                    # test całego potoku
```

Neo4j Browser (http://localhost:7474): `MATCH (n:FC) RETURN n LIMIT 300` albo
`MATCH (o:Operator)<-[:OPERATOR]-(k:Kampania) RETURN o, k`.

## Praca zespołu

```bash
docker compose up -d              # start (buduje obraz, jeśli go nie ma)
docker compose down               # stop – dane i modele zostają
docker compose logs -f api        # logi API
docker compose build app web      # po zmianie requirements.txt, Dockerfile albo frontu
```

- Kod (`src/`, `config/`, `scripts/`) jest zamontowany w kontenerach, API przeładowuje się samo.
- Front w trybie deweloperskim: `cd front && npm install && npm run dev` → http://localhost:5173
  (API: http://localhost:8000; po zmianie `front/package.json` zawsze `npm install`).
- Biblioteka doinstalowana ręcznie (`pip install` w kontenerze) znika przy odtworzeniu kontenera –
  dopisz ją do `requirements.txt`.
- Po merge'u: `docker compose exec app python scripts/smoke_test.py`.
- Czysta baza: `docker compose down`, `docker volume rm fakecollector_pgdata fakecollector_neo4jdata`,
  `docker compose up -d` (modele zostają w wolumenie `models`; `down -v` usuwa także je).

## Problemy

| Objaw | Rozwiązanie |
|---|---|
| `429 Too Many Requests` przy budowie | limit Docker Hub → `docker login` (i potwierdzenie kodu na login.docker.com/activate) |
| `docker compose up -d` długo czeka | pierwszy start: `init` pobiera modele → `docker compose logs -f init` |
| nie pobrał się któryś model | system działa bez niego; ponów: `docker compose run --rm init` |
| `Form data requires "python-multipart"`, brak `apscheduler` | stary obraz → `docker compose build app` |
| materiał z 0 słów tekstu | antywirus usunął plik HTML z paczki – rozpakuj ponownie z wyjątkiem w antywirusie |
| antywirus: „zablokowano adres” podczas analizy | ochrona sieci hosta blokuje stronę scamu w kontenerze – to bezpieczne; analiza idzie na zapisanych materiałach |
| port zajęty | zmień port w `.env` (np. `WEB_PORT=8081`) |

Więcej: [`docs/INSTALACJA.pdf`](docs/INSTALACJA.pdf).

## Licencje i dane

Pełna lista: [`docs/LICENCJE.pdf`](docs/LICENCJE.pdf). Najważniejsze:

- **Modele twarzy InsightFace (`buffalo_l`) – tylko niekomercyjne badania.** Alternatywa bez tego
  ograniczenia: YuNet + SFace (już obsługiwane, przełącznik w `config/obraz.toml`).
- **Dane organizatora nie są rozpowszechniane** – nie ma ich w repozytorium.
- Zdjęcia bazy referencyjnej osób mają różne licencje (opis w `zrodla.json`) i zostają lokalnie.
- Meta Ad Library API wymaga własnego tokenu (`META_ACCESS_TOKEN` w `.env`); bez niego działa wszystko
  poza skanerem reklam.
