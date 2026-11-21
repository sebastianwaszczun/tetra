"""Porównanie obrazów i kolejność dowodów."""
from typing import Any

import cv2

from .elementy import na_wejscie
from .hashe import odleglosci, pasujaca_para
from .model import Brak, CechyObrazu, Dowod, Prostokat, WynikPorownania
from .orb import porownaj_orb
from .qr import porownaj_qr
from .twarze import porownaj_modele_twarzy
from .wektor import NAZWY_MODELI, cosinus


def _kolejnosc(cechy: CechyObrazu) -> str:
    return cechy.sha256 or cechy.model_dump_json(exclude={"czasy_ms", "braki", "grupa_podobnych", "jak_obraz"})


def _obszar(cechy: CechyObrazu) -> list[Prostokat]:
    w, h = cechy.rozmiar
    p = cechy.przygotowanie
    return [na_wejscie((0, 0, w, h), p.rozmiar_oryginalny, p.odciete_ramki[:2], p.orientacja_exif)] if w and h else []


def porownaj_cechy(a: CechyObrazu, b: CechyObrazu, cfg: dict[str, Any]) -> WynikPorownania:
    """Ustala jedną kolejność pary, po czym przywraca współrzędne stron wywołania."""
    odwrocona = _kolejnosc(a) > _kolejnosc(b)
    wynik = _porownaj(b, a, cfg) if odwrocona else _porownaj(a, b, cfg)
    if odwrocona:
        for dowod in wynik.dowody:
            dowod.miejsca_a, dowod.miejsca_b = dowod.miejsca_b, dowod.miejsca_a
        for para in wynik.metryki.cos_par_twarzy:
            para.indeks_a, para.indeks_b = para.indeks_b, para.indeks_a
        wynik.metryki.cos_par_twarzy.sort(key=lambda p: (p.indeks_a, p.indeks_b))
        orb = wynik.metryki.orb
        if orb is not None and "pokrycie_a" in orb:
            orb["pokrycie_a"], orb["pokrycie_b"] = orb["pokrycie_b"], orb["pokrycie_a"]
    return wynik


def _porownaj(a: CechyObrazu, b: CechyObrazu, cfg: dict[str, Any]) -> WynikPorownania:
    wynik = WynikPorownania()
    m = wynik.metryki
    m.sha256_rowny = a.sha256.lower() == b.sha256.lower() if a.sha256 and b.sha256 else None
    qr = porownaj_qr(a.qr, b.qr)
    m.wspolne_qr = len(qr)
    identyczny = Dowod(rodzaj="identyczny_plik", wynik=1.0, uzasadnienie="Zgodny skrót SHA-256, 256 bitów",
                      miejsca_a=_obszar(a), miejsca_b=_obszar(b)) if m.sha256_rowny else None
    if a.ubogi_w_tresc or b.ubogi_w_tresc or a.za_maly or b.za_maly:
        wynik.braki.append(Brak(skladnik="porownanie_wizualne", powod="Obraz ubogi w treść lub za mały"))
        wynik.dowody = [identyczny] if identyczny else qr
        return wynik

    progi = cfg["hashe"]
    pomiary = odleglosci(a.hashe, b.hashe)
    duplikat = pasujaca_para(pomiary, progi["T_p"], progi["T_d"]) if progi.get("dowod", True) else None
    if pomiary:
        najlepsza = duplikat or min(pomiary, key=lambda p: (p.phash + p.dhash, p.phash, p.wariant))
        m.phash_min = min(p.phash for p in pomiary)
        m.dhash_min = min(p.dhash for p in pomiary)
        m.wariant_hash = najlepsza.wariant
    else:
        wynik.braki.append(Brak(skladnik="hashe", powod="Brak hashy percepcyjnych obu obrazów"))

    niedostepne = frozenset(a.niedostepne_modele_twarzy + b.niedostepne_modele_twarzy)
    m.cos_twarzy, twarze, rozne_wersje = porownaj_modele_twarzy(a.twarze, b.twarze, cfg["twarze"], niedostepne,
                                                            m.cos_par_twarzy)
    m.cos_twarzy_max = max((v for v in m.cos_twarzy.values() if v is not None), default=None)
    if niedostepne:
        wynik.braki.append(Brak(skladnik="twarze", powod="Niedostępne wagi modeli: " + ", ".join(sorted(niedostepne))))
    if rozne_wersje:
        wynik.braki.append(Brak(skladnik="cos_twarzy", powod="Niezgodna wersja lub kalibracja: " + ", ".join(sorted(rozne_wersje))))

    m.cos_wyglad = cosinus(a.wektor, b.wektor)
    nazwa = a.wektor.model if a.wektor is not None else ""
    sigiel = next((k for k, v in NAZWY_MODELI.items() if v == nazwa), "")
    dopasowanie = cfg["wyglad"].get(sigiel, {})
    prog_wygladu = dopasowanie.get("T_w")
    if m.cos_wyglad is None:
        powod = "Niezgodny model, wersja lub wymiar" if a.wektor is not None and b.wektor is not None else "Brak obu wektorów wyglądu"
        wynik.braki.append(Brak(skladnik="cos_wyglad", powod=powod))
    elif a.wektor.wersja != dopasowanie.get("wersja_T_w"):
        wynik.braki.append(Brak(skladnik="T_w", powod="Brak kalibracji dla wersji wektora wyglądu"))
        prog_wygladu = None

    elementy = dopasowanie.get("elementy", {})
    m.wspolne_elementy = sorted(k for k, v in elementy.items()
                                if k in a.elementy_szablonu and k in b.elementy_szablonu
                                and min(a.elementy_szablonu[k], b.elementy_szablonu[k]) >= v)
    la, lb = ({l.marka: l for l in sorted(c.logo_obraz, key=lambda l: l.pewnosc)} for c in (a, b))
    m.wspolne_logo = sorted(la.keys() & lb.keys())

    if identyczny:
        wynik.dowody.append(identyczny)
        return wynik
    if duplikat:
        wynik.dowody.append(Dowod(
            rodzaj="duplikat_kreacji", wynik=1 - (duplikat.phash + duplikat.dhash) / 128,
            uzasadnienie=f"Zgodne hashe w wariancie {duplikat.wariant}, pHash {duplikat.phash} i dHash {duplikat.dhash} bitów",
            miejsca_a=_obszar(a), miejsca_b=_obszar(b),
            szczegoly={"wariant": duplikat.wariant, "phash": duplikat.phash, "dhash": duplikat.dhash},
        ))
        return wynik

    orb_cfg = cfg["orb"]
    blisko_cos = (m.cos_wyglad is not None and prog_wygladu is not None
                  and m.cos_wyglad >= prog_wygladu - orb_cfg["zapas_cos"])
    blisko_hash = (progi.get("dowod", True)
                   and pasujaca_para(pomiary, progi["T_p"] + orb_cfg["zapas_hash"], progi["T_d"] + orb_cfg["zapas_hash"]))
    if blisko_cos or blisko_hash:
        try:
            m.orb, dowod = porownaj_orb(a, b, orb_cfg)
            if dowod is not None:
                wynik.dowody.append(dowod)
        except cv2.error as blad:
            wynik.braki.append(Brak(skladnik="orb", powod=str(blad)))
    if m.cos_wyglad is not None and prog_wygladu is not None and m.cos_wyglad >= prog_wygladu:
        wynik.dowody.append(Dowod(rodzaj="podobny_wyglad", wynik=max(0.0, m.cos_wyglad),
                                  uzasadnienie=f"Wygląd {nazwa}, cosinus {m.cos_wyglad:.3f} przy progu {prog_wygladu:.3f}",
                                  miejsca_a=_obszar(a), miejsca_b=_obszar(b)))
    niepewne = (set(cfg["szablon"]["pospolite"]) | set(dopasowanie.get("elementy_do_usuniecia", ()))
                | set(dopasowanie.get("elementy_mala_proba", ())))
    wyrozniajace = sorted(set(m.wspolne_elementy) - niepewne)
    if (cfg["szablon"]["dowod_elementow"] and prog_wygladu is not None and m.cos_wyglad is not None
            and len(wyrozniajace) >= cfg["szablon"]["min_wspolne_elementy"]):
        ocena = min(min(a.elementy_szablonu[k], b.elementy_szablonu[k]) for k in wyrozniajace)
        wynik.dowody.append(Dowod(rodzaj="wspolne_elementy_szablonu", wynik=ocena,
                                  uzasadnienie=f"{len(wyrozniajace)} wspólne wyróżniające elementy: {', '.join(wyrozniajace)}",
                                  miejsca_a=_obszar(a), miejsca_b=_obszar(b), szczegoly={"elementy": wyrozniajace}))
    wynik.dowody.extend(twarze)
    if a.wersja_logo != b.wersja_logo and m.wspolne_logo:
        wynik.braki.append(Brak(skladnik="logo_obraz", powod="Różne wersje modelu logo"))
    else:
        for marka in m.wspolne_logo:
            l, p = la[marka], lb[marka]
            wynik.dowody.append(Dowod(rodzaj="wspolne_logo", wynik=min(l.pewnosc, p.pewnosc),
                                      uzasadnienie=f"1 wspólna marka {marka}, pewność od {min(l.pewnosc, p.pewnosc):.3f}",
                                      miejsca_a=[l.prostokat], miejsca_b=[p.prostokat]))
    wynik.dowody.extend(qr)
    return wynik
