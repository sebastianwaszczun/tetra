"""Rozbicie wideo na ścieżkę dźwiękową (dla SpeechBrain i Whisper) i klatki (dla modułu obrazu)."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_FRAMES = 60
FRAME_WIDTH = 1280


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def probe(path: str | Path) -> dict[str, Any]:
    """Długość, rozdzielczość i obecność dźwięku (ffprobe)."""
    r = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], 60)
    if r.returncode != 0:
        return {"error": r.stderr.strip()[:300]}
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "duration": float(data.get("format", {}).get("duration") or 0),
        "width": video.get("width"),
        "height": video.get("height"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "has_video": bool(video),
    }


def extract_audio(path: str | Path, out_dir: str | Path) -> str | None:
    """WAV mono 16 kHz – format oczekiwany przez SpeechBrain i Whisper."""
    out = Path(out_dir) / f"{Path(path).stem[:60]}_audio.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    r = _run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(out)])
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
        log.warning("Brak dźwięku w %s: %s", path, r.stderr.strip()[:200])
        return None
    return str(out)


def extract_frames(path: str | Path, out_dir: str | Path, duration: float = 0.0) -> list[str]:
    """Klatki co sekundę (dla dłuższych filmów rzadziej, najwyżej MAX_FRAMES)."""
    folder = Path(out_dir) / f"{Path(path).stem[:60]}_frames"
    folder.mkdir(parents=True, exist_ok=True)
    every = max(1.0, duration / MAX_FRAMES) if duration else 1.0
    r = _run(["ffmpeg", "-y", "-v", "error", "-i", str(path),
              "-vf", f"fps=1/{every:.3f},scale='min({FRAME_WIDTH},iw)':-2",
              "-frames:v", str(MAX_FRAMES), "-q:v", "3", str(folder / "frame_%04d.jpg")])
    if r.returncode != 0:
        log.warning("Nie udało się wyciągnąć klatek z %s: %s", path, r.stderr.strip()[:200])
    return sorted(str(p) for p in folder.glob("frame_*.jpg"))
