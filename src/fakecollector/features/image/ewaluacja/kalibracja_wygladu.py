"""Progi i metryki wyglądu na ręcznie opisanych kreacjach."""
from collections.abc import Sequence
from dataclasses import dataclass
from math import nextafter
from typing import Any


@dataclass(frozen=True)
class Ocena:
    zgloszenie: str
    wynik: float | None
    prawda: bool


def jakosc(decyzje: Sequence[tuple[bool, bool]]) -> dict[str, int | float | None]:
    """Oblicza precyzję i czułość z licznikami i jawnym brakiem mianownika."""
    tp = sum(wykryto and prawda for wykryto, prawda in decyzje)
    fp = sum(wykryto and not prawda for wykryto, prawda in decyzje)
    fn = sum(not wykryto and prawda for wykryto, prawda in decyzje)
    return {"tp": tp, "fp": fp, "fn": fn, "n": len(decyzje),
            "precyzja": tp / (tp + fp) if tp + fp else None, "czulosc": tp / (tp + fn) if tp + fn else None}


def prog_bez_falszywych(oceny: Sequence[Ocena]) -> float | None:
    """Zbiera wszystkie osiągalne trafienia przy zerze fałszywych wykryć."""
    negatywy = [o.wynik for o in oceny if not o.prawda and o.wynik is not None]
    if not any(not o.prawda for o in oceny):
        return None
    granica = max(negatywy, default=-1.0)
    trafienia = [o.wynik for o in oceny if o.prawda and o.wynik is not None and o.wynik > granica]
    if not trafienia:
        return None
    najslabsze = min(trafienia)
    return max(nextafter(granica, 1.0), (max(granica, 0.0) + najslabsze) / 2)


def decyzja(ocena: Ocena, prog: float | None) -> bool:
    return prog is not None and ocena.wynik is not None and ocena.wynik >= prog


def podsumuj(oceny: Sequence[Ocena], prog_biezacy: float | None) -> dict[str, Any]:
    """Podaje wynik bieżący, kalibrację i sprawdzian z całym zgłoszeniem poza próbą."""
    prog = prog_bez_falszywych(oceny)
    zgloszenia = sorted({o.zgloszenie for o in oceny})
    pozytywne_zgloszenia = sum(any(o.prawda and o.zgloszenie == z for o in oceny) for z in zgloszenia)
    sprawdzian = []
    dla_progow = 0
    for z in zgloszenia:
        prog_bez_zgloszenia = prog_bez_falszywych([o for o in oceny if o.zgloszenie != z])
        dla_progow += prog_bez_zgloszenia is not None
        sprawdzian.extend((decyzja(o, prog_bez_zgloszenia), o.prawda) for o in oceny if o.zgloszenie == z)
    return {
        "prog_biezacy": prog_biezacy, "biezacy": jakosc([(decyzja(o, prog_biezacy), o.prawda) for o in oceny]),
        "prog": prog, "kalibracja": jakosc([(decyzja(o, prog), o.prawda) for o in oceny]),
        "pozytywne_zgloszenia": pozytywne_zgloszenia,
        "poza_proba": {**jakosc(sprawdzian), "foldy_z_progiem": dla_progow, "foldy": len(zgloszenia)},
    }
