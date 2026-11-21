"""Hashe percepcyjne i odległości pomiędzy wariantami rastra."""
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Literal

import imagehash
from PIL import Image, ImageOps

from .model import CechyObrazu, Hashe

WariantHash = Literal["caly", "srodek", "odbicie"]


@dataclass(frozen=True)
class Odleglosc:
    wariant: WariantHash
    phash: int
    dhash: int


def policz_hashe(obraz: Image.Image, udzial_srodka: float) -> Hashe:
    """Liczy pHash i dHash 64-bit dla całości, środka i odbicia."""
    szary = obraz.convert("L")
    w, h = szary.size
    szer, wys = max(1, round(w * udzial_srodka)), max(1, round(h * udzial_srodka))
    x, y = (w - szer) // 2, (h - wys) // 2
    warianty = (szary, szary.crop((x, y, x + szer, y + wys)), ImageOps.mirror(szary))
    return Hashe(phash=tuple(str(imagehash.phash(wariant)) for wariant in warianty),
                 dhash=tuple(str(imagehash.dhash(wariant)) for wariant in warianty))


def odleglosci(a: Hashe, b: Hashe) -> tuple[Odleglosc, ...]:
    """Łączy pHash i dHash tylko w obrębie tej samej pary wariantów."""
    return odleglosci_64(*(tuple(int(v, 16) for v in h) for h in (a.phash, a.dhash, b.phash, b.dhash)))


def odleglosci_64(ap: Sequence[int], ad: Sequence[int], bp: Sequence[int], bd: Sequence[int]) -> tuple[Odleglosc, ...]:
    """Liczy odległość bitową XOR dla wersji liczbowej kluczy."""
    if {len(ap), len(ad), len(bp), len(bd)} != {3}:
        return ()
    pary = (("caly", 0, 0), ("srodek", 1, 1), ("odbicie", 2, 0), ("odbicie", 0, 2))
    return tuple(Odleglosc(wariant,
                          (ap[i] ^ bp[j]).bit_count(), (ad[i] ^ bd[j]).bit_count())
                 for wariant, i, j in pary)


def pasujaca_para(pomiary: tuple[Odleglosc, ...], T_p: int, T_d: int) -> Odleglosc | None:
    """Wybiera najbliższą parę spełniającą jednocześnie oba progi."""
    trafienia = (p for p in pomiary if p.phash <= T_p and p.dhash <= T_d)
    return min(trafienia, key=lambda p: (p.phash + p.dhash, p.phash, p.wariant), default=None)


def duplikat(a: CechyObrazu, b: CechyObrazu, T_p: int, T_d: int) -> bool:
    """Sprawdza najpierw identyczność, potem dopasowanie hashy percepcyjnych."""
    return bool((a.sha256 and a.sha256 == b.sha256) or pasujaca_para(odleglosci(a.hashe, b.hashe), T_p, T_d))
