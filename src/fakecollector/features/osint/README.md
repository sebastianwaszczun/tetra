# Moduł OSINT: ślady infrastruktury scamowych reklam

Moduł przyjmuje materiał z incydentu: URL, HAR, zapisany HTML, MHTML albo ZIP/folder incydentu. Wyciąga z niego ślady techniczne, które przetrwają rotację domen, i oddaje jeden czysty rekord JSON. Główna część rekordu to lista **pivotów**, czyli znormalizowanych cech `(rodzaj, wartość, waga, zakres, dowód)`. Moduł korelacji porównuje po nich incydenty i łączy je w kampanie.

Moduł nie ocenia, czy strona jest scamem. Odpowiada na pytanie: **po czym rozpoznać, że dwie strony postawił ten sam operator albo ten sam kit?**

```
 URL ─┐                                            ┌─ rekord OSINT (JSON) ─┐
 HAR ─┤   ┌─────────┐   ┌──────────┐   ┌────────┐  │  landing, łańcuch URL │   ┌───────────┐
 HTML ┼──▶│ capture │──▶│ekstrakcja│──▶│wzbogac.│──▶  DNS/RDAP/IP/TLS/CT   ├──▶│ korelator │──▶ kampanie
 MHTML┤   │(wspólny │   │HTML+sieć │   │publiczne│  │  trackery, kit, HTTP  │   │ (pivoty)  │    + uzasadnienie
 ZIP ─┘   │ format) │   └──────────┘   │metadane │  │  pivots[] + quality   │   └───────────┘
          └─────────┘                  └────────┘  └───────────────────────┘
```

---

## 1. Szybki start

```bash
# z katalogu głównego repo
python -m venv .venv && . .venv/bin/activate
pip install -r modules/osint/requirements.txt
playwright install chromium            # tylko dla render=true

cd modules
uvicorn osint.main:app --host 0.0.0.0 --port 8010     # dokumentacja API: http://localhost:8010/docs
```

```bash
# JEDNO wejście na wywołanie: albo `file`, albo `url`. Typ pliku rozpoznawany automatycznie.
curl -X POST localhost:8010/analyze -F "file=@strona.har"
curl -X POST localhost:8010/analyze -F "file=@strona.html"
curl -X POST localhost:8010/analyze -F "file=@strona.mhtml"

# folder incydentu spakowany do ZIP to też jeden plik (HAR + HTML + *_files/ + manifest + info.json)
curl -X POST localhost:8010/analyze -F "file=@incydent.zip"

# URL na żywo (HTTP), opcjonalnie render w Chromium, bez crt.sh
curl -X POST localhost:8010/analyze -F url=https://podejrzana.example/ -F render=true -F ct=false
curl "localhost:8010/analyze?url=https://podejrzana.example/"

# tryb offline (bez DNS/RDAP/ASN/TLS), np. na lotnisku albo do testów
curl -X POST localhost:8010/analyze -F "file=@strona.har" -F enrich=false

# błędy: 400 = podano oba albo nic, 415 = nieobsługiwany typ (np. .png), 413 = za duży plik

curl localhost:8010/pivots/weights     # tabela rodzajów pivotów, wag i opisów
```

Wsadowo, z linii poleceń, cały udostępniony zbiór przetwarza się tak:

```bash
cd modules
python -m osint.cli ~/DANE/do_udostępnienia/* --out ../data/osint_records.jsonl
python -m osint.cli ~/DANE/do_udostępnienia/61 --each      # podfoldery strona_* jako osobne rekordy
python -m osint.correlate ../data/osint_records.jsonl      # grupy + krawędzie z przesłankami
```

Kontener (lekki, bez stosu ML):

```bash
docker build -t fakecollector-osint -f modules/osint/Dockerfile modules
docker run --rm -p 8010:8010 -v "$PWD/data:/data" fakecollector-osint
```

Usługę można dopisać do `docker-compose.yml`:

```yaml
  osint:
    build: { context: ./modules, dockerfile: osint/Dockerfile }
    ports: ["8010:8010"]
    volumes: ["./data:/data"]
```

---

## 2. Wejścia

Wszystkie loadery sprowadzają materiał do jednego formatu (`capture.Capture`: lista zasobów sieciowych i dokumentów HTML). Dzięki temu dalsza ekstrakcja nie zależy od źródła.

| Wejście | Co z niego bierzemy |
|---|---|
| `.har` (DevTools, Playwright, page-capture) | każde żądanie: URL, metoda, status, nagłówki, IP serwera, `redirectURL`, inicjator, treść (JS/CSS/HTML/ikony), ciasteczka, czas obserwacji |
| `.html` zapisany w przeglądarce | oryginalny URL z komentarza `<!-- saved from url=... -->` i pełny wyrenderowany DOM |
| `.mhtml` | URL ze `Snapshot-Content-Location`, główny HTML i zasoby z `Content-Location` |
| `*_files/` (w ZIP/folderze) | skrypty i style zapisane z przeglądarką (`.js.pobrane`) do hashowania, nawet bez oryginalnych URL-i |
| `manifest.json` (page-capture) | URL strony, czas przechwycenia, adresy strumieni wideo |
| `*.info.json` (yt-dlp, Biblioteka Reklam Meta) | ID reklamy w archiwum, **ID i nazwa strony reklamodawcy** |
| `url` | pobranie na żywo: httpx z ręcznym śledzeniem skoków albo Chromium z nagrywaniem HAR |

Zrzuty ekranu i wideo obsługują inne moduły (OCR, twarze, obraz). Jeśli OCR wyciągnie ze zrzutu link, przekazuje go tutaj jako `url`.

---

## 3. Przepływ krok po kroku

1. **Rozpoznanie typu i wczytanie** (`capture.load_bytes`). Typ rozpoznawany po rozszerzeniu, a gdy go brak, po treści: `.har` → `load_har`, `.html` → `load_html`, `.mhtml` → `load_mhtml`, `.json` → manifest page-capture lub yt-dlp, `.zip` → rozpakowanie i każdy plik w środku przez ten sam dispatcher (wynik scalany w jeden incydent). Inny typ zwraca 415. Gdy brakuje URL strony (np. HTML zapisany z `about:internet` albo strona otwarta z pliku lokalnego), landing jest wnioskowany z ruchu i oznaczany `landing.inferred=true`.
2. **Ustalenie landingu**: hosty dokumentów, `saved from url`, łańcuch live, `og:url`/`canonical`. Każdy host dostaje rolę: `landing`, `first_party`, `actor_third_party`, `commodity` (Google, Meta, publiczne CDN-y z bibliotekami) albo `placeholder` (host bez kropki, np. `https://delivered/?id=`, czyli niewypełniona zmienna szablonu).
3. **Ekstrakcja HTML** (`html_extract.py`) z każdego dokumentu. Do wspólnych pól brana jest suma ze wszystkich dokumentów.
4. **Ekstrakcja sieci** (`network_extract.py`): łańcuch przekierowań, hosty i IP, odcisk nagłówków głównego dokumentu, endpointy API i beacony, zasoby kitu, identyfikatory trackerów w URL-ach żądań.
5. **Parametry URL** (`trackers.analyze_params`): ID obiektów Meta Ads, nazewnictwo kont, rodzina TDS, schemat parametrów, znaczniki czasu UUIDv7. Tokeny dostępowe są hashowane.
6. **Wzbogacanie** (`enrich.py`) dla hostów landingu, przekierowań i aktora, równolegle i z budżetem czasu: DNS, RDAP z fallbackiem na WHOIS, IP→ASN, PTR, certyfikat TLS, crt.sh, favicona. Wyniki są cache'owane w `data/osint_cache/` (TTL 24 h). Błędy nie trafiają do cache.
7. **Pivoty** (`pivots.py`): rekord jest zamieniany na listę cech do porównań z wagą i zakresem.
8. **Jakość** (`quality`): które źródła się udały (`checks`), `completeness`, czego brakuje i dlaczego (`missing`), notatki.

---

## 4. Czego szukamy, dlaczego i czym

Domeny scamów żyją kilka dni. W próbce kilka landingów zarejestrowano **dzień przed obserwacją**. Sama domena łączy więc tylko duplikaty. Kampanię łączą rzeczy, które operator **przenosi z domeny na domenę**: konta reklamowe i analityczne, kit (szablon strony i backend), tracker TDS, hosting i nawyki rejestracji.

### A. Konta operatora (najmocniejsze, zakres `operator`)

| Sygnał | Skąd | Dlaczego łączy |
|---|---|---|
| Pixel Meta (`fb_pixel`) | `fbq('init', …)`, `connect.facebook.net/signals/config/<id>`, `facebook.com/tr?id=`, parametry `pxl`/`fbpx` | Pixel należy do konta reklamowego operatora, nie do domeny. Operator przenosi go na kolejne domeny, bo potrzebuje danych o konwersjach. |
| Google Ads `AW-…`, GA4 `G-…`, GTM, UA, TikTok, Yandex, Clarity, Hotjar, Snap, Taboola, Outbrain, AdSense | treść HTML/JS i URL-e żądań (`/pagead/1p-conversion/<n>/` jest normalizowane do `AW-<n>`) | to samo co wyżej: konto analityczne lub reklamowe |
| Tokeny weryfikacji domen (`facebook-domain-verification`, `google-site-verification`) | `<meta>` i rekordy DNS TXT | jeden token odpowiada jednemu Business Managerowi |
| ID obiektów Meta Ads (`fb_ad_object`) | parametry z makr `{{ad.id}}`, `{{campaign.id}}` (`ad_id`, `utm_id`, `utm_content`, `utm_term`), 15–20 cyfr | globalnie unikalne ID kampanii, zestawu i reklamy. Mapowanie parametr→rola różni się między reklamodawcami, więc porównujemy ID niezależnie od nazwy parametru. |
| Nazewnictwo media buyera (`ad_naming`) | `acc=`, `adset_name=`, `ad_name=` | schematy w stylu `hk77_tv6_…` są nawykiem konkretnego zespołu |
| Kampania w TDS (`tds_campaign`) | Keitaro `_trk`, `_camp_id`, `trafficsource`, `user_id` | ta sama kampania w trackerze operatora |
| Kontakty i portfele | telefony (+48 i inne), e-maile, `t.me`/`wa.me`, adresy BTC/ETH/TRX | bezpośredni identyfikator osoby lub kasy |
| Strona reklamodawcy | `info.json` z Biblioteki Reklam (`page_id`) | ta sama strona FB publikuje kolejne reklamy |
| Wycieki tokenów (`secret_param`) | `access_token=EAA…` w URL | do rekordu trafia tylko `sha256`, bez wartości |

Słabe wskazówki, które liczą się dopiero w kombinacji: `fb_id_shard` (końcowe 4 cyfry ID obiektów Meta są w próbce stałe w obrębie konta, traktujemy to jako heurystykę) oraz `naming_script` (cyrylica wklejona w polskie nazewnictwo zestawu reklam).

### B. Kit, czyli szablon i backend (zakres `kit`)

| Sygnał | Jak liczony | Dlaczego |
|---|---|---|
| `kit_dir` | prefiks katalogu statycznych zasobów, kończący się segmentem-identyfikatorem (uuid, długi hex, slug) i współdzielony przez ≥2 zasoby, np. `/_cdn/production/landing-cdn/<uuid>/<uuid>` albo `/lander/<slug>` | identyfikator konkretnego **wdrożenia** szablonu. Ten sam kit_dir na dwóch domenach oznacza tę samą paczkę. |
| `kit_family` | prefiks ścieżki do pierwszego identyfikatora, np. `/_cdn/production/landing-cdn/` | ta sama **platforma** generująca landingi, nawet przy innych wdrożeniach |
| `endpoint` | szablon ścieżki (`{uuid}`, `{n}`, `{hex}`) i nazwy parametrów dla żądań API, POST, XHR i beaconów aktora, np. `POST /api/v1/trigger/field/{uuid}/`; `event8`/`event12` → `event{n}` | backend kitu (zbieranie leadów, telemetria) zmienia się rzadziej niż front |
| `js_hash`, `css_hash`, `inline_js_hash` | sha256 po normalizacji (bez białych znaków i komentarzy). Biblioteki vendorowe i pliki z publicznych CDN są pomijane. | identyczny własny kod to bardzo mocny ślad tego samego kitu |
| `image_hash` | sha256 obrazów ≥4 KB z domeny aktora (4–20 KB z niższą wagą) | ponownie użyte grafiki. Małe ikonki, np. reakcje FB w fałszywych komentarzach, są szumem. |
| `favicon_mmh3` | MurmurHash3 w wariancie Shodana | można od razu szukać w Shodan/Censys: `http.favicon.hash:<mmh3>` |
| `url_param_schema` | nazwy parametrów i **kształt** wartości, np. `utm_id=A20&utm_visit=uuid7` | sygnatura generatora linków TDS, niezależna od konkretnego kliknięcia |
| `form_signature` | metoda, szablon akcji i nazwy pól formularza | ten sam backend zbierający dane ofiar |
| `actor_host`, `media_host` | zewnętrzne hosty spoza listy commodity: trackery, pull-zony CDN (`*.b-cdn.net`), `*.netlify.app`, hosty streamów | infrastruktura pomocnicza współdzielona przez kampanie |
| `dom_simhash` | 64-bitowy simhash szkieletu DOM (4-gramy tagów, rodzic>tag[atrybuty]) | podobny szablon przy innym tekście. Porównujemy odległością Hamminga ≤ 6, a nie równością. |
| `path_layout` | układ katalogów zasobów po szablonowaniu | słabszy odpowiednik kit_dir |

**Cechy zachowania kitu** (`kit_traits`, zakres `behavior`, pojedynczo słabe, zestaw ≥3 cech ma osobny pivot):
`hue_rotate_evasion` (losowy `filter: hue-rotate(Ndeg)` na `<html>`, który psuje hashe percepcyjne zrzutów), `obfuscated_css_classes`, `back_button_hijack`, `webdriver_check` i `devtools_detection` (cloaking), `template_placeholder_leak` (np. `https://delivered/?id=`), `fake_comments`, `dynamic_date`, `countdown_timer`, `hls_video` (fałszywy materiał TV), `lead_form_php`, `phone_mask_input`, `exit_popup`, `service_worker`, `fake_app_store`, `geo_ip_lookup`, `exchange_rate_api`, `google_translate_widget`, `base64_obfuscation`, `context_menu_block`.

### C. Infrastruktura (zakres `infra`)

| Sygnał | Źródło / narzędzie | Dlaczego i jakie są pułapki |
|---|---|---|
| Rejestrator, data utworzenia, wygaśnięcie, status, NS, abuse, rejestrujący | **RDAP** przez `rdap.org` (bootstrap IANA), fallback **WHOIS** (`python-whois`, np. dla `.pl`) | `registration_batch` = rejestrator + TLD + tydzień rejestracji, bo operatorzy kupują domeny partiami. `age_days_at_observation` = wiek domeny w chwili obserwacji (0–1 dzień to typowy sygnał scamu). Rejestrujący liczy się tylko wtedy, gdy nie stoi za proxy prywatności. |
| NS (`ns_set`) | RDAP/DNS | **W Cloudflare para NS jest przydzielana per konto**, więc ta sama para NS na dwóch domenach to prawdopodobnie to samo konto CF. |
| A/AAAA, CNAME, MX, TXT (SPF, weryfikacje), SOA | `dnspython` | MX i SOA rname spoza dużych dostawców to sygnał operatora. `include:` w SPF wskazuje wspólną infrastrukturę pocztową. |
| IP → ASN, CIDR, nazwa sieci, kraj, PTR | `ipwhois` (ASN przez DNS Team Cymru i RDAP RIR-ów), reverse DNS | **IP Cloudflare'a i wielkich chmur są oznaczane `ip_cdn` (waga 0.03)**, bo współdzielą je miliony stron. Wspólny IP na małym VPS to mocny ślad. |
| Certyfikat TLS: SHA-256, wystawca, SAN, ważność | gniazdo TLS z SNI + `cryptography` | ten sam certyfikat albo inne domeny aktora w SAN (`cert_san_peer`) |
| Historia certyfikatów | **crt.sh** (Certificate Transparency) | pierwsze pojawienie się domeny i powiązane nazwy. Bywa wolne i limitowane (429/timeout), wtedy trafia do `quality.missing`. |
| Nagłówki HTTP | z HAR lub live | stos (`server`, `x-powered-by: PHP/8.3.x`, `via: Caddy`, `fly.io`), CDN i POP (`cf-ray` → `WAW`), odcisk zestawu nazw nagłówków (bez zmiennych), CSP (hosty aktora), niestandardowe ciasteczka |

**Stan domeny** (`domains[].status_flags`): `on_hold`, `pending_delete`, `registrar_or_parking_ns`, `sinkhole_ip`, `tls_cert_mismatch`, `nxdomain`, `likely_taken_down`. Domena zawieszona po zgłoszeniu ma NS rejestratora i np. A=1.1.1.1. Jej DNS i TLS opisują wtedy rejestratora, nie aktora, więc **nie generujemy z nich pivotów NS i certyfikatu**. Bez tego wszystkie zawieszone domeny jednego rejestratora skleiłyby się w fałszywą kampanię. Ten przypadek wystąpił w próbce.

### D. Łańcuch URL (`redirect_chain`)

Łańcuch składa się z dokumentów i odpowiedzi 3xx z HAR, skoków live (status, `Location`, IP), przekierowań `meta refresh` i JS `location=` oraz nawigacji w przeglądarce. Hosty przekładek trafiają do wzbogacania z rolą `redirect`. W parametrach są też **znaczniki czasu UUIDv7** (np. `utm_visit`), czyli moment wygenerowania wizyty przez TDS, przydatny do osi czasu kampanii.

---

## 5. Format rekordu

**Kształt jest zawsze ten sam**, niezależnie od wejścia (HAR, HTML, MHTML, JSON, ZIP, URL) i od `enrich`. Szablony są w [schema.py](schema.py):

- każdy klucz jest zawsze obecny;
- brak wartości to `null`, brak elementów listy to `[]`, brak wpisów w mapie to `{}`;
- `domains[].dns`, `.rdap`, `.tls`, `.ct`, `.lexical` mają wartość `null` tylko wtedy, gdy sprawdzenia **nie wykonano** (np. `enrich=false` albo RDAP dla subdomeny platformy typu `*.netlify.app`). Jeśli sprawdzenie wykonano, obiekt ma pełny zestaw pól, a `error` mówi, co się nie udało;
- listy obiektów (`domains`, `ips`, `redirect_chain`, `hosts`, `trackers`, `endpoints`, `pivots`) mogą być puste, ale każdy ich element ma stały zestaw kluczy;
- jedyne mapy z dynamicznymi kluczami to dane, nie schemat: `url_params.ad_ids`, `.naming`, `.click_ids`, `.tds_ids`, `.uuid7_timestamps`, `html.og`, `kit_trait_descriptions`.

Przykład: sam HTML nie ma ruchu sieciowego, więc `http.server` = `null`, `endpoints` = `[]`, `assets.scripts` = `[]`, a `quality.checks.network_capture` = `false`.

Skrót rekordu:

```jsonc
{
  "schema_version": "osint-record/1.0",
  "record_id": "…",                    // stabilny hash wejść
  "observed_at": "2026-10-09T06:25:24Z",
  "inputs": [{"kind": "har", "name": "…", "sha256": "…"}],
  "landing": {"url": "…", "final_url": null, "hosts": ["…"], "registered_domains": ["…"],
              "title": "…", "lang": "pl", "inferred": false},
  "url_params": {"schema": "utm_campaign=A10&…&utm_visit=uuid7", "tds_families": ["utm_visit_kit"],
                 "ad_ids": {"fb_campaign_id": ["…"]}, "naming": {…}, "tds_ids": {…},
                 "uuid7_timestamps": {"utm_visit": "…"}, "secret_hashes": ["sha256:…"]},
  "redirect_chain": [{"url": "…", "status": 302, "type": "http_redirect", "location": "…", "ip": "…"}],
  "domains": [{"domain": "…", "role": "landing", "tld": "live", "platform": null,
               "rdap": {"registrar": "…", "created": "…", "nameservers": […], "status": […]},
               "dns": {"a": […], "ns": […], "mx": […], "txt": […], "soa": {…}},
               "tls": {"issuer_org": "…", "sha256": "…", "sans": […]},
               "ct": {"cert_count": 5, "first_seen": "…", "names": […]},
               "age_days_at_observation": 0, "status_flags": [], "lexical": {"entropy": 3.2, …}}],
  "ips": [{"ip": "…", "cdn": "cloudflare", "asn": "13335", "asn_name": "…", "network_cidr": "…", "ptr": "…"}],
  "http": {"server": "cloudflare", "cdn": "cloudflare", "cdn_pop": "WAW", "server_stack": […],
           "header_fingerprint": "…", "distinct_cookies": […], "csp_hosts": […]},
  "hosts": [{"host": "…", "role": "actor_third_party", "requests": 4, "ips": […]}],
  "trackers": [{"type": "fb_pixel", "id": "…", "sources": ["html", "network"]}],
  "endpoints": [{"method": "POST", "path_template": "/api/v1/trigger/field/{uuid}/", "kind": "api", …}],
  "assets": {"kit_dirs": […], "kit_families": […], "scripts": [{"url": "…", "vendor": false, "norm_sha256": "…"}],
             "stylesheets": […], "images": […], "libs": ["jquery@3.6.0"], "favicon": {"mmh3": 0, "md5": "…"}},
  "html": {"title": "…", "forms": […], "phones": […], "emails": […], "messengers": […],
           "crypto_wallets": […], "outbound_hosts": […], "dom_simhash": "…", "obfuscated_class_ratio": 0.0},
  "kit_traits": ["hue_rotate_evasion", "…"],
  "ad_library": [{"ad_archive_id": "…", "page_id": "…", "page_name": "…"}],
  "pivots": [{"kind": "fb_pixel", "value": "…", "weight": 0.95, "scope": "operator", "evidence": "…"}],
  "quality": {"checks": {"html": true, "dns": true, "rdap": true, "tls": true, …},
              "completeness": 1.0, "strong_pivots": 30, "missing": ["ct …: ReadTimeout"], "notes": […]}
}
```

---

## 6. Jak używać tego przy łączeniu w kampanie

### 6.1 Ocena pary (implementacja referencyjna: `correlate.py`)

1. Znajdź wspólne pivoty `(kind, value)`. `dom_simhash` porównuj rozmyto (Hamming ≤ 6).
2. Wkład pivotu: `c = waga_rodzaju × rzadkość`, gdzie `rzadkość = min(1, 1.5 / log2(df + 1))`, a `df` to liczba rekordów korpusu z tą wartością. Wartość wspólna dla dwóch incydentów daje ok. 0.95, wspólna dla setek bliżej zera. Dzięki temu IDF **sam wygasza szum** (IP Cloudflare'a, popularne biblioteki), którego nie wyłapała lista commodity.
3. W obrębie jednego rodzaju liczy się **maksimum**: 10 wspólnych obrazków z jednej paczki to jeden dowód, nie dziesięć.
4. Rodzaje łączymy przez **noisy-OR**: `score = 1 − Π(1 − c_rodzaju)`. Każdy niezależny ślad podnosi pewność, a wynik zostaje w [0, 1].
5. Etykieta:
   - `≥ 0.97` i wspólny pivot zakresu `identity` (domena, ID reklamy): **duplikat**;
   - `≥ 0.85`: **ta sama kampania**;
   - `≥ 0.6`: **prawdopodobnie ta sama**;
   - `≥ 0.3`: **podobny kit / słabe powiązanie**.

   Pole `scopes` mówi, czym łączy się para: `operator` (to samo konto), `kit` (ten sam szablon), `infra` (ten sam hosting) albo `identity`.
6. Każda krawędź ma listę `evidence`: pivot, wartość, wkład i opis po polsku. To jest gotowe uzasadnienie do UI i do Neo4j.

```python
from osint.correlate import compare, cluster, match_new, doc_freq
cluster(records, threshold=0.75)        # grupy (union-find) + krawędzie z przesłankami
match_new(new_record, corpus, top=5)    # nowy materiał: ranking najbliższych incydentów
```

**Nowy materiał** jest przypisany do kampanii, jeśli jego najlepsze dopasowanie ma `score ≥ 0.75` i zakres `operator` lub `kit`. Jeśli dopasowanie ma tylko zakres `infra`/`behavior` albo wynik jest poniżej progu, zgłaszamy **potencjalnie nową kampanię** i pokazujemy najbliższe incydenty jako kontekst.

### 6.2 Zapis do baz zespołu

- Postgres, tabela `infra(ad_id, kind, value, position)`: jeden wiersz na pivot (`kind = pivot.kind`). Indeks `(kind, value)` od razu daje „pokaż wszystkie reklamy z tym pikselem/kit_dir/NS”.
- Krawędzie: `fakecollector.db.add_link(a, b, "infra:<kind>", score, weight, reason)`, gdzie `reason` to sklejone `evidence` (np. „ten sam Pixel Meta …; ten sam katalog wdrożenia kitu …”).
- Neo4j: graf dwudzielny `(:Ad)-[:HAS]->(:Pivot {kind, value})` pozwala na pivotowanie analityczne i wyszukiwanie ścieżek:

```cypher
MATCH (a:Ad)-[:HAS]->(p:Pivot)<-[:HAS]-(b:Ad)
WHERE a <> b AND p.kind IN ['fb_pixel','kit_dir','google_ads','endpoint']
RETURN a, p, b
```

### 6.3 Fuzja z innymi modułami

Wynik OSINT to **jeden z sygnałów** obok twarzy, obrazu i tekstu. Rekomendujemy fuzję tym samym noisy-OR: sygnały z innych modułów dodajemy jako kolejne „rodzaje” z wagami. Zakresy `operator` i `kit` mogą samodzielnie łączyć kampanie. Zakresy `infra` i `behavior` powinny łączyć tylko razem z innym sygnałem, np. tą samą twarzą albo narracją.

---

## 7. Wyniki na zbiorze przykładowym

Przebieg z wzbogacaniem: 12 rekordów, 0.1–3 s na incydent z cache, do ok. 16 s bez cache. `python -m osint.correlate`, próg 0.75:

| Grupa | Incydenty | Główne przesłanki |
|---|---|---|
| **A: platforma „landing-cdn”** | 1, 2, 3, 48, 61 (strona_1–3) | 1↔2: ten sam Pixel Meta, **identyczny kit_dir** (te same dwa UUID wdrożenia), te same skrypty i obrazy, różne domeny tego samego rejestratora zarejestrowane dzień przed obserwacją. 1/2↔48/61: wspólne endpointy `POST /api/v1/trigger/field/{uuid}/` i `/api/v1/agent/persist-session-record/{uuid}/{uuid}`, wspólna rodzina `/_cdn/production/landing-cdn/`, ta sama favicona i ten sam SDK CSS formularza, schemat `utm_*` z `utm_visit=UUIDv7`, `hue-rotate` na `<html>`. 61/strona_1↔2: ten sam `AW-…` Google Ads. 2↔3: ta sama domena (duplikat). |
| 10 | 10 | osobny wzorzec: fałszywy App Store / PWA, 3 piksele Meta, ID obiektów Meta Ads |
| 14 | 14 | Keitaro (`_trk`, `_camp_id`, sub_id 20 znaków), host trackera aktora z beaconami `/click?event{n}`, wideo HLS, nazewnictwo kont z cyrylicą |
| 36 | 36 | zasoby na `*.netlify.app`, wyciek `access_token` (zahashowany), Pixel z parametru URL |
| 47 | 47 | `/lander/<slug>`, pull-zona BunnyCDN z wideo, formularz `order.php` |
| 5 | 5 | tylko metadane Biblioteki Reklam (ID reklamy, strona reklamodawcy). Brak strony, więc brak infrastruktury. |

Odrzucone fałszywe powiązanie: 14↔47 łączył wcześniej identyczny plik `likes.png` (1.7 KB, ikonka reakcji FB z fałszywych komentarzy). Po wprowadzeniu progu rozmiaru obrazów krawędź spadła poniżej 0.3.

Obserwacje o wzorcach:

- Platforma A rotuje domeny bardzo szybko (wiek 0–1 dnia), każda domena ma inną parę NS Cloudflare, ale kit, endpointy i konta reklamowe zostają.
- Jedna z domen A była już zawieszona w chwili analizy (NS rejestratora, A=1.1.1.1). Flagi stanu zapobiegły fałszywym powiązaniom.

---

## 8. Niekompletne dane i odporność

| Sytuacja | Zachowanie |
|---|---|
| strona już nie działa / NXDOMAIN | analiza HAR/HTML działa w pełni; DNS/TLS z flagą w `status_flags` i wpisem w `quality.missing` |
| domena zawieszona/parkowana | pivoty NS i certyfikatu pomijane (patrz 4.C) |
| brak URL (HTML z `about:internet`, plik lokalny) | landing wnioskowany z ruchu albo `og:url`, flaga `landing.inferred` |
| brak RDAP dla TLD (np. `.pl`) | fallback WHOIS (port 43) |
| crt.sh 429/timeout, wolne RIR-y | budżet czasu `OSINT_ENRICH_BUDGET_S`, brak cache dla błędów, wpis w `missing` |
| cloaking (biała strona dla botów) | mobilny UA z Facebooka i `Referer: l.facebook.com`; HAR od zgłaszającego ma pierwszeństwo przed wynikiem live |
| duplikaty materiałów | ten sam `record_id` dla tych samych plików; w korelacji zakres `identity` |
| różne formaty tego samego incydentu (HAR + HTML + MHTML w jednym ZIP) | scalane w jeden rekord, listy łączone bez powtórzeń, `html_variants` trzyma simhash każdej wersji. Wysłane osobno dają osobne rekordy, które korelator połączy jako duplikat (ta sama domena). |

---

## 9. Konfiguracja (zmienne środowiskowe)

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `OSINT_CACHE_DIR` | `data/osint_cache` | cache wzbogacania |
| `OSINT_CACHE_TTL_S` | 86400 | ważność cache |
| `OSINT_ENRICH_BUDGET_S` | 45 | łączny limit czasu wzbogacania na rekord |
| `OSINT_ENRICH_TIMEOUT_S` | 8 | timeout pojedynczego zapytania |
| `OSINT_HTTP_TIMEOUT_S` | 10 | timeout pobierania live |
| `OSINT_MAX_ENRICH_DOMAINS` / `_IPS` | 8 / 10 | ile hostów/IP wzbogacać |
| `OSINT_ENABLE_CT` | true | crt.sh |
| `OSINT_ALLOW_PRIVATE` | false | wyłącza ochronę SSRF (tylko do testów lokalnych) |
| `OSINT_USER_AGENT`, `OSINT_REFERER`, `OSINT_ACCEPT_LANGUAGE` | mobilny FB / `l.facebook.com` / pl-PL | tożsamość klienta przy pobieraniu live |
| `OSINT_MAX_UPLOAD_BYTES` | 300 MB | limit plików w żądaniu |

---

## 10. Bezpieczeństwo

- **Nie otwieraj linków ze scamów w swojej przeglądarce.** Tryb URL pobiera stronę serwerowo (httpx bez wykonywania JS) albo w odizolowanym Chromium headless. Najlepiej uruchamiać go w kontenerze.
- Ochrona SSRF: adresy prywatne, loopback i link-local są odrzucane na każdym skoku przekierowania i dla podzasobów w trybie HTTP. W trybie `render` Chromium sam śledzi przekierowania; sprawdzany jest tylko adres startowy.
- Tokeny z URL-i (`access_token`, `EAA…`) trafiają do rekordu wyłącznie jako hash.
- Materiały organizatora nie mogą trafić do repozytorium (`data/` jest w `.gitignore`), dotyczy to też cache wzbogacania.

---

## 11. Ograniczenia i dalszy rozwój

- Bez kluczy API nie korzystamy z pasywnego DNS ani historii IP: SecurityTrails, VirusTotal, urlscan.io, Validin. To najcenniejsze rozszerzenie, bo pozwala zobaczyć, co wcześniej stało na tym samym IP lub NS.
- `favicon_mmh3`, `kit_dir`, `endpoint` i hashe JS nadają się do aktywnego wyszukiwania nowych domen kampanii w Shodan, Censys albo urlscan („szukaj po śladzie”). Taki scheduler byłby naturalnym kolejnym krokiem.
- Meta Ad Library API (`ads_archive`) pozwala, po ID strony z `ad_library`, okresowo pobierać nowe reklamy tego samego reklamodawcy.
- Wagi są aprioryczne. Na większym oznaczonym zbiorze można je dostroić, np. regresją logistyczną na parach (to samo / inne), zachowując wyjaśnialność.

---

## 12. Biblioteki i usługi zewnętrzne

| Komponent | Licencja | Użycie |
|---|---|---|
| FastAPI, Uvicorn, python-multipart | MIT, BSD-3, Apache-2.0 | API HTTP |
| httpx | BSD-3 | pobieranie live, RDAP, crt.sh |
| dnspython | ISC | DNS, PTR |
| ipwhois | BSD-2 | IP→ASN/RDAP RIR |
| python-whois | MIT | fallback WHOIS |
| tldextract | BSD-3 | podział domen (wbudowana lista PSL, bez pobierania) |
| mmh3 | MIT | hash favicony (wariant Shodana) |
| beautifulsoup4, lxml | MIT, BSD-3 | parsowanie HTML |
| cryptography | Apache-2.0 / BSD | parsowanie certyfikatów X.509 |
| Playwright (+ Chromium) | Apache-2.0 | opcjonalny render z nagrywaniem HAR |

| Usługa | Dostęp | Ograniczenia |
|---|---|---|
| `rdap.org` → serwery RDAP rejestrów | publiczny, bez klucza | limity per rejestr |
| serwery WHOIS (port 43) | publiczne | różne formaty, limity |
| RDAP RIR-ów (RIPE, ARIN, …) i DNS Team Cymru (przez ipwhois) | publiczne | limity zapytań |
| crt.sh (Certificate Transparency) | publiczny | wolny, zwraca 429 przy obciążeniu |
| resolver DNS systemu | lokalny | n/d |

Moduł nie wymaga żadnych kluczy API.
