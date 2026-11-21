"""Agregacja cech z kolejnych ujęć tej samej reklamy."""
from collections.abc import Sequence

import numpy as np

from .konfiguracja import wczytaj_konfiguracje
from .model import Brak, CechyObrazu, CechyZbiorcze, KodQR, Logo, Twarz
from .qr import normalizuj_qr
from .twarze import porownaj_modele_twarzy


def zbierz(cechy: Sequence[CechyObrazu]) -> CechyZbiorcze:
    """Zbiera etykiety, klucze i najlepsze twarze wraz z wersjonowanymi wektorami."""
    wynik = CechyZbiorcze()
    cfg = wczytaj_konfiguracje()["twarze"]
    logotypy: dict[str, Logo] = {}
    qr: dict[str, KodQR] = {}
    twarze: dict[str, Twarz] = {}
    pozostale: list[tuple[Twarz, frozenset[str]]] = []
    for c in cechy:
        niedostepne = frozenset(c.niedostepne_modele_twarzy)
        for nazwa, ocena in c.elementy_szablonu.items():
            wynik.elementy[nazwa] = max(ocena, wynik.elementy.get(nazwa, 0.0))
        for logo in c.logo_obraz:
            poprzedni = logotypy.get(logo.marka)
            if poprzedni is None or logo.pewnosc > poprzedni.pewnosc:
                logotypy[logo.marka] = logo
        for kod in c.qr:
            qr.setdefault(normalizuj_qr(kod.tekst), kod)
        for t in c.twarze:
            if t.osoba is None:
                pozostale.append((t, niedostepne))
            elif t.osoba not in twarze or t.jakosc > twarze[t.osoba].jakosc:
                twarze[t.osoba] = t
        if c.sha256 and c.sha256 not in wynik.zrodla:
            wynik.zrodla.append(c.sha256)
        if c.hashe.phash or c.hashe.dhash:
            wynik.hashe.append(c.hashe.model_copy(deep=True))
        if c.wektor is not None:
            wynik.wektory.append(c.wektor.model_copy(deep=True))
    anonimowe: list[tuple[Twarz, frozenset[str]]] = []
    for t, brak in sorted(pozostale, key=lambda x: -x[0].jakosc):
        if not any(porownaj_modele_twarzy([t], [u], cfg, brak | drugi_brak)[1] for u, drugi_brak in anonimowe):
            anonimowe.append((t, brak))
    wynik.twarze = [t.model_copy(deep=True) for t in (*twarze.values(), *(t for t, _ in anonimowe))]
    wynik.logo = [l.model_copy() for l in logotypy.values()]
    wynik.qr = [k.model_copy() for k in qr.values()]

    wersje = {(w.model, w.wersja, w.wymiar) for w in wynik.wektory}
    if len(wersje) == 1:
        srednia = np.mean([w.wartosci for w in wynik.wektory], axis=0)
        norma = np.linalg.norm(srednia)
        if norma > 0:
            wynik.wektor_sredni = wynik.wektory[0].model_copy(update={"wartosci": (srednia / norma).tolist()})
        else:
            wynik.braki.append(Brak(skladnik="wektor_sredni", powod="Zerowa norma średniej"))
    elif wersje:
        wynik.braki.append(Brak(skladnik="wektor_sredni", powod="Niezgodne modele, wersje lub wymiary"))
    else:
        wynik.braki.append(Brak(skladnik="wektor_sredni", powod="Brak wektorów obrazu"))
    return wynik
