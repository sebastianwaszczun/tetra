"""Klucze i lokalne wyszukiwanie kandydatów bez bazy."""
from collections.abc import Mapping

from .hashe import odleglosci_64, pasujaca_para
from .konfiguracja import wczytaj_konfiguracje
from .model import Brak, CechyObrazu, KluczeWyszukiwania
from .qr import normalizuj_qr
from .twarze import MODEL_TWARZY, wektory_twarzy
from .wektor import NAZWY_MODELI, cosinus


def klucze_wyszukiwania(cechy: CechyObrazu) -> KluczeWyszukiwania:
    """Zwraca liczby 64-bit, wersjonowane wektory i znormalizowane QR."""
    qr = sorted({normalizuj_qr(k.tekst) for k in cechy.qr})
    wynik = KluczeWyszukiwania(sha256=cechy.sha256 or None, qr=qr)
    if not (cechy.ubogi_w_tresc or cechy.za_maly):
        cfg = wczytaj_konfiguracje()["twarze"]
        wynik.phash = [int(h, 16) for h in cechy.hashe.phash]
        wynik.dhash = [int(h, 16) for h in cechy.hashe.dhash]
        wynik.wektor = cechy.wektor
        wynik.wektory_twarzy = [w for t in cechy.twarze for w in wektory_twarzy(t, cfg).values()]
    if not (wynik.phash or wynik.wektor or wynik.wektory_twarzy):
        wynik.braki.append(Brak(skladnik="klucze_wizualne", powod="Brak hashy percepcyjnych i wektorów"))
    return wynik


def szukaj_kandydatow(klucz: KluczeWyszukiwania, indeks: Mapping[str, KluczeWyszukiwania]) -> list[str]:
    """Zwraca identyfikatory kandydatów. Pojedynczy sygnał wystarcza do wyszukania."""
    cfg = wczytaj_konfiguracje()
    h, orb = cfg["hashe"], cfg["orb"]
    tf = {nazwa: cfg["twarze"][s] for s, nazwa in MODEL_TWARZY.items()}
    prog_wygladu = None
    if klucz.wektor is not None:
        model = next((s for s, n in NAZWY_MODELI.items() if n == klucz.wektor.model), "")
        ustawienia = cfg["wyglad"].get(model, {})
        if klucz.wektor.wersja == ustawienia.get("wersja_T_w") and "T_w" in ustawienia:
            prog_wygladu = ustawienia["T_w"] - orb["zapas_cos"]

    wyniki = []
    for nazwa, drugi in sorted(indeks.items()):
        if (klucz.sha256 is not None and klucz.sha256 == drugi.sha256) or set(klucz.qr).intersection(drugi.qr):
            wyniki.append(nazwa)
            continue
        hashe = odleglosci_64(klucz.phash, klucz.dhash, drugi.phash, drugi.dhash)
        if pasujaca_para(hashe, h["T_p"] + orb["zapas_hash"], h["T_d"] + orb["zapas_hash"]):
            wyniki.append(nazwa)
            continue
        wyglad = cosinus(klucz.wektor, drugi.wektor)
        if prog_wygladu is not None and wyglad is not None and wyglad >= prog_wygladu:
            wyniki.append(nazwa)
            continue
        if any(a.model in tf and a.wersja == tf[a.model].get("wersja_kalibracji")
               and (ocena := cosinus(a, b)) is not None and ocena >= tf[a.model]["T_f"]
               for a in klucz.wektory_twarzy for b in drugi.wektory_twarzy):
            wyniki.append(nazwa)
    return wyniki
