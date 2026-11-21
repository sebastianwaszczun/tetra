# Moduł audio i wideo (`fakecollector.features.av`)

Reklamy wideo z tej samej kampanii mają często **ten sam głos** (lektor albo sklonowany głos celebryty),
**tę samą ścieżkę dźwiękową** albo **te same ujęcia**, nawet gdy film jest przycięty, przekodowany albo
nagrany z ekranu telefonu. Moduł zamienia każde nagranie na odciski, które pozwalają to wykryć, i daje
transkrypcję dla modułu tekstu.

## Co liczy dla każdego pliku

| Cecha | Jak | Po co |
|---|---|---|
| `transcript` | Whisper (`openai/whisper-small`, transformers; faster-whisper, jeśli jest) | treść reklamy idzie do modułu tekstu jako tekst z `origin = "transcript"` |
| `voice_emb` (192) | SpeechBrain ECAPA (`spkrec-ecapa-voxceleb`), średnia z 3-sekundowych kawałków mowy | ten sam lektor / ten sam sklonowany głos w różnych reklamach |
| `audio_fp_hashes`, `audio_fp_times` | pary najmocniejszych szczytów widma (metoda Shazama), numpy | ta sama ścieżka dźwiękowa, też przycięta, przekodowana, z szumem |
| `video_fp` | dHash 64 bity, 2 klatki na sekundę (ffmpeg + numpy) | te same ujęcia, też przycięte, pomniejszone, przekodowane |
| `onscreen_text` | OCR 6–8 klatek (tesseract pol+eng) | napisy w filmie („Zarabiaj 5000 zł”, adres strony) |
| metadane | ffprobe: czas, rozdzielczość, fps, kodeki, program kodujący, data | `screen_recording` (nagranie ekranu), słaba przesłanka „ten sam program” |
| `speech_seconds` | prosty detektor głośności | czy jest z czego liczyć głos i transkrypcję |
| `voice_matches` | porównanie z próbkami w `data/reference_people/<osoba>/*.wav/mp3/mp4` | „głos podobny do: Rafał Brzoska” – przesłanka, nie dowód |

## Skąd biorą się nagrania

Nagrania znajduje preprocessing, więc moduł widzi wszystko, co jest w materiale:
* pliki wideo i audio (mp4, mov, webm, mp3, m4a…), w tym nagrania ekranu telefonu,
* **wideo osadzone na stronie** zapisane jako strumień (lista `.m3u8` + kawałki `.ts`, np. w zapisie
  narzędziem page-capture w `DANE/14/ineptsolidoval_top/resources/media/`). Kawałki są sklejane w jeden
  plik mp4. Gdy zapisano tylko część strumienia, w brakach materiału pojawia się np.
  „wideo ze strony zapisane częściowo: 2 z 105 kawałków strumienia”.

W zbiorze DANE nagrania są w trzech przypadkach: DANE/5 (reklama z Biblioteki Reklam, 198 s),
DANE/14 (nagranie ekranu 249 s + 20 s wideo z lektorem ze strony Metabo RX) i DANE/47
(nagranie ekranu 166 s, bez dźwięku). BACKUP nie ma nagrań.

## Porównanie: `compare(a, b)`

```python
{"signals": {"same_audio": 1.0, "same_video": 0.21, "same_voice": 0.93, "voice_cosine": 0.72,
             "same_encoder": 0},
 "av_score": 1.0, "confidence": 1.0,
 "reasons": ["ta sama ścieżka dźwiękowa (… 829 pasujących fragmentów, przesunięcie -39.89 s)", ...]}
```

`av_score` = **najmocniejszy** z sygnałów dźwięk / obraz / głos, bo każdy z nich osobno wystarcza, żeby
uznać nagrania za powiązane. Transkrypcje porównuje moduł tekstu, tutaj nie są liczone drugi raz.

Głos (ECAPA, cosinus): porównujemy pary pojedynczych nagrań (nie uśredniamy różnych lektorów
z jednego materiału). W reklamach z muzyką w tle różni lektorzy dają do ok. 0,5 (DANE/5 ~ DANE/14: 0,47),
ten sam głos zwykle powyżej 0,75. `same_voice` = 0 przy 0,55 i 1 przy 0,80. Przesłanki: od 0,65
„podobny głos”, od 0,75 „ten sam lektor albo ten sam sklonowany głos”.

## Pomiary na danych (bez modeli)

Film z DANE/5 porównany z przerobionymi wersjami tego samego filmu i z innym nagraniem:

| Wersja | Dźwięk | Obraz | Wynik |
|---|---|---|---|
| fragment 50 s, 360×360, mocna kompresja, głośność ×0,5 | 1,0 (829 par) | 1,0 | 1,0 |
| sam dźwięk, fragment 60 s, MP3 64 kb/s, głośność ×1,5 | 1,0 (1079 par) | – | 1,0 |
| fragment 40 s, szum, wykadrowany i powiększony | 1,0 (646 par) | 0,21 | 1,0 |
| inne nagranie (DANE/14) | 0,0 (3 pary) | 0,0 | 0,0 |

Ograniczenie: odcisk obrazu liczy całą klatkę, więc film mocno wykadrowany albo pokazany jako mały
fragment ekranu telefonu nie zostanie rozpoznany po obrazie. Wtedy łączy go dźwięk, głos albo moduł obrazu.

## Jak uruchomić

```bash
# szybko, bez modeli: odciski dźwięku i obrazu, OCR, metadane
docker compose exec app python -m fakecollector.features.av data/DANE --no-asr --no-voice --compare

# pełna analiza (pierwsze uruchomienie pobiera modele do /models: Whisper ~1 GB, ECAPA ~80 MB)
docker compose exec app python -m fakecollector.features.av data/DANE/5 data/DANE/14 --compare
```

W API:
* `POST /analyze` – przy przesłanym wideo zwraca `av_features` (bez wektorów); transkrypcja trafia do
  `text_features`. Przełączniki `av` i `asr`.
* `POST /av/compare` – porównanie dwóch ścieżek z `data/` (do testów), np. `{"a": "data/DANE/5", "b": "data/DANE/14"}`.

W kodzie (potok):

```python
from fakecollector.ingest import ingest
from fakecollector.features import av, text
inc = ingest("data/DANE/5")
feats = av.extract(inc)              # None, gdy materiał nie ma nagrań
av.attach_to_incident(inc, feats)    # transkrypcja i napisy -> inc.text (gdy brak lepszego źródła)
text_feats = text.extract(inc)
```

Ustawienia (zmienne środowiskowe): `WHISPER_MODEL` (np. `openai/whisper-base` – szybszy, gorszy po polsku),
`WHISPER_LANGUAGE` (np. `pl`; domyślnie wykrywany), `AV_MAX_SECONDS`, `AV_MAX_ASR_SECONDS`, `REFERENCE_DIR`.

## Co trafia do rekordu materiału

Słownik z `extract()`:
* `items` – lista plików z cechami wyżej,
* `transcript`, `onscreen_text`,
* `voice_emb` (192, do pgvector),
* `voice_matches`, `duration_total`, `speech_seconds`, `screen_recording`,
* `confidence`, `missing`, `errors`.

Pewność jest niższa dla nagrań ekranu (×0,8), przy mniej niż 5 s mowy (×0,7) i przy błędach (×0,8).

## Wybór tekstu po transkrypcji

Preprocessing wybiera tekst w kolejności: tekst użytkownika + strona (HTML > page_text > HAR) >
Biblioteka Reklam > metadane > **transkrypcja** > OCR. Źródło krótsze niż 15 słów (np. sam tytuł reklamy
„Czytać”) ustępuje następnemu. Dla reklamy wideo bez strony moduł tekstu dostaje więc transkrypcję.
