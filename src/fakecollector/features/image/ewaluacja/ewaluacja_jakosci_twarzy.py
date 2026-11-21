"""Lokalny pomiar jakości i wpływu filtra na dopasowania twarzy."""
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from math import floor
from pathlib import Path
from statistics import mean, median
from time import perf_counter
from typing import Any

import cv2
import numpy as np
from PIL import Image
from scipy.stats import rankdata

from ..konfiguracja import wczytaj_konfiguracje
from ..magface import norma_magface, skaluj_norme
from ..model import Prostokat, Wektor
from ..przygotowanie import czytaj_bajty, przygotuj_obraz
from ..referencje_twarzy import Baza, decyzja_osoby, ranking_osob, wczytaj_probki
from ..twarze import (MODEL_TWARZY, Detekcja, Macierz, Progi, Silnik, _pole, analizuj_twarze, jakosc_twarzy,
                     silnik_twarzy, wykryj_detekcje, zmierz_twarz, zgodnosc_glosow)
from ..wzorzec import otworz_obraz, plik_danych, wczytaj_kreacje

METODY = ("zastepcza", "magface_skladnik", "magface_calosc")
SERIE = {
    "rozmycie": ("czysty", "sigma_1", "sigma_2", "sigma_4"),
    "pomniejszenie": ("czysty", "50%", "25%"),
    "jpeg": ("czysty", "q30", "q10"),
    "przyciecie": ("czysty", "ciecie_20%"),
}


@dataclass(frozen=True)
class Oceny:
    wektor: Wektor | None
    jakosc: dict[str, float]
    czas_ms: dict[str, float]
    skladowe: tuple[float, int, float, float]
    norma_magface: float


@dataclass(frozen=True)
class ProbaJakosci:
    wariant: str
    osoba: str | None
    zbior: str
    pomiary: dict[str, Oceny]
    bazy: dict[str, Baza]


def _odsun(det: Detekcja, x: int, y: int) -> Detekcja:
    lewy, gora, w, h = det.prostokat
    return Detekcja((lewy - x, gora - y, w, h), det.punkty - (x, y), det.pewnosc)


def _warianty(raster: Macierz, det: Detekcja, pole: Prostokat) -> Iterator[tuple[str, Macierz, Detekcja]]:
    x, y, w, h = pole
    brzegi = round(max(w, h) / 4)
    lewy, gora = max(0, x - brzegi), max(0, y - brzegi)
    wycinek = raster[gora:y + h + brzegi, lewy:x + w + brzegi]
    lokalna = _odsun(det, lewy, gora)
    yield "czysty", raster, det
    for sigma in (1, 2, 4):
        yield f"sigma_{sigma}", cv2.GaussianBlur(wycinek, (0, 0), sigma), lokalna
    for procent in (50, 25):
        wys, szer = wycinek.shape[:2]
        nowa_szer, nowa_wys = max(1, round(szer * procent / 100)), max(1, round(wys * procent / 100))
        mniejszy = cv2.resize(wycinek, (nowa_szer, nowa_wys), interpolation=cv2.INTER_AREA)
        yield f"{procent}%", mniejszy, lokalna.na_skale(nowa_szer / szer, nowa_wys / wys)
    for jakosc in (30, 10):
        ok, jpeg = cv2.imencode(".jpg", wycinek, [cv2.IMWRITE_JPEG_QUALITY, jakosc])
        if not ok:
            raise ValueError("Nie udało się zakodować wariantu JPEG")
        yield f"q{jakosc}", cv2.imdecode(jpeg, cv2.IMREAD_COLOR), lokalna
    ciecie = x - lewy + round(w / 5)
    yield "ciecie_20%", wycinek[:, ciecie:], _odsun(lokalna, ciecie, 0)


def _ocen(raster: Macierz, det: Detekcja, sigiel: str, silnik: Silnik, cfg: Progi) -> Oceny:
    pole = _pole(det, raster.shape[1], raster.shape[0])
    if pole is None:
        raise ValueError("Wariant utracił całą ramkę twarzy")
    pomiar = zmierz_twarz(silnik, sigiel, raster, det)
    skladowe = pomiar.norma, min(pole[2:]), det.pewnosc, pomiar.ostrosc
    argumenty = *skladowe, cfg[sigiel]
    start = perf_counter()
    zastepcza = jakosc_twarzy(*argumenty, cfg)
    czas_zastepczej = (perf_counter() - start) * 1000
    start = perf_counter()
    norma = norma_magface(pomiar.wyrownana)
    czas_magface = (perf_counter() - start) * 1000
    magface = skaluj_norme(norma, cfg["magface"])
    hybryda = jakosc_twarzy(*argumenty, {**cfg, "magface_tylko_norma": True}, magface)
    return Oceny(pomiar.wektor, dict(zip(METODY, (zastepcza, hybryda, magface), strict=True)),
                 {"zastepcza": czas_zastepczej, "magface": czas_magface}, skladowe, norma)


def _spearman(oceny: list[float]) -> float | None:
    rangi = rankdata(oceny)
    if np.ptp(rangi) == 0:
        return None
    return float(np.corrcoef(np.arange(len(oceny)), rangi)[0, 1])


def _status(nazwa: str | None) -> tuple[str | None, str]:
    return nazwa, "rozpoznana" if nazwa is not None else "nieznana"


def _sprawdz_glosy(pomiary: dict[str, Oceny], bazy: dict[str, Baza], tryb: str, cfg: Progi,
                   dopuszczone: set[str] | None = None) -> dict[str, tuple[str | None, str]]:
    glosy = {}
    for sigiel, pomiar in pomiary.items():
        progi = cfg[sigiel]
        wektor = pomiar.wektor
        poprzedni_prog = progi.get("prog_jakosci_zastepcza", progi["prog_jakosci"])
        if dopuszczone is not None:
            zalicz = sigiel in dopuszczone
        else:
            zalicz = tryb == "bez_filtra" or pomiar.jakosc[tryb] >= poprzedni_prog
        glosy[sigiel] = decyzja_osoby(ranking_osob(wektor, bazy[sigiel]), progi["T_osoba"], progi["margines"]) if wektor and zalicz else None
    return {**{sigiel: _status(nazwa) for sigiel, nazwa in glosy.items()},
            "oba": zgodnosc_glosow(glosy["buffalo_l"], glosy["yunet_sface"])}


def _podzbior(indeks: dict[tuple[str, str], Wektor], klucz: tuple[str, str] | None, obca: bool) -> Baza:
    baza: Baza = {}
    for k, wektor in indeks.items():
        if k != klucz and (not obca or klucz is None or k[0] != klucz[0]):
            baza.setdefault(k[0], []).append(wektor)
    return baza


def _podsumuj(wyniki: list[tuple[str | None, str | None, str]]) -> dict[str, int]:
    return {
        "proby": len(wyniki), "trafne": sum(prawda == nazwisko and status != "niepewne" for prawda, nazwisko, status in wyniki),
        "falszywe": sum(nazwisko is not None and nazwisko != prawda for prawda, nazwisko, _ in wyniki),
        "niepewne": sum(status == "niepewne" for _, _, status in wyniki),
    }


def _wspolny_pomiar(pomiary: dict[str, Oceny], cfg: Progi) -> Oceny:
    # Przy udanym kodowaniu używamy pierwszych dostępnych punktów, jak w analizuj_twarze.
    dostepne = (p for p in pomiary.values() if p.wektor is not None and p.skladowe[1] >= cfg["min_bok_px"])
    return next(dostepne, pomiary["buffalo_l"])


def _skalibruj(proby: list[ProbaJakosci], cfg: Progi) -> dict[str, Any]:
    """Dobiera najwyższe progi zachowujące wszystkie oryginały referencyjne."""
    znane = [p for p in proby if p.osoba is not None]
    czyste = [p for p in znane if p.wariant == "czysty"]
    if not czyste:
        raise ValueError("Kalibracja jakości wymaga czytelnych oryginałów referencyjnych")
    percentyle = cfg["magface"]["percentyle_calosci"]
    granice = np.percentile([_wspolny_pomiar(p.pomiary, cfg).norma_magface for p in znane], percentyle)
    if granice[0] == granice[1]:
        raise ValueError("Normy MagFace nie pozwalają wyznaczyć skali z percentyli")
    skala = dict(zip(("norma_min", "norma_max"), map(float, granice), strict=True))
    nowy_cfg = {**cfg, "magface_tylko_norma": True}
    zepsute = [p for p in znane if p.wariant != "czysty"]
    wspolne = [_wspolny_pomiar(p.pomiary, cfg) for p in zepsute]

    def ocen_skladnik(w: Oceny, wspolny: Oceny, progi: Progi) -> float:
        norma, bok, pewnosc, _ = w.skladowe
        magface = skaluj_norme(wspolny.norma_magface, cfg["magface"])
        return jakosc_twarzy(norma, bok, pewnosc, wspolny.skladowe[3], progi, nowy_cfg, magface)

    def prog(wartosci: list[float]) -> float:
        return floor(min(wartosci) * 1_000_000) / 1_000_000

    kandydaci = {}
    for s in MODEL_TWARZY:
        ostrosci = [p.pomiary[s].skladowe[3] for p in czyste]
        kandydaci[s] = []
        for percentyl in cfg["percentyle_ostrosci_dobrej"]:
            ostrosc = float(np.percentile(ostrosci, percentyl))
            if ostrosc <= 0:
                continue
            progi = {**cfg[s], "ostrosc_dobrej": ostrosc}
            wyniki_czystych = [ocen_skladnik(p.pomiary[s], w, progi) for p in czyste
                               for w in (_wspolny_pomiar(p.pomiary, cfg), p.pomiary[s])]
            prog_kandydata = prog(wyniki_czystych)
            odciete = [p.wariant for p, w in zip(zepsute, wspolne, strict=True)
                       if p.pomiary[s].skladowe[1] >= cfg["min_bok_px"]
                       and ocen_skladnik(p.pomiary[s], w, progi) < prog_kandydata]
            mocne = sum(nazwa in {"sigma_4", "q10"} for nazwa in odciete)
            kandydaci[s].append({"percentyl": percentyl, "ostrosc_dobrej": ostrosc, "prog_jakosci": prog_kandydata,
                                 "odciete_mocne": mocne, "odciete_pozostale": len(odciete) - mocne})
        if not kandydaci[s]:
            raise ValueError(f"{s}: oryginały mają za małą ostrość do kalibracji")

    wybrane = {s: max(w, key=lambda k: (k["odciete_mocne"], -k["odciete_pozostale"]))
               for s, w in kandydaci.items()}
    progi_modeli = {s: {**cfg[s], **w} for s, w in wybrane.items()}
    prog_skladnika = {s: w["prog_jakosci"] for s, w in wybrane.items()}

    def oceny(p: ProbaJakosci) -> tuple[dict[str, float], float]:
        wspolny = _wspolny_pomiar(p.pomiary, cfg)
        skladowe = {s: ocen_skladnik(w, wspolny, progi_modeli[s]) for s, w in p.pomiary.items()}
        return skladowe, skaluj_norme(wspolny.norma_magface, skala)

    oceny_czystych = [oceny(p) for p in czyste]
    prog_calosci = prog([ocena for _, ocena in oceny_czystych])
    warianty = defaultdict(lambda: defaultdict(int))
    dopasowania = defaultdict(list)
    for p in proby:
        skladnik, calosc = oceny(p)
        poprzednie = {m: _sprawdz_glosy(p.pomiary, p.bazy, m, cfg)["oba"]
                      for m in ("zastepcza", "magface_skladnik")}
        rozmiar = {s for s, w in p.pomiary.items() if w.skladowe[1] >= cfg["min_bok_px"]}
        for metoda, ocena, progi in (
            ("skladnik", skladnik, prog_skladnika),
            ("calosc", dict.fromkeys(MODEL_TWARZY, calosc), dict.fromkeys(MODEL_TWARZY, prog_calosci)),
        ):
            zaliczone = {s for s in rozmiar if ocena[s] >= progi[s]}
            filtrowane = rozmiar - zaliczone
            glosy = _sprawdz_glosy(p.pomiary, p.bazy, "bez_filtra", cfg, zaliczone)
            for model, (osoba, status) in glosy.items():
                dopasowania[p.zbior, metoda, model].append((p.osoba, osoba, status))
            if p.osoba is None:
                continue
            osoba, status = glosy["oba"]
            w = warianty[p.wariant, metoda]
            w["probki"] += 1
            w["odciete_rozmiarem"] += len(rozmiar) < len(MODEL_TWARZY)
            w["odciete_jakoscia"] += bool(filtrowane)
            w["odciete_oba_jakoscia"] += len(filtrowane) == len(MODEL_TWARZY)
            w["odciete_arcface"] += "buffalo_l" in filtrowane
            w["odciete_sface"] += "yunet_sface" in filtrowane
            w["trafne"] += osoba == p.osoba
            w["niepewne"] += status == "niepewne"
            w["bez_nazwiska"] += osoba is None
            w["bledne_nazwiska"] += osoba is not None and osoba != p.osoba
            for stara_metoda, (stare, stary_status) in poprzednie.items():
                w[f"odciete_{stara_metoda}_niepewne"] += bool(filtrowane) and stary_status == "niepewne"
                w[f"odciete_{stara_metoda}_chybione"] += bool(filtrowane) and stare is not None and stare != p.osoba
                w[f"odciete_{stara_metoda}_nieznane"] += bool(filtrowane) and stary_status == "nieznana"
                w[f"odciete_{stara_metoda}_trafne"] += bool(filtrowane) and stare == p.osoba

    return {"prog_skladnika": prog_skladnika, "calosc": {**skala, "prog_jakosci": prog_calosci},
            "ostrosc_dobrej": {s: w["ostrosc_dobrej"] for s, w in wybrane.items()},
            "percentyl_ostrosci_dobrej": {s: w["percentyl"] for s, w in wybrane.items()},
            "kandydaci_ostrosci": kandydaci,
            "percentyle_calosci": percentyle, "normy_do_percentyli": len(znane),
            "warianty": [{"wariant": w, "metoda": m, **liczby} for (w, m), liczby in warianty.items()],
            "dopasowania": [{"zbior": z, "metoda": m, "model": s, **_podsumuj(wyniki)}
                            for (z, m, s), wyniki in dopasowania.items()]}


def ewaluuj_jakosc_twarzy(dane: Path, nieznane: tuple[str, ...], uwzglednij_szkice: bool) -> dict[str, Any]:
    """Porównuje jakość przy tych samych detekcjach, embeddingach i progach nazwiska."""
    konfiguracja = wczytaj_konfiguracje()
    cfg = konfiguracja["twarze"]
    silniki = {s: silnik_twarzy(s) for s in MODEL_TWARZY}
    katalog = (dane / "reference_people").resolve()
    indeksy = {s: wczytaj_probki(s, katalog) for s in silniki}
    klucze = sorted(set().union(*indeksy.values()))
    braki = []
    korelacje = defaultdict(list)
    czasy = defaultdict(list)
    dopasowania = defaultdict(list)
    proby = []
    # Ładowanie i pierwszy przebieg CPU pozostają poza pomiarem czasu.
    sztuczny = np.zeros((112, 112, 3), np.uint8)
    norma_magface(sztuczny)

    def probka(obraz: Image.Image, warianty: bool, etykieta: str) -> dict[str, dict[str, Oceny]]:
        raster = cv2.cvtColor(np.asarray(obraz), cv2.COLOR_RGB2BGR)
        pomiary: dict[str, dict[str, Oceny]] = {}
        for sigiel, silnik in silniki.items():
            with silnik.blokada:
                detekcje = wykryj_detekcje(silnik, raster, cfg)
                if len(detekcje) != 1:
                    braki.append(f"{etykieta}, {sigiel}: wykryto {len(detekcje)} twarzy, potrzebna jedna")
                    continue
                det, pole = detekcje[0]
                seria = _warianty(raster, det, pole) if warianty else (("czysty", raster, det),)
                pomiary[sigiel] = {nazwa: _ocen(r, d, sigiel, silnik, cfg) for nazwa, r, d in seria}
        return pomiary

    def ocen_dopasowanie(pomiary: dict[str, dict[str, Oceny]], bazy: dict[str, Baza], prawda: str | None,
                         zbior: str, wariant: str) -> None:
        dla_wariantu = {s: p[wariant] for s, p in pomiary.items()}
        proby.append(ProbaJakosci(wariant, prawda, zbior, dla_wariantu, bazy))
        for metoda in ("bez_filtra", *METODY):
            for sigiel, (nazwa, status) in _sprawdz_glosy(dla_wariantu, bazy, metoda, cfg).items():
                dopasowania[zbior, metoda, sigiel].append((prawda, nazwa, status))

    przyjete = 0
    for klucz in klucze:
        osoba, rel = klucz
        sciezka = (katalog / rel).resolve()
        if not sciezka.is_relative_to(katalog):
            raise ValueError("Ścieżka referencji poza katalogiem bazy")
        obraz, _ = przygotuj_obraz(czytaj_bajty(sciezka, konfiguracja["wczytywanie"]["maks_bajtow"]))
        if obraz is None:
            braki.append(f"{rel}: brak czytelnego obrazu")
            continue
        pomiary = probka(obraz, True, rel)
        if set(pomiary) != set(silniki):
            continue
        przyjete += 1
        for sigiel, wpisy in pomiary.items():
            for ocena in wpisy.values():
                for metoda, czas in ocena.czas_ms.items():
                    czasy[metoda].append(czas)
            for seria, warianty in SERIE.items():
                for metoda in METODY:
                    rho = _spearman([wpisy[w].jakosc[metoda] for w in warianty])
                    if rho is not None:
                        korelacje[sigiel, seria, metoda].append(rho)
        baz_loo = {s: _podzbior(i, klucz, False) for s, i in indeksy.items()}
        baz_obca = {s: _podzbior(i, klucz, True) for s, i in indeksy.items()}
        for wariant in pomiary["buffalo_l"]:
            zbior = "czyste" if wariant == "czysty" else "zepsute"
            ocen_dopasowanie(pomiary, baz_loo, osoba, zbior, wariant)
            ocen_dopasowanie(pomiary, baz_obca, None, f"symulowane_{zbior}", wariant)

    bazy = {s: _podzbior(i, None, False) for s, i in indeksy.items()}
    kontrole = []
    kreacje = []
    if (dane / "wzorzec/kreacje.json").is_file():
        for k in wczytaj_kreacje(dane).kreacje:
            if k.status != "zatwierdzone" and not uwzglednij_szkice:
                continue
            x, y, w, h = k.prostokat_kreacji
            wycinek = otworz_obraz(plik_danych(dane, k.plik)).crop((x, y, x + w, y + h))
            obraz, cechy = przygotuj_obraz(wycinek)
            if obraz is None:
                continue
            kreacje.append((k.id, obraz, cechy))
            if k.id not in nieznane:
                continue
            pomiary = probka(obraz, False, k.id)
            if set(pomiary) == set(silniki):
                ocen_dopasowanie(pomiary, bazy, None, "kontrole", "czysty")
                kontrole.append(k.id)
    braki.extend(f"Brak pomiaru kontroli {k}" for k in nieznane if k not in kontrole)
    kalibracja = _skalibruj(proby, cfg)
    konfiguracja_pomiaru = {**cfg, "jakosc": "magface", "magface_tylko_norma": True}
    for sigiel, prog in kalibracja["prog_skladnika"].items():
        progi = cfg[sigiel]
        konfiguracja_pomiaru[sigiel] = {**progi, "prog_jakosci": prog, "ostrosc_dobrej": kalibracja["ostrosc_dobrej"][sigiel],
                                        "prog_jakosci_zastepcza": progi.get("prog_jakosci_zastepcza", progi["prog_jakosci"])}
    czasy_kreacji = []
    for identyfikator, obraz, cechy in kreacje:
        analizuj_twarze(obraz, cechy, cfg=konfiguracja_pomiaru, opisy=False)
        czasy_kreacji.append({"id": identyfikator, "twarze": len(cechy.twarze),
                              "magface_ms": cechy.czasy_ms.get("magface", 0.0), "twarze_ms": cechy.czasy_ms["twarze"]})

    return {
        "referencje": przyjete, "kontrole": sorted(kontrole), "braki": braki, "warianty": SERIE,
        "kalibracja_jakosci": kalibracja, "czasy_kreacji_ms": czasy_kreacji,
        "korelacje": [{"model": s, "seria": rodzaj, "metoda": m, "rho": mean(r) if r else None, "twarze": len(r)}
                      for s in silniki for rodzaj in SERIE for m in METODY if (r := korelacje[s, rodzaj, m]) or przyjete],
        "czas_ms": {metoda: {"mediana": median(t), "srednia": mean(t), "pomiarow": len(t)} for metoda, t in czasy.items()},
        "dopasowania": [{"zbior": z, "metoda": m, "model": s, **_podsumuj(wyniki)}
                        for (z, m, s), wyniki in dopasowania.items()],
    }
