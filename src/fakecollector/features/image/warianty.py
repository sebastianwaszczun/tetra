"""Powtarzalne warianty do sprawdzania obietnic odporności z sekcji 3.5."""
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from random import Random
from typing import Literal

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

Oczekiwanie = Literal["obiecane", "nieobiecane"]


@dataclass
class Wariant:
    nazwa: str
    oczekiwanie: Oczekiwanie
    obraz: Image.Image


def _przytnij(obraz: Image.Image, udzial: float) -> Image.Image:
    """Usuwa zadany procent szerokości i wysokości, po połowie z obu stron."""
    w, h = obraz.size
    szer, wys = max(1, round(w * (1 - udzial))), max(1, round(h * (1 - udzial)))
    x, y = (w - szer) // 2, (h - wys) // 2
    return obraz.crop((x, y, x + szer, y + wys))


def generuj_warianty(obraz: Image.Image, ziarno: int = 0) -> Iterator[Wariant]:
    """Generuje po jednym obrazie naraz. Ziarno ustala kolor zastępczego tła."""
    rgba = obraz.convert("RGBA") if obraz.has_transparency_data else None
    alfa = rgba.getchannel("A") if rgba is not None else None
    if rgba is not None:
        rgb = Image.new("RGB", rgba.size, "white")
        rgb.paste(rgba, mask=alfa)
    else:
        rgb = obraz.convert("RGB")
    w, h = rgb.size
    yield Wariant("skala_25", "obiecane", rgb.resize((max(1, round(w / 4)), max(1, round(h / 4))),
                                                     Image.Resampling.LANCZOS))
    with BytesIO() as bufor:
        rgb.save(bufor, "JPEG", quality=30)
        bufor.seek(0)
        with Image.open(bufor) as jpg:
            yield Wariant("jpeg_q30", "obiecane", jpg.copy())
    yield Wariant("przyciecie_10", "obiecane", _przytnij(rgb, 0.10))
    yield Wariant("ramka", "obiecane", ImageOps.expand(rgb, border=max(1, min(w, h) // 20), fill="white"))
    for nazwa, metoda in (("jasnosc", ImageEnhance.Brightness), ("kontrast", ImageEnhance.Contrast)):
        for procent in (80, 120):
            yield Wariant(f"{nazwa}_{procent}", "obiecane", metoda(rgb).enhance(procent / 100))
    yield Wariant("odbicie", "obiecane", ImageOps.mirror(rgb))
    pas = max(1, int(h * 0.15))
    pasek = Image.new("RGB", (w, h + pas), "black")
    pasek.paste(rgb)
    ImageDraw.Draw(pasek).text((1, h), "TEST", fill="white", font_size=max(1, pas * 3 // 4))
    yield Wariant("pasek_15", "obiecane", pasek)
    znak = rgb.copy()
    ImageDraw.Draw(znak, "RGBA").text((w // 8, h // 3), "TEST", fill=(255, 255, 255, 150),
                                     font_size=max(1, min(w, h) // 5))
    yield Wariant("znak_wodny", "obiecane", znak)
    yield Wariant("obrot_8", "nieobiecane", rgb.rotate(8, resample=Image.Resampling.BICUBIC))
    yield Wariant("przyciecie_30", "nieobiecane", _przytnij(rgb, 0.30))
    los = Random(ziarno)
    kolor = tuple(los.randrange(256) for _ in range(3))
    kolaz = Image.new("RGB", (w, h), kolor)
    mini = rgb.resize((max(1, w // 2), max(1, h // 2)))
    kolaz.paste(mini, (0, 0))
    kolaz.paste(ImageOps.mirror(mini), (w - mini.width, h - mini.height))
    yield Wariant("kolaz", "nieobiecane", kolaz)
    # Dla RGB bez maski zachowujemy środkowy wycinek jako przybliżenie pierwszego planu.
    tlo = Image.new("RGB", (w, h), kolor)
    if rgba is not None and alfa.getextrema()[0] < 255:
        tlo.paste(rgba, mask=alfa)
    else:
        srodek = _przytnij(rgb, 0.40)
        tlo.paste(srodek, ((w - srodek.width) // 2, (h - srodek.height) // 2))
    yield Wariant("wymiana_tla", "nieobiecane", tlo)


def warianty_porownania(obraz: Image.Image, ziarno: int = 0) -> Iterator[Wariant]:
    """Zwraca rozszerzony zestaw wariantów próby generalnej."""
    wybrane = {"pasek_15", "przyciecie_10", "przyciecie_30", "jpeg_q30", "odbicie", "skala_25"}
    yield from (w for w in generuj_warianty(obraz, ziarno) if w.nazwa in wybrane)
    rgb = obraz.convert("RGB")
    yield Wariant("przyciecie_20", "nieobiecane", _przytnij(rgb, 0.20))
    w, h = rgb.size
    tlo = Image.new("RGB", (w + w // 2, h + h // 2), "#738093")
    ImageDraw.Draw(tlo).rectangle((0, 0, tlo.width, h // 6), fill="#331c28")
    tlo.paste(rgb, (w // 4, h // 4))
    yield Wariant("wstawka", "nieobiecane", tlo)


def scena_syntetyczna(ziarno: int, rozmiar: tuple[int, int] = (450, 320)) -> Image.Image:
    """Powtarzalna scena geometryczna do lokalnego pomiaru i testów porównania."""
    los = Random(ziarno)
    w, h = rozmiar
    obraz = Image.new("RGB", rozmiar, "#58686b")
    rys = ImageDraw.Draw(obraz)
    for _ in range(110):
        x, y = los.randrange(w), los.randrange(h)
        p, d = min(w - 1, x + los.randrange(5, max(6, w // 8))), min(h - 1, y + los.randrange(5, max(6, h // 8)))
        kolor = tuple(los.randrange(256) for _ in range(3))
        figura = los.choice((rys.rectangle, rys.ellipse))
        figura((x, y, p, d), fill=kolor, outline="#eae6d4", width=1)
    return obraz
