"""Wycinki obrazu oraz ocena elementów szablonu."""
from dataclasses import dataclass
from typing import Literal, cast

from PIL import Image

from .model import Prostokat
from .wektor import Macierz, ModelObrazu

Rog = Literal["lewy_gorny", "prawy_gorny", "lewy_dolny", "prawy_dolny", "dol"]


@dataclass(frozen=True)
class Wycinek:
    obraz: Image.Image
    prostokat: Prostokat
    rog: Rog | None = None


def wycinki(obraz: Image.Image, szerokosc_rogu: float, wysokosc_rogu: float, wysokosc_pasa: float) -> list[Wycinek]:
    """Daje obraz, cztery rogi i dolny pas w stałej kolejności."""
    w, h = obraz.size
    sw, sh, pas = (max(1, round(miara)) for miara in (w * szerokosc_rogu, h * wysokosc_rogu, h * wysokosc_pasa))
    wynik = [Wycinek(obraz, (0, 0, w, h))]
    for rog, x, y, szer, wys in (
        ("lewy_gorny", 0, 0, sw, sh), ("prawy_gorny", w - sw, 0, sw, sh),
        ("lewy_dolny", 0, h - sh, sw, sh), ("prawy_dolny", w - sw, h - sh, sw, sh),
        ("dol", 0, h - pas, w, pas),
    ):
        wynik.append(Wycinek(obraz.crop((x, y, x + szer, y + wys)), (x, y, szer, wys), cast(Rog, rog)))
    return wynik


def ocen_elementy(model: ModelObrazu, wektory: Macierz, opisy: dict[str, list[str]]) -> dict[str, float]:
    """Zachowuje maksymalny wynik ze wszystkich wycinków także poniżej progu."""
    grupy = list(opisy.values()) + [["an ordinary picture with none of the listed visual elements"]]
    wyniki = model.ocen_opisy(wektory, grupy).max(axis=0)
    return dict(zip(opisy, (float(x) for x in wyniki[:-1]), strict=True))


def na_wejscie(prostokat: Prostokat, rozmiar: tuple[int, int], offset: tuple[int, int],
                orientacja: int) -> Prostokat:
    """Odtwarza współrzędne wejściowe po odcięciu pasów i obrocie EXIF."""
    ox, oy = offset
    x, y, szer, wys = prostokat
    x, y = x + ox, y + oy
    w, h = rozmiar
    wyniki = {
        1: (x, y, szer, wys), 2: (w - x - szer, y, szer, wys),
        3: (w - x - szer, h - y - wys, szer, wys), 4: (x, h - y - wys, szer, wys),
        5: (y, x, wys, szer), 6: (y, h - x - szer, wys, szer),
        7: (w - y - wys, h - x - szer, wys, szer), 8: (w - y - wys, x, wys, szer),
    }
    return wyniki.get(orientacja, wyniki[1])
