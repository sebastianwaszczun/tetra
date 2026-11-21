"""Lokalne predyktory opisowe FaceTorch na wycinkach z detektorów twarzy."""
import json
from collections.abc import Callable, Sequence
from functools import cache
from hashlib import file_digest
from math import isfinite
from pathlib import Path
from threading import Lock
from time import perf_counter

import cv2
import numpy as np
from numpy.typing import NDArray

from .model import Brak, CechyObrazu, Emocja, Prostokat, Twarz

_BLOKADA = Lock()
_PLIKI = ("deepfake.pt", "fer.pt")
ETYKIETY_EMOCJI = ("Anger", "Disgust", "Fear", "Happiness", "Neutral", "Sadness", "Surprise")
Predyktor = Callable[[NDArray[np.generic]], tuple[float, Emocja]]


@cache
def _wczytaj_predyktor(manifest: Path) -> Predyktor:
    opis = json.loads(manifest.read_text(encoding="utf-8"))
    katalog = Path(opis["sciezka"]).resolve()
    pliki = {}
    for nazwa in _PLIKI:
        plik = (katalog / nazwa).resolve()
        if not katalog.is_relative_to(manifest.parent.resolve()) or not plik.is_relative_to(katalog):
            raise ValueError("Wagi FaceTorch muszą pozostać w lokalnym katalogu modeli")
        with plik.open("rb") as zrodlo:
            if file_digest(zrodlo, "sha256").hexdigest() != opis["sha256_plikow"][nazwa]:
                raise ValueError(f"Niezgodna suma SHA-256 wag {nazwa}")
        pliki[nazwa] = plik

    import torch
    from facetorch.analyzer.predictor import FacePredictor
    from facetorch.analyzer.predictor.post import PostArgMax, PostSigmoidBinary
    from facetorch.analyzer.predictor.pre import PredictorPreProcessor
    from facetorch.base import BaseDownloader
    from torchvision.transforms import Compose, Normalize, Resize

    class PlikLokalny(BaseDownloader):
        def run(self) -> None:
            raise FileNotFoundError(self.path_local)

    cpu = torch.device("cpu")
    normalizacja = Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])

    def predyktor(nazwa: str, bok: int, post: PostArgMax | PostSigmoidBinary) -> FacePredictor:
        transform = Compose([Resize((bok, bok), antialias=True), normalizacja])
        pre = PredictorPreProcessor(transform, cpu, optimize_transform=False)
        return FacePredictor(PlikLokalny("", str(pliki[nazwa])), cpu, pre, post)

    # Geometria, kolory i kolejność etykiet z konfiguracji wydania 0.6.2.
    deepfake = predyktor("deepfake.pt", 380, PostSigmoidBinary(None, cpu, False, ["Real", "Fake"]))
    fer = predyktor("fer.pt", 244, PostArgMax(None, cpu, False, list(ETYKIETY_EMOCJI), dim=1))

    def ocen(wycinek: NDArray[np.generic]) -> tuple[float, Emocja]:
        rgb = cv2.cvtColor(wycinek, cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).float().div_(255)
        with torch.inference_mode():
            wynik = deepfake.run(tensor)[0].logits.item()
            ekspresja = fer.run(tensor)[0]
            pewnosc = torch.softmax(ekspresja.logits, dim=0).max().item()
        return wynik, Emocja(etykieta=ekspresja.label, pewnosc=pewnosc)

    return ocen


def predyktor_facetorch(manifest: Path) -> Predyktor:
    """Ładuje oba modele raz na proces, z weryfikacją lokalnych plików."""
    with _BLOKADA:
        return _wczytaj_predyktor(manifest)


def opisz_twarze(raster: NDArray[np.generic], kandydaci: Sequence[tuple[Twarz, Prostokat, bool]],
                 cechy: CechyObrazu, manifest: Path, limit: int) -> None:
    """Uzupełnia opis największych twarzy przyjętych przez filtr jakości."""
    gotowe = [(t, p) for t, p, jakosc_ok in kandydaci if jakosc_ok]
    for twarz, _, jakosc_ok in kandydaci:
        if not jakosc_ok:
            twarz.brak_facetorch = "niska_jakosc"
    gotowe.sort(key=lambda wpis: -(wpis[1][2] * wpis[1][3]))
    for twarz, _ in gotowe[limit:]:
        twarz.brak_facetorch = "limit_twarzy"
    wybrane = gotowe[:limit]
    if not wybrane:
        return
    try:
        predyktor = predyktor_facetorch(manifest)
    except (OSError, ValueError, RuntimeError, ImportError, KeyError) as blad:
        for twarz, _ in wybrane:
            twarz.brak_facetorch = "brak_wag"
        cechy.braki.append(Brak(skladnik="facetorch", powod=f"brak_wag: {blad}"))
        return

    czas = 0.0
    for twarz, (x, y, w, h) in wybrane:
        start = perf_counter()
        try:
            wynik, emocja = predyktor(raster[y:y + h, x:x + w])
            if not isfinite(wynik) or not 0 <= wynik <= 1:
                raise ValueError("Wynik FaceTorch spoza skali 0–1")
            twarz.deepfake = wynik
            twarz.emocja = emocja
            twarz.uwaga_facetorch = "orientacyjnie"
        except (OSError, ValueError, RuntimeError, cv2.error) as blad:
            twarz.brak_facetorch = "blad_predykcji"
            cechy.braki.append(Brak(skladnik="facetorch", powod=f"blad_predykcji: {blad}"))
        finally:
            czas += (perf_counter() - start) * 1000
    cechy.czasy_ms["facetorch"] = czas
