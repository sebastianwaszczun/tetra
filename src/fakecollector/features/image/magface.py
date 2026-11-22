"""Lokalna ocena jakości z normy MagFace iResNet100."""
import json
from collections.abc import Mapping
from functools import cache
from hashlib import file_digest
from math import isfinite
from pathlib import Path
from pickle import UnpicklingError
from threading import Lock
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from torch.nn import Module

MANIFEST = Path("/models/obraz/magface.json")
_BLOKADA = Lock()


def skaluj_norme(norma: float, granice: Mapping[str, float]) -> float:
    """Skaluje normę liniowo i ogranicza wynik do przedziału 0–1."""
    dol, gora = granice["norma_min"], granice["norma_max"]
    if not (isfinite(norma) and isfinite(dol) and isfinite(gora) and gora > dol):
        raise ValueError("Nieprawidłowa norma lub granice jakości MagFace")
    return float(np.clip((norma - dol) / (gora - dol), 0, 1))


@cache
def _wczytaj_model() -> "Module":
    opis = json.loads(MANIFEST.read_text(encoding="utf-8"))
    plik = Path(opis["sciezka"]).resolve()
    if not plik.is_relative_to(MANIFEST.parent.resolve()):
        raise ValueError("Wagi MagFace muszą znajdować się w /models/obraz")
    with plik.open("rb") as zrodlo:
        suma = file_digest(zrodlo, "sha256").hexdigest()
    if suma != opis["sha256_plikow"][plik.name]:
        raise ValueError("Niezgodna suma SHA-256 wag MagFace")

    import torch

    from ._magface_iresnet import iresnet100

    try:
        checkpoint = torch.load(plik, map_location="cpu", weights_only=True)
    except (EOFError, UnpicklingError) as blad:
        raise ValueError("Nieprawidłowy plik wag MagFace") from blad
    wagi = {}
    for nazwa, tensor in checkpoint["state_dict"].items():
        for prefiks in ("features.module.", "module.features."):
            if nazwa.startswith(prefiks):
                wagi[nazwa.removeprefix(prefiks)] = tensor
                break
    model = iresnet100()
    model.load_state_dict(wagi, strict=True)
    return model.eval()


def norma_magface(wyrownana: NDArray[np.generic]) -> float:
    """Zwraca normę MagFace z wyrównanego rastra BGR 112 × 112."""
    with _BLOKADA:
        model = _wczytaj_model()
    import torch
    wejscie = torch.from_numpy(wyrownana).permute(2, 0, 1).unsqueeze(0).float().div_(255)
    with torch.inference_mode():
        return torch.linalg.vector_norm(model(wejscie)).item()


def ocena_magface(wyrownana: NDArray[np.generic], granice: Mapping[str, float]) -> float:
    """Zwraca normę MagFace przeskalowaną do 0–1."""
    return skaluj_norme(norma_magface(wyrownana), granice)
