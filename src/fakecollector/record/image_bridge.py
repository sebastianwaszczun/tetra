"""Pomocnicze funkcje modułu obrazu dla rekordu materiału.

Samą analizę obrazów materiału robi adapter modułu obrazu: fakecollector.features.image.potok.extract.
Tu są tylko: odtworzenie CechyObrazu z JSON-a, wektory twarzy do pgvector i progi z kalibracji.
"""
from __future__ import annotations

from typing import Any


def load_features(item: dict[str, Any]):
    """CechyObrazu z zapisanego JSON-a (do porównań w etapie grupowania)."""
    from fakecollector.features.image.model import CechyObrazu
    return CechyObrazu.model_validate(item["cechy"])


def face_vectors(feats: dict[str, Any]) -> list[tuple[str, int, list[float]]]:
    """Wektory twarzy zaakceptowane przez próg jakości: (model, numer, wartości)."""
    from fakecollector.features.image.konfiguracja import wczytaj_konfiguracje
    from fakecollector.features.image.twarze import wektory_twarzy
    cfg = wczytaj_konfiguracje()["twarze"]
    out = []
    for i, item in enumerate(feats.get("items", [])):
        cechy = load_features(item)
        for j, twarz in enumerate(cechy.twarze):
            for w in wektory_twarzy(twarz, cfg).values():
                out.append((w.model, i * 100 + j, list(w.wartosci)))
    return out


def face_threshold(model: str) -> float:
    """Próg podobieństwa twarzy z kalibracji modułu obrazu (T_f) dla danego modelu."""
    from fakecollector.features.image.konfiguracja import wczytaj_konfiguracje
    from fakecollector.features.image.twarze import MODEL_TWARZY
    cfg = wczytaj_konfiguracje()["twarze"]
    for sigiel, name in MODEL_TWARZY.items():
        if name == model and "T_f" in cfg.get(sigiel, {}):
            return float(cfg[sigiel]["T_f"])
    return 0.4


def appearance_threshold(model: str) -> float:
    """Próg podobieństwa wyglądu (T_w) z kalibracji, z zapasem jak w module obrazu."""
    from fakecollector.features.image.konfiguracja import wczytaj_konfiguracje
    from fakecollector.features.image.wektor import NAZWY_MODELI
    cfg = wczytaj_konfiguracje()
    for sigiel, name in NAZWY_MODELI.items():
        if name == model and "T_w" in cfg["wyglad"].get(sigiel, {}):
            return float(cfg["wyglad"][sigiel]["T_w"]) - float(cfg["orb"].get("zapas_cos", 0.0))
    return 0.7
