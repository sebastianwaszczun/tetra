"""Moduł audio i wideo: odciski głosu, dźwięku i obrazu z filmów oraz transkrypcja.

Dla każdego pliku wideo/audio liczy:
  * transkrypcję mowy (Whisper)                      -> tekst idzie do modułu tekstu (origin "transcript")
  * odcisk głosu (SpeechBrain ECAPA, 192 liczby)     -> ten sam lektor albo ten sam sklonowany głos
  * odcisk dźwięku (pary szczytów widma, jak Shazam) -> ta sama ścieżka dźwiękowa, też przycięta
  * odcisk obrazu (dHash 2 klatek na sekundę)        -> ten sam film, też przycięty lub w nagraniu ekranu
  * tekst na ekranie (OCR kilku klatek)              -> napisy w reklamie („Zarabiaj 5000 zł”)
  * metadane pliku (ffprobe)                         -> rozdzielczość, kodek, program kodujący

Użycie w potoku:
    feats = av.extract(incident)             # słownik do rekordu materiału (albo None)
    av.attach_to_incident(incident, feats)   # transkrypcja i napisy -> tekst dla modułu tekstu
    av.compare(feats_a, feats_b)             # sygnały, wynik, przesłanki

CLI:
    python -m fakecollector.features.av data/DANE --compare
    python -m fakecollector.features.av film.mp4 --no-asr --json data/av.json
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

log = logging.getLogger(__name__)

SR = 16000
MAX_SECONDS = float(os.environ.get("AV_MAX_SECONDS", "600"))          # dłuższe pliki przycinamy
MAX_ASR_SECONDS = float(os.environ.get("AV_MAX_ASR_SECONDS", "300"))  # transkrypcja jest najwolniejsza
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "openai/whisper-small")
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE") or None         # None = wykryj sam
VOICE_MODEL = "speechbrain/spkrec-ecapa-voxceleb"
MODELS_DIR = Path(os.environ.get("SPEECHBRAIN_HOME", "/models/speechbrain"))
REFERENCE_DIR = Path(os.environ.get("REFERENCE_DIR", "data/reference_people"))

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".3gp"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".aac", ".flac"}

SCREEN_RECORDING_RE = re.compile(
    r"screen.?recording|nagr(ywanie|anie).?ekranu|rpreplay|screen.?record|zrzut.?ekranu.?wideo|screencast", re.I)

# Typowe „halucynacje” Whispera na muzyce i ciszy
HALLUCINATION_RE = re.compile(
    r"[^.!?\n]*(amara\.org|napisy (wykonane|stworzone|przygotowane)|dzięki za (obejrzenie|uwagę)|"
    r"dziękuję za (obejrzenie|uwagę)|subskrybuj|thanks for watching|subtitles by)[^.!?\n]*[.!?]?", re.I)


# ============================================================================ wynik

@dataclass
class MediaFeatures:
    """Cechy jednego pliku wideo albo audio."""
    path: str
    name: str
    kind: str                                   # video / audio
    screen_recording: bool = False              # nagranie ekranu telefonu (UI zamiast samej reklamy)
    duration: float = 0.0
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    encoder: str | None = None                  # np. „Lavf58.76.100”, „CapCut” – program, który zapisał plik
    creation_time: str | None = None
    has_audio: bool = False
    speech_seconds: float = 0.0
    speech_ratio: float = 0.0
    transcript: str = ""
    transcript_model: str | None = None
    onscreen_text: str = ""                     # OCR kilku klatek
    voice_emb: list[float] | None = None        # 192 liczby, znormalizowane
    voice_windows: int = 0                      # z ilu 3-sekundowych kawałków mowy liczony
    audio_fp_hashes: list[int] = field(default_factory=list)
    audio_fp_times: list[int] = field(default_factory=list)
    video_fp: list[str] = field(default_factory=list)   # dHash klatek (hex, 16 znaków)
    confidence: float = 1.0
    errors: list[str] = field(default_factory=list)

    VECTOR_FIELDS = ("voice_emb", "audio_fp_hashes", "audio_fp_times", "video_fp")

    def to_dict(self, with_vectors: bool = True) -> dict[str, Any]:
        d = asdict(self)
        if not with_vectors:
            for k in self.VECTOR_FIELDS:
                v = d.get(k)
                d[k] = f"<{len(v)} liczb>" if v else None
        return d


# ============================================================================ dekodowanie i metadane

def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def probe(path: str | Path) -> dict[str, Any]:
    """Metadane pliku z ffprobe."""
    r = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], 60)
    if r.returncode != 0:
        return {"error": r.stderr.decode("utf-8", "replace").strip()[:300]}
    info = json.loads(r.stdout or b"{}")
    fmt = info.get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    out: dict[str, Any] = {"duration": float(fmt.get("duration") or 0.0)}
    for s in info.get("streams", []):
        stags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
        if s.get("codec_type") == "video" and "width" not in out:
            out.update(width=s.get("width"), height=s.get("height"), video_codec=s.get("codec_name"))
            num, _, den = (s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1").partition("/")
            try:
                out["fps"] = round(float(num) / float(den or 1), 2) if float(den or 1) else None
            except ValueError:
                out["fps"] = None
            rot = stags.get("rotate") or next((d.get("rotation") for d in s.get("side_data_list", [])
                                                if "rotation" in d), None)
            if rot and abs(int(float(rot))) in (90, 270):
                out["width"], out["height"] = out["height"], out["width"]
            tags.setdefault("encoder", stags.get("encoder"))
        elif s.get("codec_type") == "audio" and "audio_codec" not in out:
            out["audio_codec"] = s.get("codec_name")
    out["has_audio"] = "audio_codec" in out
    out["encoder"] = tags.get("encoder") or tags.get("com.apple.quicktime.software") or tags.get("software")
    out["creation_time"] = tags.get("creation_time") or tags.get("com.apple.quicktime.creationdate")
    return out


def load_audio(path: str | Path, max_seconds: float = MAX_SECONDS) -> np.ndarray:
    """Dowolny plik (mp4, mp3, wav…) -> mono 16 kHz float32 w zakresie -1..1."""
    r = _run(["ffmpeg", "-v", "error", "-i", str(path), "-t", str(max_seconds), "-vn",
              "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip()[:300] or "ffmpeg: błąd dekodowania")
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


# ============================================================================ mowa (VAD)

def speech_segments(y: np.ndarray, min_len: float = 0.4, max_gap: float = 0.3) -> list[tuple[float, float]]:
    """Fragmenty z głosem na podstawie głośności (bez modelu).

    Próg = cisza tła + 10 dB. Muzyka też przejdzie przez ten filtr – dlatego odcisk głosu liczymy
    z wielu kawałków i uśredniamy, a Whisper sam ignoruje fragmenty bez mowy.
    """
    win, hop = int(0.03 * SR), int(0.01 * SR)
    if len(y) < win:
        return []
    cs = np.concatenate([[0.0], np.cumsum(y.astype(np.float64) ** 2)])
    starts = np.arange(0, len(y) - win + 1, hop)
    energy = (cs[starts + win] - cs[starts]) / win
    db = 10 * np.log10(energy + 1e-10)
    if db.max() < -55:  # cisza
        return []
    thr = max(np.percentile(db, 10) + 10, -50.0)
    active = db > thr
    segs: list[list[float]] = []
    for i in np.flatnonzero(np.diff(np.concatenate([[0], active.astype(np.int8), [0]])) != 0).reshape(-1, 2):
        s, e = i[0] * hop / SR, (i[1] - 1) * hop / SR + win / SR
        if segs and s - segs[-1][1] < max_gap:
            segs[-1][1] = e
        else:
            segs.append([s, e])
    return [(round(s, 2), round(e, 2)) for s, e in segs if e - s >= min_len]


# ============================================================================ odcisk dźwięku

FP_NFFT, FP_HOP = 1024, 256          # 64 ms okno, 16 ms krok (mniejszy krok = odporność na przesunięcie po kompresji)
FP_FMIN, FP_FMAX = 10, 300           # pasmo ok. 150 Hz – 4,7 kHz (mowa i melodia)
FP_PEAKS_PER_SEC = 15
FP_FANOUT, FP_MAX_DT, FP_MAX_DF = 3, 63, 60


def _max_filter_1d(a: np.ndarray, size: int, axis: int) -> np.ndarray:
    pad = size // 2
    widths = [(0, 0)] * a.ndim
    widths[axis] = (pad, pad)
    p = np.pad(a, widths, mode="constant", constant_values=-np.inf)
    v = np.lib.stride_tricks.sliding_window_view(p, size, axis=axis)
    return v.max(axis=-1)


def audio_fingerprint(y: np.ndarray, max_seconds: float = 300) -> tuple[list[int], list[int]]:
    """Odcisk ścieżki dźwiękowej: pary najmocniejszych szczytów widma (metoda znana z Shazama).

    Odporny na ponowną kompresję, zmianę głośności i przycięcie. Zwraca (hashe, czasy kotwic w ramkach).
    """
    y = y[: int(max_seconds * SR)]
    if len(y) < FP_NFFT * 4:
        return [], []
    frames = np.lib.stride_tricks.sliding_window_view(y, FP_NFFT)[::FP_HOP] * np.hanning(FP_NFFT).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames, axis=1))[:, FP_FMIN:FP_FMAX]
    S = np.log(spec + 1e-6).astype(np.float32)
    local = _max_filter_1d(_max_filter_1d(S, 11, 0), 21, 1)
    thr = np.percentile(S, 85)
    t_idx, f_idx = np.nonzero((S == local) & (S > thr))
    if len(t_idx) == 0:
        return [], []
    mags = S[t_idx, f_idx]
    # gęstość: najwyżej FP_PEAKS_PER_SEC najmocniejszych szczytów na sekundę
    frames_per_sec = SR / FP_HOP
    bucket = (t_idx / frames_per_sec).astype(int)
    order = np.lexsort((-mags, bucket))
    keep, last, count = [], -1, 0
    for i in order:
        if bucket[i] != last:
            last, count = bucket[i], 0
        if count < FP_PEAKS_PER_SEC:
            keep.append(i)
            count += 1
    keep = np.array(sorted(keep, key=lambda i: (t_idx[i], f_idx[i])))
    pt, pf = t_idx[keep], f_idx[keep]
    hashes, times = [], []
    n = len(pt)
    for i in range(n):
        made = 0
        for j in range(i + 1, n):
            dt = pt[j] - pt[i]
            if dt > FP_MAX_DT:
                break
            df = int(pf[j]) - int(pf[i])
            if dt == 0 or abs(df) > FP_MAX_DF:
                continue
            hashes.append(int(pf[i]) * 16384 + (df + 128) * 64 + int(dt))
            times.append(int(pt[i]))
            made += 1
            if made >= FP_FANOUT:
                break
    return hashes, times


def compare_audio_fp(ha: list[int], ta: list[int], hb: list[int], tb: list[int]) -> dict[str, Any] | None:
    """Ile par szczytów pasuje przy tym samym przesunięciu w czasie. 1.0 = ta sama ścieżka."""
    if len(ha) < 50 or len(hb) < 50:
        return None
    ha_, ta_ = np.asarray(ha, np.int64), np.asarray(ta, np.int64)
    hb_, tb_ = np.asarray(hb, np.int64), np.asarray(tb, np.int64)
    order = np.argsort(hb_)
    hb_s, tb_s = hb_[order], tb_[order]
    lo = np.searchsorted(hb_s, ha_, "left")
    hi = np.searchsorted(hb_s, ha_, "right")
    counts = hi - lo
    if counts.sum() == 0:
        return {"score": 0.0, "matches": 0, "offset_s": None}
    idx_a = np.repeat(np.arange(len(ha_)), counts)
    idx_b = np.concatenate([np.arange(l, h) for l, h in zip(lo[counts > 0], hi[counts > 0])])
    offsets = tb_s[idx_b] - ta_[idx_a]
    vals, cnt = np.unique(offsets, return_counts=True)
    best = int(cnt.max())
    # dopuszczamy ±1 ramkę rozjazdu
    k = int(np.argmax(cnt))
    near = int(cnt[np.abs(vals - vals[k]) <= 1].sum())
    shorter = min(len(ha_), len(hb_))
    # pomiar na danych: ta sama ścieżka po przycięciu, kompresji MP3/AAC, zmianie głośności i z szumem
    # = 31–45% pasujących par; różne nagrania = 0,0% (2–4 przypadkowe trafienia). 30% = pewne.
    score = float(min(1.0, near / (0.30 * shorter)))
    if near < 12:   # kilka przypadkowych trafień to nie dopasowanie
        score = 0.0
    return {"score": round(score, 3), "matches": near, "offset_s": round(float(vals[k]) * FP_HOP / SR, 2)}


# ============================================================================ odcisk obrazu

VIDEO_FP_FPS = 2
MAX_VIDEO_FRAMES = 600


def video_fingerprint(path: str | Path, max_seconds: float = MAX_SECONDS) -> list[str]:
    """dHash (64 bity) dla 2 klatek na sekundę. Klatki jednolite (czarne, białe) są pomijane."""
    r = _run(["ffmpeg", "-v", "error", "-i", str(path), "-t", str(max_seconds), "-an",
              "-vf", f"fps={VIDEO_FP_FPS},scale=9:8:flags=area,format=gray",
              "-frames:v", str(MAX_VIDEO_FRAMES), "-f", "rawvideo", "pipe:1"])
    if r.returncode != 0 or not r.stdout:
        return []
    px = np.frombuffer(r.stdout, np.uint8)
    px = px[: len(px) // 72 * 72].reshape(-1, 8, 9).astype(np.int16)
    px = px[px.std(axis=(1, 2)) >= 4]
    bits = (px[:, :, 1:] > px[:, :, :-1]).reshape(len(px), 64)
    vals = np.packbits(bits, axis=1).view(">u8").ravel()
    return [f"{int(v):016x}" for v in vals]


def _hex_to_u64(h: list[str]) -> np.ndarray:
    return np.array([int(x, 16) for x in h], dtype=np.uint64)


def compare_video_fp(a: list[str], b: list[str], max_dist: int = 10) -> dict[str, Any] | None:
    """Jaka część klatek jednego filmu ma prawie identyczną klatkę w drugim (kolejność bez znaczenia,
    więc działa też dla przyciętych i przemontowanych wersji oraz reklamy w nagraniu ekranu)."""
    if len(a) < 4 or len(b) < 4:
        return None
    ua, ub = np.unique(_hex_to_u64(a)), np.unique(_hex_to_u64(b))
    if len(ua) < 3 or len(ub) < 3:
        return None
    x = ua[:, None] ^ ub[None, :]
    dist = np.unpackbits(x.view(np.uint8).reshape(len(ua), len(ub), 8), axis=-1).sum(-1)
    ma = (dist.min(axis=1) <= max_dist)
    mb = (dist.min(axis=0) <= max_dist)
    frac_a, frac_b = float(ma.mean()), float(mb.mean())
    score = max(frac_a, frac_b) if min(ma.sum(), mb.sum()) >= 3 else 0.0
    return {"score": round(score, 3), "frames_a": round(frac_a, 3), "frames_b": round(frac_b, 3)}


# ============================================================================ modele (ładowane raz)

_lock = threading.Lock()
_voice_model: Any = None
_asr: Any = None


def _quiet_speechbrain() -> None:
    """SpeechBrain wypisuje kilkadziesiąt linijek o pobieraniu modelu – zostawiamy tylko ostrzeżenia."""
    for name in list(logging.root.manager.loggerDict) + ["speechbrain"]:
        if name.startswith("speechbrain"):
            logging.getLogger(name).setLevel(logging.WARNING)


def _get_voice_model():
    global _voice_model
    with _lock:
        if _voice_model is None:
            _quiet_speechbrain()
            try:
                from speechbrain.inference.speaker import EncoderClassifier
            except ImportError:  # speechbrain < 1.0
                from speechbrain.pretrained import EncoderClassifier  # type: ignore
            _quiet_speechbrain()
            _voice_model = EncoderClassifier.from_hparams(
                source=VOICE_MODEL, savedir=str(MODELS_DIR / "spkrec-ecapa-voxceleb"), run_opts={"device": "cpu"})
    return _voice_model


def voice_embedding(y: np.ndarray, segments: list[tuple[float, float]], window: float = 3.0,
                    max_windows: int = 40) -> tuple[list[float] | None, int]:
    """Odcisk głosu: średnia z embeddingów ECAPA liczonych na 3-sekundowych kawałkach mowy."""
    speech = np.concatenate([y[int(s * SR): int(e * SR)] for s, e in segments]) if segments else y[:0]
    w = int(window * SR)
    if len(speech) < int(1.5 * SR):
        return None, 0
    n = max(1, min(max_windows, len(speech) // w))
    if len(speech) < w:
        speech = np.tile(speech, int(np.ceil(w / len(speech))))[:w]
        n = 1
    step = max(w, (len(speech) - w) // max(1, n - 1)) if n > 1 else w
    chunks = np.stack([speech[i * step: i * step + w] for i in range(n) if i * step + w <= len(speech)])
    import torch
    model = _get_voice_model()
    with torch.no_grad():
        emb = model.encode_batch(torch.from_numpy(chunks.astype(np.float32))).squeeze(1).cpu().numpy()
    emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
    mean = emb.mean(axis=0)
    mean = mean / (np.linalg.norm(mean) + 1e-9)
    return [round(float(v), 6) for v in mean], len(chunks)


def _get_asr():
    global _asr
    with _lock:
        if _asr is None:
            try:   # szybsza wersja, jeśli ktoś ją doinstaluje
                from faster_whisper import WhisperModel
                size = WHISPER_MODEL.split("whisper-")[-1]
                _asr = ("faster-whisper:" + size,
                        WhisperModel(size, device="cpu", compute_type="int8",
                                     download_root=os.environ.get("HF_HOME", "/models/hf")))
            except ImportError:
                import warnings
                from transformers import logging as hf_logging, pipeline
                hf_logging.set_verbosity_error()
                warnings.filterwarnings("ignore", category=FutureWarning, module="transformers")
                _asr = (WHISPER_MODEL, pipeline("automatic-speech-recognition", model=WHISPER_MODEL,
                                                device=-1, chunk_length_s=30))
    return _asr


def transcribe(y: np.ndarray) -> tuple[str, str]:
    """Transkrypcja (język wykrywany automatycznie albo z WHISPER_LANGUAGE)."""
    y = y[: int(MAX_ASR_SECONDS * SR)]
    name, model = _get_asr()
    if name.startswith("faster-whisper"):
        segs, _info = model.transcribe(y, language=WHISPER_LANGUAGE, vad_filter=True, beam_size=1)
        text = " ".join(s.text.strip() for s in segs)
    else:
        kwargs: dict[str, Any] = {"task": "transcribe"}
        if WHISPER_LANGUAGE:
            kwargs["language"] = WHISPER_LANGUAGE
        out = model({"raw": y, "sampling_rate": SR}, generate_kwargs=kwargs, batch_size=4)
        text = out["text"] if isinstance(out, dict) else " ".join(o["text"] for o in out)
    text = HALLUCINATION_RE.sub(" ", text)
    text = re.sub(r"\b(\w+)( \1\b){3,}", r"\1", text)      # „tak tak tak tak” -> „tak”
    return " ".join(text.split()), name


# ============================================================================ tekst na ekranie

def onscreen_text(path: str | Path, duration: float, n: int = 6, workdir: Path | None = None) -> str:
    """OCR kilku klatek rozłożonych równo w czasie. Powtarzające się linie liczymy raz."""
    import tempfile
    from fakecollector.ingest.screenshot import ocr_image
    lines: list[str] = []
    seen: set[str] = set()
    with tempfile.TemporaryDirectory(dir=workdir) as td:
        for i in range(n):
            t = duration * (i + 0.5) / n if duration else i
            frame = Path(td) / f"f{i}.png"
            r = _run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                      "-vf", "scale='min(1280,iw)':-2", str(frame)], 120)
            if r.returncode != 0 or not frame.exists():
                continue
            try:
                text = ocr_image(frame)
            except Exception as exc:  # brak tesseracta itp.
                log.warning("OCR klatki: %s", exc)
                return ""
            for line in text.splitlines():
                key = re.sub(r"\W+", "", line.lower())
                if len(key) >= 4 and key not in seen:
                    seen.add(key)
                    lines.append(line.strip())
    return "\n".join(lines)


# ============================================================================ analiza pliku

def analyze_media(path: str | Path, audio_path: str | Path | None = None, *, asr: bool = True,
                  voice: bool = True, ocr: bool = True, fingerprints: bool = True) -> MediaFeatures:
    """Wszystkie cechy jednego pliku. Błędy jednej części nie przerywają pozostałych."""
    path = Path(path)
    kind = "audio" if path.suffix.lower() in AUDIO_EXT else "video"
    mf = MediaFeatures(path=str(path), name=path.name, kind=kind,
                       screen_recording=bool(SCREEN_RECORDING_RE.search(path.name)))
    info = probe(path)
    if info.get("error"):
        mf.errors.append(f"ffprobe: {info['error']}")
        mf.confidence = 0.0
        return mf
    for k in ("duration", "width", "height", "fps", "video_codec", "audio_codec", "encoder",
              "creation_time", "has_audio"):
        setattr(mf, k, info.get(k))
    mf.duration = round(float(mf.duration or 0), 2)
    if kind == "audio":
        mf.has_audio = True

    if kind == "video" and fingerprints:
        try:
            mf.video_fp = video_fingerprint(path)
        except Exception as exc:
            mf.errors.append(f"odcisk obrazu: {exc}")
    if kind == "video" and ocr:
        try:
            mf.onscreen_text = onscreen_text(path, mf.duration, n=8 if mf.screen_recording else 6)
        except Exception as exc:
            mf.errors.append(f"OCR klatek: {exc}")

    if mf.has_audio:
        try:
            y = load_audio(audio_path or path)
        except Exception as exc:
            mf.errors.append(f"dźwięk: {exc}")
            y = np.zeros(0, np.float32)
        segs = speech_segments(y)
        mf.speech_seconds = round(sum(e - s for s, e in segs), 2)
        mf.speech_ratio = round(mf.speech_seconds / (len(y) / SR), 3) if len(y) else 0.0
        if fingerprints and len(y):
            try:
                mf.audio_fp_hashes, mf.audio_fp_times = audio_fingerprint(y)
            except Exception as exc:
                mf.errors.append(f"odcisk dźwięku: {exc}")
        if voice and mf.speech_seconds >= 1.5:
            try:
                mf.voice_emb, mf.voice_windows = voice_embedding(y, segs)
            except Exception as exc:
                mf.errors.append(f"odcisk głosu (SpeechBrain): {exc.__class__.__name__}: {exc}")
        if asr and mf.speech_seconds >= 1.0:
            try:
                mf.transcript, mf.transcript_model = transcribe(y)
            except Exception as exc:
                mf.errors.append(f"transkrypcja (Whisper): {exc.__class__.__name__}: {exc}")

    # pewność: nagranie ekranu zawiera interfejs telefonu, mało mowy = słaby odcisk głosu
    conf = 1.0
    if mf.screen_recording:
        conf *= 0.8
    if mf.voice_emb and mf.speech_seconds < 5:
        conf *= 0.7
    if mf.errors:
        conf *= 0.8
    mf.confidence = round(conf, 2)
    return mf


# ============================================================================ wiele plików = jeden materiał

def _combine(items: list[MediaFeatures], references: dict[str, np.ndarray] | None = None) -> dict[str, Any]:
    voices = [np.asarray(m.voice_emb) * max(1, m.voice_windows) for m in items if m.voice_emb]
    voice = None
    if voices:
        v = np.sum(voices, axis=0)
        voice = [round(float(x), 6) for x in v / (np.linalg.norm(v) + 1e-9)]
    missing = []
    if not items:
        missing.append("brak plików wideo i audio")
    elif not any(m.has_audio for m in items):
        missing.append("filmy bez ścieżki dźwiękowej")
    elif not voice:
        missing.append("brak odcisku głosu (za mało mowy albo brak modelu)")
    if items and not any(m.transcript for m in items):
        missing.append("brak transkrypcji")
    return {
        "items": [m.to_dict() for m in items],
        "transcript": "\n\n".join(m.transcript for m in items if m.transcript),
        "onscreen_text": "\n".join(m.onscreen_text for m in items if m.onscreen_text),
        "voice_emb": voice,
        "voice_matches": match_reference_voices(voice, references) if voice else [],
        "duration_total": round(sum(m.duration or 0 for m in items), 2),
        "speech_seconds": round(sum(m.speech_seconds for m in items), 2),
        "screen_recording": any(m.screen_recording for m in items),
        "confidence": round(min((m.confidence for m in items), default=0.0), 2),
        "missing": missing,
        "errors": [f"{m.name}: {e}" for m in items for e in m.errors],
    }


def analyze_files(paths: Iterable[str | Path], **opts: Any) -> dict[str, Any]:
    """Kilka plików jednego materiału (np. folder przypadku) -> jeden słownik cech."""
    items = [analyze_media(p, **opts) for p in paths]
    return _combine(items, load_reference_voices() if opts.get("voice", True) else None)


def extract(incident: Any, **opts: Any) -> dict[str, Any] | None:
    """Adapter dla potoku: bierze wideo i audio z Incident (preprocessing). None = brak nagrań.

    Ścieżkę dźwiękową wyciągniętą już przez preprocessing (WAV 16 kHz) wykorzystujemy ponownie.
    """
    arts = incident.for_module("av") if hasattr(incident, "for_module") else []
    videos = [a for a in arts if a.kind == "video" and a.path]
    extracted = {a.meta.get("video"): a.path for a in arts if a.kind == "audio" and a.meta.get("video")}
    standalone = [a for a in arts if a.kind == "audio" and a.path and not a.meta.get("video")]
    if not videos and not standalone:
        return None
    items = [analyze_media(v.path, extracted.get(v.path), **opts) for v in videos]
    items += [analyze_media(a.path, **opts) for a in standalone]
    return _combine(items, load_reference_voices() if opts.get("voice", True) else None)


def attach_to_incident(incident: Any, feats: dict[str, Any] | None) -> None:
    """Transkrypcja i napisy z ekranu trafiają do materiału jako tekst – moduł tekstu weźmie je,
    gdy nie ma lepszego źródła (strony). Potem ponownie wybieramy tekst materiału."""
    if not feats:
        return
    from fakecollector.ingest.core import select_text
    from fakecollector.ingest.screenshot import find_urls
    for item in feats["items"]:
        if item.get("transcript"):
            incident.add_text(item["transcript"], "transcript", path=item["path"])
        if item.get("onscreen_text"):
            incident.add_text(item["onscreen_text"], "ocr", path=item["path"], from_video=True)
            for url in find_urls(item["onscreen_text"])[:3]:
                incident.add_url(url, "video_ocr")
    select_text(incident)
    if getattr(incident, "store_dir", None):
        incident.save()


# ============================================================================ głosy referencyjne

_ref_cache: dict[str, np.ndarray] | None = None


def load_reference_voices(root: Path = REFERENCE_DIR) -> dict[str, np.ndarray]:
    """Próbki głosu znanych osób: data/reference_people/<Imię Nazwisko>/*.wav|mp3|m4a|mp4.

    Embeddingi są zapisywane w <root>/_voices.json, żeby nie liczyć ich przy każdym starcie.
    """
    global _ref_cache
    if _ref_cache is not None:
        return _ref_cache
    _ref_cache = {}
    if not root.is_dir():
        return _ref_cache
    cache_file = root / "_voices.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    except (OSError, json.JSONDecodeError):
        cache = {}
    changed = False
    for person_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        embs = []
        for f in sorted(person_dir.iterdir()):
            if f.suffix.lower() not in AUDIO_EXT | VIDEO_EXT:
                continue
            key = f"{person_dir.name}/{f.name}:{f.stat().st_size}:{int(f.stat().st_mtime)}"
            if key not in cache:
                try:
                    y = load_audio(f, 180)
                    emb, _ = voice_embedding(y, speech_segments(y))
                except Exception as exc:
                    log.warning("Głos referencyjny %s: %s", f, exc)
                    emb = None
                cache[key] = emb
                changed = True
            if cache[key]:
                embs.append(np.asarray(cache[key]))
        if embs:
            m = np.mean(embs, axis=0)
            _ref_cache[person_dir.name] = m / (np.linalg.norm(m) + 1e-9)
    if changed:
        try:
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
    return _ref_cache


def match_reference_voices(voice: list[float] | None, refs: dict[str, np.ndarray] | None,
                           min_sim: float = 0.5) -> list[dict[str, Any]]:
    """Do których znanych osób głos jest podobny. To przesłanka (np. sklonowany głos), nie dowód."""
    if not voice or not refs:
        return []
    v = np.asarray(voice)
    sims = sorted(((float(v @ e), name) for name, e in refs.items()), reverse=True)
    return [{"person": name, "similarity": round(s, 3)} for s, name in sims[:3] if s >= min_sim]


# ============================================================================ porównanie

def _cos(a: list[float] | None, b: list[float] | None) -> float | None:
    if not a or not b:
        return None
    va, vb = np.asarray(a), np.asarray(b)
    return float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))


GENERIC_ENCODERS = {"avc coding", "hevc coding", "h.264", "h264", "hevc", "mpeg-4 video", "core media video"}


def _encoder(name: str | None) -> str | None:
    """Nazwa programu kodującego, bez ogólnych nazw kodeków, które nic nie mówią o autorze."""
    if not name or name.strip().lower() in GENERIC_ENCODERS:
        return None
    return name.strip()


VOICE_LOW, VOICE_HIGH = 0.55, 0.80


def _voice_score(cos: float | None) -> float | None:
    # ECAPA na reklamach (muzyka w tle, kompresja): różni lektorzy wychodzą do ~0,5 (DANE/5 ~ DANE/14:
    # 0,47), ten sam głos zwykle > 0,75, ten sam plik ~1,0. Poniżej 0,55 = brak sygnału.
    if cos is None:
        return None
    return round(float(np.clip((cos - VOICE_LOW) / (VOICE_HIGH - VOICE_LOW), 0, 1)), 3)


def _best_voice_pair(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, str, str] | None:
    """Najbardziej podobna para głosów z poszczególnych nagrań (nie uśredniamy różnych lektorów)."""
    best = None
    for ia in a.get("items", []):
        for ib in b.get("items", []):
            c = _cos(ia.get("voice_emb"), ib.get("voice_emb"))
            if c is not None and (best is None or c > best[0]):
                best = (c, ia["name"], ib["name"])
    return best


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Porównanie dwóch materiałów po nagraniach.

    Wynik to najmocniejszy z sygnałów (ten sam dźwięk / ten sam obraz / ten sam głos), bo każdy
    z nich osobno wystarcza, żeby uznać nagrania za powiązane. Transkrypcję porównuje moduł tekstu.
    """
    signals: dict[str, Any] = {"same_audio": None, "same_video": None, "same_voice": None,
                               "voice_cosine": None, "same_encoder": None}
    reasons: list[str] = []
    best_audio = best_video = None
    for ia in a.get("items", []):
        for ib in b.get("items", []):
            r = compare_audio_fp(ia.get("audio_fp_hashes") or [], ia.get("audio_fp_times") or [],
                                 ib.get("audio_fp_hashes") or [], ib.get("audio_fp_times") or [])
            if r and (best_audio is None or r["score"] > best_audio[0]["score"]):
                best_audio = (r, ia["name"], ib["name"])
            v = compare_video_fp(ia.get("video_fp") or [], ib.get("video_fp") or [])
            if v and (best_video is None or v["score"] > best_video[0]["score"]):
                best_video = (v, ia["name"], ib["name"])
            ea, eb = _encoder(ia.get("encoder")), _encoder(ib.get("encoder"))
            if ea and eb:
                same = ea == eb
                signals["same_encoder"] = max(signals["same_encoder"] or 0, 1 if same else 0)
    if best_audio:
        signals["same_audio"] = best_audio[0]["score"]
        if best_audio[0]["score"] >= 0.5:
            reasons.append(f"ta sama ścieżka dźwiękowa ({best_audio[1]} i {best_audio[2]}, "
                           f"{best_audio[0]['matches']} pasujących fragmentów, przesunięcie "
                           f"{best_audio[0]['offset_s']} s)")
    if best_video:
        signals["same_video"] = best_video[0]["score"]
        if best_video[0]["score"] >= 0.5:
            reasons.append(f"te same ujęcia w filmach ({best_video[1]} i {best_video[2]}: "
                           f"{round(best_video[0]['score'] * 100)}% klatek)")
    pair = _best_voice_pair(a, b)
    if pair is None and a.get("voice_emb") and b.get("voice_emb"):
        pair = (_cos(a["voice_emb"], b["voice_emb"]), "", "")
    if pair is not None:
        cos, na, nb = pair
        where = f" ({na} i {nb})" if na else ""
        signals["voice_cosine"] = round(cos, 3)
        signals["same_voice"] = _voice_score(cos)
        if cos >= 0.75:
            reasons.append(f"bardzo podobny głos{where}, podobieństwo {cos:.2f} – ten sam lektor "
                           f"albo ten sam sklonowany głos")
        elif cos >= 0.65:
            reasons.append(f"podobny głos{where}, podobieństwo {cos:.2f}")
    shared_ref = {m["person"] for m in a.get("voice_matches", [])} & {m["person"] for m in b.get("voice_matches", [])}
    if shared_ref:
        reasons.append("w obu nagraniach głos podobny do: " + ", ".join(sorted(shared_ref)))
    if signals["same_encoder"]:
        reasons.append("pliki zapisane tym samym programem (słaba przesłanka)")

    strong = [s for s in (signals["same_audio"], signals["same_video"], signals["same_voice"]) if s is not None]
    av_score = round(max(strong), 3) if strong else None
    conf = round(min(a.get("confidence", 0), b.get("confidence", 0)), 2) if strong else 0.0
    return {"signals": signals, "av_score": av_score, "confidence": conf, "reasons": reasons}


# ============================================================================ CLI

def _strip(feats: dict[str, Any]) -> dict[str, Any]:
    d = dict(feats)
    d["items"] = [{k: (f"<{len(v)} liczb>" if k in MediaFeatures.VECTOR_FIELDS and v else v)
                   for k, v in it.items()} for it in feats["items"]]
    d["voice_emb"] = f"<{len(feats['voice_emb'])} liczb>" if feats.get("voice_emb") else None
    return d


def to_dict(feats: dict[str, Any], with_vectors: bool = True) -> dict[str, Any]:
    """Wersja do API/podglądu bez długich wektorów."""
    return feats if with_vectors else _strip(feats)


def analyze_path(path: str | Path, **opts: Any) -> dict[str, Any] | None:
    """Plik albo folder przypadku -> cechy AV. Nagrania znajduje preprocessing (też strumienie
    wideo zapisane razem ze stroną). None = brak nagrań."""
    from fakecollector.ingest import ingest
    inc = ingest(str(path), fetch=False, ocr=False, video=False, assets=False, save=False, dedup=False)
    feats = extract(inc, **opts)
    if feats is not None:
        feats["incident_missing"] = [m for m in inc.missing if "wideo" in m or "strumienia" in m]
    return feats


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m fakecollector.features.av",
                                 description="Moduł audio i wideo: głos, dźwięk, obraz, transkrypcja")
    ap.add_argument("inputs", nargs="+", help="pliki wideo/audio, foldery przypadków albo folder zbioru")
    ap.add_argument("--compare", action="store_true", help="porównaj wszystkie materiały parami")
    ap.add_argument("--no-asr", action="store_true", help="bez transkrypcji (najwolniejsza część)")
    ap.add_argument("--no-voice", action="store_true", help="bez odcisku głosu (SpeechBrain)")
    ap.add_argument("--no-ocr", action="store_true", help="bez OCR klatek")
    ap.add_argument("--json", help="zapisz cechy do pliku JSON")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(message)s")
    opts = dict(asr=not a.no_asr, voice=not a.no_voice, ocr=not a.no_ocr)

    from fakecollector.ingest.__main__ import expand
    records: list[tuple[str, dict[str, Any]]] = []
    for value in expand(a.inputs):
        feats = analyze_path(value, **opts)
        if not feats:
            continue
        print(f"[av] {value}: {len(feats['items'])} nagrań", flush=True)
        records.append((value, feats))
        for it in feats["items"]:
            print(f"   {it['name']}: {it['kind']}{' (nagranie ekranu)' if it['screen_recording'] else ''} "
                  f"{it['duration']} s, {it['width']}x{it['height']}, mowa {it['speech_seconds']} s, "
                  f"głos={'tak' if it['voice_emb'] else 'nie'}, odcisk dźwięku={len(it['audio_fp_hashes'])}, "
                  f"klatek={len(it['video_fp'])}, koder={it['encoder']}")
            if it["transcript"]:
                print(f"   transkrypcja: {it['transcript'][:300]}")
            if it["onscreen_text"]:
                print(f"   na ekranie: {' | '.join(it['onscreen_text'].splitlines()[:8])[:300]}")
            for e in it["errors"]:
                print(f"   błąd: {e}")
        if feats["voice_matches"]:
            print(f"   głos podobny do: {feats['voice_matches']}")
        for m in feats.get("incident_missing", []):
            print(f"   brak: {m}")

    if a.compare and len(records) > 1:
        print("\nPorównanie parami (av_score, sygnały):")
        for i in range(len(records)):
            for j in range(i + 1, len(records)):
                r = compare(records[i][1], records[j][1])
                s = r["signals"]
                print(f"  {Path(records[i][0]).name:>12} ~ {Path(records[j][0]).name:<12} "
                      f"wynik={r['av_score']}  dźwięk={s['same_audio']} obraz={s['same_video']} "
                      f"głos={s['voice_cosine']}")
                for reason in r["reasons"]:
                    print(f"      - {reason}")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps({k: v for k, v in records}, ensure_ascii=False), encoding="utf-8")
        print(f"Zapisano {len(records)} materiałów do {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
