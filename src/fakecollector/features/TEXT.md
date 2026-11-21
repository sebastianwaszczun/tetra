# Moduł tekstu

Moduł wyciąga z tekstu materiału cechy narracji i porównuje dwa materiały po tekście. Rozpoznaje ten sam tekst na różnych domenach oraz ten sam szablon artykułu z inną osobą, marką albo kwotą. Każde porównanie zwraca przesłanki w zdaniach dla człowieka.

Plik: `src/fakecollector/features/text.py`
Miejsce w systemie: moduł cech między preprocessingiem a porównaniem i grupowaniem (zob. schemat „Architektura FakeCollector”).

## Pliki

| Plik | Zawartość |
|---|---|
| `text.py` | cały moduł: ekstrakcja tekstu z HTML, cechy, porównanie, CLI |
| `lexicons/osoby.txt` | osoby, których wizerunek kradną reklamy, z wyrażeniem regularnym obejmującym odmianę nazwiska |
| `lexicons/marki.txt` | podszywane marki: media, banki, instytucje, sklepy |
| `lexicons/stopwords_pl.txt` | stop-słowa polskie i angielskie oraz słowa z widgetów komentarzy („Lubię to · Odpowiedz”) |

## Wejście

Moduł przyjmuje tekst i informację o jego źródle. Źródło wpływa na pewność porównań:

| `origin` | Skąd tekst | Waga pewności |
|---|---|---|
| `html` | treść strony wyciągnięta z HTML | 1,0 |
| `input` | tekst wklejony przez użytkownika | 1,0 |
| `adlib` | treść reklamy z biblioteki reklam | 1,0 |
| `json` | metadane (np. `.info.json`) | 0,9 |
| `transcript` | transkrypcja wideo | 0,8 |
| `ocr` | OCR zrzutu ekranu | 0,6 razy jakość OCR |

Tekst krótszy niż 15 słów dostaje proporcjonalnie niższą pewność.

## Wyjście: cechy (`analyze_text`)

| Pole | Opis |
|---|---|
| `origin`, `confidence` | źródło tekstu i pewność porównań (0–1) |
| `n_words`, `language` | liczba słów, język (`pl`, `en`, `other`) |
| `title` | tytuł strony |
| `keywords` | 15 najczęstszych słów i fraz dwuwyrazowych (tytuł liczy się trzy razy) |
| `persons` | osoby ze słownika i liczba wystąpień |
| `brands` | podszywane marki i liczba wystąpień |
| `money`, `percents` | kwoty i procenty z tekstu |
| `urls`, `emails`, `phones` | linki i domeny, e-maile, telefony (ostatnie 9 cyfr) |
| `persuasion` | liczba znaczników w 6 grupach: `profit`, `urgency`, `authority`, `secrecy`, `sensational`, `social_proof` |
| `narrative` | kategoria narracji, jej opis i wyniki wszystkich kategorii |
| `skeleton_preview` | początek szkieletu tekstu (do podglądu w GUI) |
| `minhash`, `skeleton_minhash` | podpisy MinHash (128 liczb) tekstu i szkieletu |
| `text_emb`, `skeleton_emb` | embeddingi tekstu i szkieletu (384 wymiary, znormalizowane) |
| `text_sha256` | hash znormalizowanego tekstu do wykrywania identycznych kopii |
| `missing` | czego zabrakło, np. „brak tekstu”, „embedding tekstu niedostępny” |

Pole równe `None` oznacza, że cechy nie dało się policzyć. To nie jest błąd: porównanie po prostu pomija ten sygnał.

## Jak działa

1. **Ekstrakcja z HTML** (`extract_text_from_html`). Tytuł, `meta description`, tagi `og:*`, nagłówki `h1`/`h2`, linki wychodzące i komentarz `saved from url`. Treść wyciąga `trafilatura`, jeśli jest zainstalowana, a w przeciwnym razie BeautifulSoup po usunięciu skryptów, stylów i formularzy.
2. **Normalizacja.** Małe litery, ujednolicone cudzysłowy i myślniki, pojedyncze spacje. Do MinHash dodatkowo bez polskich znaków i bez stop-słów.
3. **Encje.** Osoby i marki ze słowników, kwoty, procenty, linki, e-maile i telefony z wyrażeń regularnych.
4. **Szkielet tekstu** (`skeleton`). Elementy, które oszuści zmieniają między odcinkami kampanii, są podmieniane na znaczniki: `<OSOBA>`, `<MARKA>`, `<KWOTA>`, `<PROCENT>`, `<DATA>`, `<URL>`, `<EMAIL>`, `<TELEFON>`, `<LICZBA>`. Ten sam artykuł o Stanowskim i o Sekielskim daje wtedy prawie ten sam szkielet.
5. **MinHash.** Shingle po 5 słów, 128 permutacji, stałe ziarno. Format zgodny z `hashvalues` w `datasketch.MinHash`, więc moduł porównania może użyć `MinHashLSH` bez przeliczania.
6. **Embeddingi.** Tekst dzielony na fragmenty po 180 słów (model obcina dłuższe wejście), najwyżej 8 fragmentów od początku, średnia z embeddingów znormalizowana do długości 1.
7. **Narracja.** Połowa wyniku z podobieństwa embeddingu do opisów kategorii (zero-shot), połowa ze słów-wskazówek. Działa też bez modelu. Kategorie: `fake_news_investment`, `fake_news_scandal`, `health_miracle`, `shop_scam`, `gambling`, `other`.
8. **Znaczniki perswazji.** Liczba fraz z 6 grup typowych dla oszustw.

## Porównanie dwóch materiałów (`compare`)

`compare(a, b)` przyjmuje dwa wyniki `analyze_text` (obiekty albo słowniki) i zwraca:

| Pole | Opis |
|---|---|
| `signals` | wartości poszczególnych sygnałów (tabela niżej) |
| `shared_persons`, `shared_brands`, `shared_contacts` | wspólne osoby, marki, telefony i e-maile |
| `text_score` | łączny wynik tekstowy 0–1 |
| `confidence` | mniejsza z pewności obu materiałów |
| `reasons` | przesłanki w zdaniach, np. „87% wspólnych fragmentów tekstu” |

| Sygnał | Jak liczony | Waga w `text_score` |
|---|---|---|
| `exact_duplicate` | ten sam `text_sha256` | wymusza wynik 1,0 |
| `text_minhash` | Jaccard z MinHash tekstu | 0,30 |
| `skeleton_minhash` | Jaccard z MinHash szkieletu | 0,25 |
| `skeleton_cosine` | kosinus embeddingów szkieletu | 0,20 |
| `text_cosine` | kosinus embeddingów tekstu | 0,15 |
| `keywords` | Jaccard słów kluczowych | 0,05 |
| `same_narrative` | ta sama kategoria narracji (bez `other`) | 0,05 |

`text_score` to średnia ważona tylko z sygnałów, które dało się policzyć. Ostateczne wagi łączenia z innymi modułami (obraz, OSINT, głos) ustala moduł `similarity/`. Ten wynik jest wkładem samego tekstu.

**Wspólna osoba sama w sobie nie łączy kampanii.** Wiele niezależnych kampanii używa tych samych celebrytów, dlatego osoby trafiają do przesłanek, ale nie do `text_score`.

## Użycie w kodzie

```python
from fakecollector.features.text import analyze_text, compare, extract, extract_text_from_html

text, meta = extract_text_from_html(html)
a = analyze_text(text, origin="html", title=meta["title"])
b = analyze_text(ocr_text, origin="ocr")
wynik = compare(a, b)
print(wynik["text_score"], wynik["reasons"])
```

Integracja z potokiem: preprocessing przekazuje obiekt (albo słownik) z polami `text`, `text_origin` i `title`:

```python
cechy = extract(incident)   # słownik cech albo None, gdy materiał nie ma tekstu
```

## Uruchomienie z linii poleceń

W kontenerze `app`, z folderu projektu:

```powershell
# opis każdej strony i 40 najbardziej podobnych par
docker compose exec app python -m fakecollector.features.text data/BACKUP data/DANE --compare --top 40

# zapis cech do pliku i porównania do drugiego pliku (oba w folderze data/)
docker compose exec app python -m fakecollector.features.text data/BACKUP data/DANE --compare --top 40 --json data/wyniki_tekst.json > data/porownanie.txt

# szybki test bez modelu embeddingów
docker compose exec app python -m fakecollector.features.text data/BACKUP --compare --no-embeddings
```

Ścieżką może być plik, folder jednego przypadku (`data/BACKUP/p1`) albo cała paczka (`data/BACKUP`), wtedy każdy podfolder to osobny przypadek. Wczytywanie folderów w CLI jest tymczasowe, do testów modułu. W gotowym systemie pliki czyta preprocessing.

## Konfiguracja

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `TEXT_EMB_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | model embeddingów; zmiana wymiaru wymaga zmiany kolumny `text_emb` w bazie |
| `TEXT_EMBEDDINGS` | `1` | `0` wyłącza embeddingi (moduł działa dalej na MinHash i słownikach) |

Przy modelu z rodziny E5 (np. `intfloat/multilingual-e5-base`, 768 wymiarów) moduł sam dodaje wymagany prefiks `passage: `.

## Rozszerzanie słowników

Jedna linia to jedna pozycja: `Nazwa | wyrażenie regularne`. Wyrażenie pisze się małymi literami i powinno obejmować odmianę:

```
Rafał Brzoska | brzosk\w*
Robert Lewandowski | robert\w*\s+lewandowsk\w*
```

Popularne nazwiska (Lewandowski, Prokop, Stoch) wymagają imienia, bo fałszywe komentarze pod artykułami podpisane są zwykłymi nazwiskami. Osoby w `osoby.txt` powinny pokrywać się z bazą referencyjną modułu obrazu, żeby oba moduły mówiły o tych samych ludziach.

## Wyniki na danych organizatora

Przebieg na `data/BACKUP` i `data/DANE` z embeddingami:

| Grupa | Materiały | Wynik tekstowy |
|---|---|---|
| Kampania „Brzoska”, fałszywa Gazeta.pl | BACKUP p1, p2, p7, p15, DANE/1, DANE/2 | 0,90–1,00 |
| „Trudna odpowiedź” | BACKUP p8, p11 | 0,92 |
| Szablon „brutalnej napaści” (Stanowski, Sekielski) | BACKUP p3, p4 | ok. 0,84 |
| Strony niezwiązane, np. Brzoska i Stanowski | — | ok. 0,10 |

Nowe warianty kampanii „Brzoska” z paczki DANE zostały połączone ze starymi z paczki BACKUP, mimo że stoją na innych domenach.

## Znane ograniczenia i następne kroki

- **Strony błędów.** Gdy w folderze jest zapis strony z błędem serwera (np. BACKUP/p1: „502: Bad gateway” z Cloudflare), moduł bierze z niej tytuł i słowa `cloudflare`, `gateway`. Do zrobienia: pomijanie stron błędów i wybór tytułu z właściwego artykułu.
- **Nazwy platform i produktów.** Fikcyjne nazwy „platform inwestycyjnych” („Świtowin Zyskarnia”, „Steryga Dochodnica”, „Orzeł Fundaris”) i suplementów (Kardivix, RelieFlex, Uromexil Forte) to bardzo mocny sygnał łączący kampanie. „Świtowin Zyskarnia” występuje i w kampanii „Brzoska”, i w artykule o Stanowskim. Na razie łapią je tylko słowa kluczowe. Do zrobienia: osobna cecha `product_names` i twardy sygnał w porównaniu.
- **Komentarze pod artykułami.** Fałszywe komentarze wliczają się do tekstu i słów kluczowych. Można je wycinać albo analizować osobno.
- **Długie teksty.** Embedding obejmuje najwyżej pierwsze 8 fragmentów po 180 słów, czyli około 1440 słów. MinHash obejmuje cały tekst.
- **Słowniki są ręczne.** Nowe osoby i marki z ukrytego zbioru testowego trzeba dopisać albo uzupełnić rozpoznawanie nazw własnych (np. model NER dla polskiego).

## Zależności i licencje

| Biblioteka lub model | Do czego | Licencja |
|---|---|---|
| `beautifulsoup4`, `lxml` | ekstrakcja tekstu z HTML | MIT, BSD |
| `numpy` | MinHash, wektory | BSD |
| `sentence-transformers` | embeddingi | Apache 2.0 |
| `paraphrase-multilingual-MiniLM-L12-v2` | model embeddingów (384 wymiary) | Apache 2.0 |
| `trafilatura` (opcjonalnie) | lepsze wycinanie treści artykułu | Apache 2.0 |

Licencje warto potwierdzić na stronach projektów przed oddaniem.
