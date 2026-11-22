"""Kodowanie obrazów i opisów przez lokalne modele open_clip."""
import json
from collections import OrderedDict
from collections.abc import Sequence
from functools import cache, partial
from hashlib import file_digest
from pathlib import Path
from threading import Lock
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps

from .model import Wektor

KATALOG_MODELI = Path("/models/obraz")
NAZWY_MODELI = {"siglip2": "ViT-B-16-SigLIP2", "clip": "ViT-B-32"}
Macierz = NDArray[np.float32]
Kadrowanie = Literal["dopelnienie", "srodek"]


def dopelnij_do_kwadratu(obraz: Image.Image, bok_modelu: int) -> Image.Image:
    """Zachowuje całą zawartość i dodaje symetryczne białe tło."""
    bok = min(max(obraz.size), bok_modelu)
    if max(obraz.size) > bok:
        skala = bok / max(obraz.size)
        obraz = obraz.resize((max(1, round(obraz.width * skala)), max(1, round(obraz.height * skala))),
                             Image.Resampling.BICUBIC)
    if obraz.size == (bok, bok):
        return obraz
    tlo = Image.new("RGB", (bok, bok), "white")
    tlo.paste(obraz, ((bok - obraz.width) // 2, (bok - obraz.height) // 2))
    return tlo


def _srodek_kwadratu(obraz: Image.Image, bok_modelu: int) -> Image.Image:
    bok = min(obraz.width, obraz.height, bok_modelu)
    return ImageOps.fit(obraz, (bok, bok), method=Image.Resampling.BICUBIC)


class ModelObrazu:
    """Jeden załadowany model wraz z lokalnym tokenizerem i pamięcią opisów."""

    def __init__(self, sigiel: str, wersja: str, model: Any, transformacja: Any, tokenizer: Any, torch: Any) -> None:
        self.sigiel = sigiel
        self.nazwa = NAZWY_MODELI[sigiel]
        self.wersja = wersja
        self.model = model
        self.transformacja = transformacja
        self.tokenizer = tokenizer
        self.torch = torch
        rozmiar = model.visual.image_size
        self.bok_modelu = rozmiar if isinstance(rozmiar, int) else min(rozmiar)
        self._opisy: OrderedDict[str, Macierz] = OrderedDict()
        self._blokada = Lock()

    def koduj_obrazy(self, obrazy: Sequence[Image.Image], kadrowanie: Kadrowanie = "dopelnienie") -> Macierz:
        przygotuj = dopelnij_do_kwadratu if kadrowanie == "dopelnienie" else _srodek_kwadratu
        partia = self.torch.stack([self.transformacja(przygotuj(obraz, self.bok_modelu)) for obraz in obrazy])
        with self.torch.inference_mode():
            return self.model.encode_image(partia, normalize=True).float().cpu().numpy()

    def koduj_opisy(self, opisy: Sequence[str]) -> Macierz:
        with self._blokada:
            brakujace = list(dict.fromkeys(opis for opis in opisy if opis not in self._opisy))
            if brakujace:
                with self.torch.inference_mode():
                    wektory = self.model.encode_text(self.tokenizer(brakujace), normalize=True).float().cpu().numpy()
                self._opisy.update(zip(brakujace, wektory, strict=True))
            macierz = np.stack([self._opisy[opis] for opis in opisy])
            for opis in opisy:
                self._opisy.move_to_end(opis)
            while len(self._opisy) > 512:
                self._opisy.popitem(last=False)
        return macierz

    def ocen_opisy(self, obrazy: Macierz, grupy: Sequence[Sequence[str]]) -> Macierz:
        """Uśrednia cosinusy opisów, następnie stosuje funkcję wyniku modelu."""
        wektory = self.koduj_opisy([opis for grupa in grupy for opis in grupa])
        konce = np.cumsum([len(g) for g in grupy[:-1]])
        srednie = np.stack([wartosci.mean(axis=0) for wartosci in np.split(wektory, konce)], axis=1)
        skala = float(self.model.logit_scale.detach().exp().cpu())
        logity = (obrazy @ srednie) * skala
        if self.sigiel == "siglip2":
            logity += float(self.model.logit_bias.detach().cpu())
            return 1 / (1 + np.exp(-np.clip(logity, -80, 80)))
        wykladnicze = np.exp(logity - logity.max(axis=1, keepdims=True))
        return wykladnicze / wykladnicze.sum(axis=1, keepdims=True)

    def rekord(self, wektor: Macierz) -> Wektor:
        return Wektor(model=self.nazwa, wersja=self.wersja, wymiar=wektor.size, wartosci=wektor.tolist())


_BLOKADA_MODELI = Lock()


def _tokeny_siglip2(tokenizer: Any, torch: Any, opisy: Sequence[str]) -> Any:
    tokeny = tokenizer.tokenizer(
        [tokenizer.clean_fn(opis) for opis in opisy],
        return_tensors="np", max_length=tokenizer.context_length, padding="max_length", truncation=True,
    ).input_ids
    if tokenizer.strip_sep_token:
        tokeny = np.where(tokeny == tokenizer.tokenizer.sep_token_id, 0, tokeny)
    return torch.from_numpy(tokeny.astype(np.int64, copy=False))


def pobierz_model(sigiel: str) -> ModelObrazu:
    """Ładuje model raz na proces z jawnie przekazanych plików lokalnych."""
    with _BLOKADA_MODELI:
        return _wczytaj_model(sigiel)


@cache
def _wczytaj_model(sigiel: str) -> ModelObrazu:
    plik = KATALOG_MODELI / f"{sigiel}.json"
    if sigiel not in NAZWY_MODELI or not plik.is_file():
        raise FileNotFoundError(f"Brak lokalnych wag {sigiel} w /models/obraz")
    manifest = json.loads(plik.read_text(encoding="utf-8"))
    wagi = Path(manifest["sciezka"])
    if not wagi.resolve().is_relative_to(KATALOG_MODELI.resolve()) or not wagi.exists():
        raise FileNotFoundError(f"Nieprawidłowa lokalna ścieżka wag {sigiel}")

    import open_clip
    import torch

    if sigiel == "siglip2":
        if not (wagi / "open_clip_model.safetensors").is_file():
            raise FileNotFoundError("Brak lokalnych wag SigLIP 2 w formacie safetensors")
        identyfikator = f"local-dir:{wagi}"
        lokalny_tokenizer = open_clip.get_tokenizer(identyfikator, local_files_only=True, trust_remote_code=False)
        tokenizer = partial(_tokeny_siglip2, lokalny_tokenizer, torch)
        model, _, transformacja = open_clip.create_model_and_transforms(identyfikator, device="cpu")
    else:
        identyfikator = NAZWY_MODELI[sigiel]
        config = open_clip.get_pretrained_cfg(identyfikator, "openai")
        with wagi.open("rb") as plik_wag:
            if file_digest(plik_wag, "sha256").hexdigest() != config["url"].split("/")[-2]:
                raise ValueError("Wagi CLIP mają niezgodną sumę SHA-256")
        tokenizer = open_clip.get_tokenizer(identyfikator)
        model, _, transformacja = open_clip.create_model_and_transforms(
            identyfikator, pretrained=str(wagi), device="cpu", force_quick_gelu=True,
            image_mean=config["mean"], image_std=config["std"],
            image_interpolation=config["interpolation"], image_resize_mode=config["resize_mode"],
        )
    return ModelObrazu(sigiel, manifest["wersja"], model.eval(), transformacja, tokenizer, torch)


def cosinus(a: Wektor | None, b: Wektor | None) -> float | None:
    """Porównuje wyłącznie wektory o zgodnej tożsamości i wymiarze."""
    if a is None or b is None or (a.model, a.wersja, a.wymiar) != (b.model, b.wersja, b.wymiar):
        return None
    normy = np.linalg.norm(a.wartosci) * np.linalg.norm(b.wartosci)
    return float(np.clip(np.dot(a.wartosci, b.wartosci) / normy, -1, 1)) if normy else None
