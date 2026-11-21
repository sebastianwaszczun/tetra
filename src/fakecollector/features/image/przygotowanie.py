"""Dekodowanie lokalnych obrazów, normalizacja i pomiar treści."""
import re
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from struct import error as BladNaglowka
from time import perf_counter

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .konfiguracja import wczytaj_konfiguracje
from .model import Brak, CechyObrazu, MiaraTresci, Przygotowanie

try:
    from pillow_heif import register_heif_opener
except ImportError:
    register_heif_opener = None

if register_heif_opener is not None:
    register_heif_opener(thumbnails=False)

FORMATY = ("PNG", "JPEG", "WEBP", "GIF", "BMP", "HEIF", "AVIF")
ROZSZERZENIA = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".heif", ".avif", ".svg"})
Obraz = bytes | Image.Image


def czy_svg(dane: bytes) -> bool:
    """Rozpoznaje nagłówek SVG bez wykonywania ani interpretowania dokumentu."""
    return re.match(
        rb"(?:\xef\xbb\xbf)?\s*(?:(?:<\?xml.*?\?>|<!--.*?-->|<!DOCTYPE\s+svg[^>]*>)\s*)*<svg[\s/>]",
        dane[:4096], re.IGNORECASE | re.DOTALL,
    ) is not None


def czytaj_bajty(plik: Path, maks_bajtow: int) -> bytes:
    """Odczyt z limitem chroni także przed zmianą rozmiaru pliku między operacjami."""
    with plik.open("rb") as strumien:
        dane = strumien.read(maks_bajtow + 1)
    if len(dane) > maks_bajtow:
        raise ValueError("Przekroczony limit bajtów pliku")
    return dane


def otworz_raster(dane: bytes, maks_pikseli: int) -> Image.Image:
    """Otwiera pierwszą klatkę, sprawdzając rozmiar przed pełnym dekodowaniem."""
    obraz = Image.open(BytesIO(dane), formats=FORMATY)
    if not 0 < obraz.width * obraz.height <= maks_pikseli:
        obraz.close()
        raise ValueError("Przekroczony limit pikseli lub pusty obraz")
    return obraz


def _orientacja_i_tlo(obraz: Image.Image) -> Image.Image:
    orientowany = ImageOps.exif_transpose(obraz)
    if not orientowany.has_transparency_data:
        return orientowany.convert("RGB")
    rgba = orientowany.convert("RGBA")
    tlo = Image.new("RGB", rgba.size, "white")
    tlo.paste(rgba, mask=rgba.getchannel("A"))
    return tlo


def _dlugosc_pasa(obraz: Image.Image, bok: int, limit: int) -> int:
    w, h = obraz.size
    # Maksymalny jednolity prostokąt przy krawędzi wyznaczamy binarnie.
    dol, gora = 0, limit
    while dol < gora:
        n = (dol + gora + 1) // 2
        prostokat = ((0, 0, n, h), (0, 0, w, n), (w - n, 0, w, h), (0, h - n, w, h))[bok]
        if all(a == b for a, b in obraz.crop(prostokat).getextrema()):
            dol = n
        else:
            gora = n - 1
    return dol


def _obetnij_ramki(obraz: Image.Image, udzial: float) -> tuple[Image.Image, tuple[int, int, int, int]]:
    w, h = obraz.size
    maks_w, maks_h = int(w * udzial), int(h * udzial)
    # Pas sięgający poza limit nie ma widocznej granicy w dopuszczalnym zakresie.
    limity = (maks_w, maks_h, maks_w, maks_h)
    pasy = [_dlugosc_pasa(obraz, bok, limit + 1) if limit else 0 for bok, limit in enumerate(limity)]
    l, g, p, d = (pas if pas <= limit else 0 for pas, limit in zip(pasy, limity))
    # Po odjęciu pasów poziomego koloru można odsłonić inne kolory pasów pionowych.
    if (g or d) and h - g - d > 0:
        srodek = obraz.crop((0, g, w, h - d))
        lewy, prawy = (_dlugosc_pasa(srodek, bok, maks_w + 1) if maks_w else 0 for bok in (0, 2))
        l, p = (pas if pas <= maks_w else 0 for pas in (lewy, prawy))
    ramki = (l, g, p, d)
    return (obraz.crop((l, g, w - p, h - d)) if any(ramki) else obraz), ramki


def _zmierz_tresc(obraz: Image.Image, bok_probki: int, prog_gradientu: float) -> MiaraTresci:
    probka = obraz.copy() if max(obraz.size) <= bok_probki else ImageOps.contain(
        obraz, (bok_probki, bok_probki), Image.Resampling.NEAREST,
    )
    rgb = np.asarray(probka, dtype=np.uint16) >> 4
    kolory = (rgb[:, :, 0] << 8) | (rgb[:, :, 1] << 4) | rgb[:, :, 2]
    dominujacy = np.bincount(kolory.ravel()).max() / kolory.size
    jasnosc = np.asarray(probka.convert("L"), dtype=np.int16)
    poziom, pion = np.diff(jasnosc, axis=1), np.diff(jasnosc, axis=0)
    wyrazne = np.count_nonzero(np.abs(poziom) >= prog_gradientu) + np.count_nonzero(np.abs(pion) >= prog_gradientu)
    return MiaraTresci(dominujacy_kolor=dominujacy, kontrast=float(jasnosc.std()),
                     krawedzie=wyrazne / max(1, poziom.size + pion.size))


def przygotuj_obraz(obraz: Obraz) -> tuple[Image.Image | None, CechyObrazu]:
    """Zwraca raster roboczy i cechy dostępne po przygotowaniu."""
    raster, cechy, _ = przygotuj_z_pelnym_rastrem(obraz)
    return raster, cechy


def przygotuj_z_pelnym_rastrem(obraz: Obraz) -> tuple[Image.Image | None, CechyObrazu, Image.Image | None]:
    """Zwraca także zorientowany raster przed odcięciem ramek."""
    konfiguracja = wczytaj_konfiguracje()
    limity, progi = konfiguracja["wczytywanie"], konfiguracja["przygotowanie"]
    cechy = CechyObrazu()
    start = perf_counter()
    try:
        if isinstance(obraz, bytes):
            if len(obraz) > limity["maks_bajtow"]:
                raise ValueError("Przekroczony limit bajtów pliku")
            if czy_svg(obraz):
                cechy.format = "SVG"
                cechy.sha256 = sha256(obraz).hexdigest()
                raise ValueError("SVG pominięty")
            with otworz_raster(obraz, limity["maks_pikseli"]) as wejscie:
                rozmiar, format_pliku = wejscie.size, wejscie.format or ""
                orientacja = wejscie.getexif().get(274, 1)
                rgb = _orientacja_i_tlo(wejscie)
            cechy.sha256 = sha256(obraz).hexdigest()
        else:
            rozmiar, format_pliku = obraz.size, obraz.format or "PIL"
            if not 0 < obraz.width * obraz.height <= limity["maks_pikseli"]:
                raise ValueError("Przekroczony limit pikseli lub pusty obraz")
            klatka = obraz.tell()
            if klatka:
                obraz.seek(0)
            try:
                rozmiar = obraz.size
                orientacja = obraz.getexif().get(274, 1)
                rgb = _orientacja_i_tlo(obraz)
            finally:
                if klatka:
                    obraz.seek(klatka)
            # Dla obiektu w pamięci oryginalne bajty pliku nie są częścią kontraktu.
            digest = sha256(f"PIL RGB {rgb.width}x{rgb.height}\\0".encode("ascii"))
            digest.update(rgb.tobytes())
            cechy.sha256 = digest.hexdigest()
    except UnidentifiedImageError:
        cechy.braki.append(Brak(skladnik="wczytywanie", powod="Nierozpoznany lub nieobsługiwany format obrazu"))
        return None, cechy, None
    except (OSError, SyntaxError, EOFError, ValueError, RuntimeError, BladNaglowka, Image.DecompressionBombError) as blad:
        cechy.braki.append(Brak(skladnik="wczytywanie", powod=str(blad)))
        return None, cechy, None
    finally:
        cechy.czasy_ms["wczytywanie"] = (perf_counter() - start) * 1000

    start = perf_counter()
    raster, ramki = _obetnij_ramki(rgb, progi["maks_udzial_ramki"])
    raster.info["orientacja_exif"] = orientacja
    rgb.info["orientacja_exif"] = orientacja
    cechy.format = format_pliku
    cechy.rozmiar = raster.size
    cechy.przygotowanie = Przygotowanie(rozmiar_oryginalny=rozmiar, odciete_ramki=ramki, orientacja_exif=orientacja)
    cechy.czasy_ms["przygotowanie"] = (perf_counter() - start) * 1000
    start = perf_counter()
    miara = _zmierz_tresc(raster, progi["bok_probki_px"], progi["prog_gradientu"])
    cechy.miara_tresci = miara
    cechy.ubogi_w_tresc = miara.dominujacy_kolor >= progi["dominujacy_kolor"] or (
        miara.krawedzie <= progi["maks_gestosc_krawedzi"] and miara.kontrast <= progi["maks_niski_kontrast"]
    )
    cechy.za_maly = min(raster.size) < progi["min_bok_px"]
    cechy.czasy_ms["filtr_tresci"] = (perf_counter() - start) * 1000
    return raster, cechy, rgb
