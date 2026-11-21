"""Wybór progów hashy na parach kontrolnych i wariantach obrazu."""
from collections.abc import Iterable

import numpy as np
from numpy.typing import NDArray

from ..hashe import Odleglosc

Pomiary = tuple[Odleglosc, ...]


def siatka_trafien(pary: Iterable[Pomiary]) -> NDArray[np.int64]:
    """W komórce (p, d) zapisuje liczbę par spełniających oba progi."""
    siatka = np.zeros((65, 65), dtype=np.int64)
    for para in pary:
        granica = 65
        poczatek = 0
        for pomiar in sorted(para, key=lambda p: (p.phash, p.dhash)):
            if pomiar.dhash < granica:
                if granica <= 64:
                    siatka[poczatek:pomiar.phash, granica] += 1
                poczatek, granica = pomiar.phash, pomiar.dhash
        if granica <= 64:
            siatka[poczatek:, granica] += 1
    return siatka.cumsum(axis=1)


def dobierz_progi(pozytywy: Iterable[Pomiary], negatywy: Iterable[Pomiary], zapas: int) -> tuple[int, int] | None:
    """Maksymalizuje trafienia przy zerze błędów nawet po podniesieniu obu progów o zapas."""
    trafienia = siatka_trafien(pozytywy)
    bledy = siatka_trafien(negatywy)
    kandydaci = ((p, d) for p in range(65 - zapas) for d in range(65 - zapas)
                 if bledy[p + zapas, d + zapas] == 0)
    # Przy tej samej czułości wybieramy bardziej restrykcyjną parę.
    return max(kandydaci, key=lambda k: (int(trafienia[k]), -sum(k), -k[0]), default=None)
