# Preprocessing (`fakecollector.ingest`)

Preprocessing to pierwszy krok potoku. Bierze **dowolne wejście** i rozkłada je na składniki.
Każdy składnik trafia do modułu, który umie go przeanalizować. Całe wejście nie jest przypisywane
do jednego modułu: folder z HAR-em, zrzutem i wideo zasila jednocześnie moduł tekstu, OSINT, obrazu
i audio/wideo.

```
wejście ──► detect_input ──► handler ──► Incident (składniki) ──► finalize ──► incident.json
 tekst / URL / zrzut / wideo / HAR / HTML / JSON / ZIP / folder przypadku / upload z frontu
```

## Jak uruchomić

```bash
# cały zbiór, bez pobierania stron z internetu
docker compose exec app python -m fakecollector.ingest data/BACKUP data/DANE --no-fetch

# jeden link (strona otwierana TYLKO w kontenerze, w Chromium)
docker compose exec app python -m fakecollector.ingest "https://przyklad.live/oferta"

# podgląd wybranego tekstu i wynik OSINT
docker compose exec app python -m fakecollector.ingest data/DANE/61 --no-fetch --text --osint
```

Przełączniki: `--no-fetch`, `--no-ocr`, `--no-video`, `--no-assets`, `--no-dedup`, `--no-save`,
`--store DIR`, `--json plik.json`, `--osint`, `--text`, `-v`.

W API: `POST /analyze` (multipart: `text` + `files`). Tego adresu używa już front. Odpowiedź zawiera
`incident`, `routes` (ile składników dostaje każdy moduł), `text_preview` i `text_features`.

W kodzie:

```python
from fakecollector.ingest import ingest, ingest_upload, run_osint
inc = ingest("data/DANE/47", fetch=False)
inc.for_module("image")      # zrzuty, obrazy z *_files, klatki z wideo
inc.for_module("av")         # wideo i wyciągnięty dźwięk (WAV 16 kHz mono)
inc.text, inc.text_origin    # wejście dla modułu tekstu: text.extract(inc)
osint_rec = run_osint(inc)   # moduł OSINT (features/osint)
```

## Co trafia do którego modułu

| Moduł | Rodzaje składników | Skąd się biorą |
|---|---|---|
| tekst | `text` | zapisany HTML, `page_text.txt`, HTML z HAR-a, metadane Biblioteki Reklam, OCR zrzutów, tekst użytkownika |
| OSINT | `url`, `har`, `html`, `json` | podany link, adresy z HTML/HAR/metadanych/OCR, pliki HAR, strony, `dns_ssl.json`, ciasteczka |
| obraz | `screenshot`, `image`, `frame` | zrzuty ekranu, obrazy z folderów `*_files`, klatki z wideo (co sekundę, przy dłuższych filmach rzadziej, maks. 60) |
| audio/wideo | `video`, `audio` | pliki wideo, ścieżka dźwiękowa wyciągnięta przez ffmpeg |

## Wybór tekstu dla modułu tekstu

1. Tekst wpisany przez użytkownika dołączamy zawsze, o ile to coś więcej niż sam link.
2. Tekst strony: dla **każdej strony osobno** (np. `DANE/61/strona_1..3`) bierzemy najlepsze źródło:
   zapisany HTML > `page_text.txt` > HTML wyciągnięty z HAR-a. Identyczne teksty liczymy raz.
3. Gdy nie ma strony: opis reklamy z Biblioteki Reklam (`adlib`), inne metadane (`json`), transkrypcja
   z modułu audio/wideo (`transcript`), OCR. Źródło krótsze niż 15 słów ustępuje następnemu.
   Wybór robi `select_text(inc)`; moduł audio/wideo wywołuje ją ponownie po dopisaniu transkrypcji.
4. **Strony błędów** (502 Cloudflare, 404, parking domeny, „Just a moment…”) są pomijane. Użyjemy ich
   tylko wtedy, gdy nic innego nie ma, i dopisujemy to w `missing`.

`text_origin` mówi modułowi tekstu, jak bardzo ufać tekstowi (html 1.0, OCR 0.6 itd.).

## Folder przypadku

Kolejność: metadane JSON → strony HTML → HAR → pliki `.txt` → zrzuty (OCR, maks. 12) → wideo → audio → ZIP.

* Pliki w `*_files/` to zasoby zapisanej strony: obrazy trafiają do modułu obrazu (maks. 150).
* ZIP rozpakowany już obok (np. `archive_dorquesio.click_…`) nie jest czytany drugi raz.
* W archiwum strony `source.html` pomijamy, gdy jest `rendered.html`.
* Plik nazwany samym rozszerzeniem (`.har`) też jest rozpoznawany.
* Zapis strony narzędziem page-capture (`manifest.json` + `resources/`): adres strony z manifestu,
  obrazy z `resources/` idą jako zasoby strony (nie jako zrzuty do OCR).
* Wideo ze strony zapisane jako strumień (`.m3u8` + kawałki `.ts`) jest sklejane w jeden plik mp4
  (`data/store/<id>/av/stream_N.mp4`); brakujące kawałki są wpisywane do braków. Pliki `.ts` są
  rozpoznawane po zawartości, więc skrypty TypeScript nie zostaną pomylone z wideo.
* Popularne domeny z OCR (`cloudflare.com`, „AMAZON.COM” w treści reklamy) nie są adresem materiału.

## Pobieranie stron (URL)

`url.fetch()` otwiera stronę w Chromium (Playwright) **wyłącznie w kontenerze**, nagrywa HAR,
zapisuje HTML i zrzut całej strony, zbiera łańcuch przekierowań. Udaje telefon z aplikacji Facebooka
(ten sam User-Agent i Referer co moduł OSINT), bo strony scamowe pokazują botom pustą stronę.
Gdy Playwright zawiedzie, próbujemy zwykłego HTTP. Adresy prowadzące do sieci prywatnej
(np. `http://postgres:5432`) są blokowane. Martwa strona daje `status = "dead"`, a analiza idzie
dalej na zapisanych materiałach.

## Wynik: `Incident`

Zapisywany jako `data/store/<id>/incident.json`. Najważniejsze pola:

| Pole | Znaczenie |
|---|---|
| `input_type`, `source` | co przyszło na wejściu |
| `artifacts` | lista składników: `kind`, `path` albo `value`, `source`, `meta`, `sha256` |
| `text`, `text_origin`, `title` | wejście dla modułu tekstu |
| `urls`, `final_url`, `redirect_chain` | adresy (pierwszy = główny) i przekierowania |
| `status` | `ok`, `partial` (brakuje tekstu, strony albo obrazu), `dead` (strona nie działa) |
| `missing`, `errors` | czego brakuje (pokazujemy w GUI) i co się nie udało |
| `fingerprint`, `duplicate_of` | wykrywanie tego samego materiału zgłoszonego drugi raz |
| `case_dir`, `store_dir` | folder przypadku i folder z plikami wytworzonymi (klatki, dźwięk, pobrane strony) |

Indeks duplikatów to na razie `data/store/index.json`. Docelowo zastąpi go tabela w Postgresie.

## Pliki

| Plik | Rola |
|---|---|
| `core.py` | `ingest()`, `ingest_upload()`, obsługa każdego rodzaju wejścia, folder przypadku, `finalize()`, `run_osint()` |
| `models.py` | `Incident`, `Artifact`, tabela `ROUTES` (który moduł co dostaje) |
| `detect.py` | rozpoznanie rodzaju wejścia |
| `url.py` | pobieranie strony (Playwright, zapas: HTTP), blokada adresów prywatnych |
| `har.py` | główny dokument, pierwsza nawigacja i przekierowania z HAR-a |
| `screenshot.py` | OCR (tesseract pol+eng, długie zrzuty w kawałkach), adresy w tekście |
| `video.py` | ffprobe, dźwięk do WAV 16 kHz, klatki (maks. 60 na film) |
| `dedup.py` | sha256, normalizacja URL (bez utm_, fbclid…), odcisk, indeks duplikatów |
| `__main__.py` | CLI |

## Wyniki na danych z paczki

* BACKUP p1–p15: wszystkie 15 przypadków mają tekst strony (122–5110 słów). W p1 strona błędu 502
  z archiwum została pominięta, a tekst wzięty z zapisanej strony Gazeta.pl.
* Ponowne podanie tego samego folderu jest oznaczane jako duplikat.
* DANE/5 (wideo z Biblioteki Reklam): dźwięk + klatki dla modułu AV i obrazu, adres reklamy dla OSINT.
  Tekst to sam tytuł („Czytać”), więc treść da dopiero transkrypcja z modułu AV.
* DANE/61: teksty trzech stron połączone (611 słów).
