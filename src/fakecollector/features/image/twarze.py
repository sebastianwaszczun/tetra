"""Wykrywanie twarzy i lokalne wektory InsightFace lub YuNet + SFace."""
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from hashlib import file_digest
from math import ceil, floor
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any, Literal, Protocol, TypeVar

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from .elementy import na_wejscie
from .konfiguracja import wczytaj_konfiguracje
from .magface import ocena_magface
from .model import Brak, CechyObrazu, CosinusyParyTwarzy, Dowod, Prostokat, Twarz, Wektor
from .opisy_twarzy import opisz_twarze
from .wektor import cosinus

# InsightFace importuje Albumentations, które domyślnie sprawdza aktualizacje przez sieć.
os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"

MODEL_TWARZY = {"buffalo_l": "InsightFace buffalo_l / ArcFace", "yunet_sface": "OpenCV YuNet / SFace"}
KATALOG_MODELI = Path("/models/obraz")
SciezkiWag = dict[str, Path]
Macierz = NDArray[np.generic]
Progi = Mapping[str, Any]


@dataclass(frozen=True)
class Detekcja:
    prostokat: tuple[float, float, float, float]
    punkty: Macierz
    pewnosc: float

    def na_skale(self, skala_x: float, skala_y: float) -> "Detekcja":
        x, y, w, h = self.prostokat
        return Detekcja((x * skala_x, y * skala_y, w * skala_x, h * skala_y),
                        self.punkty * (skala_x, skala_y), self.pewnosc)


class Silnik(Protocol):
    wersja: str
    blokada: Any

    def wykryj(self, raster: Macierz) -> list[Detekcja]: ...
    def koduj(self, raster: Macierz, det: Detekcja, wyrownana: Macierz) -> Macierz: ...


class _Buffalo:
    def __init__(self, wagi: SciezkiWag, wersja: str, progi: Progi, wspolne: Progi) -> None:
        import onnxruntime as ort
        from insightface.model_zoo.arcface_onnx import ArcFaceONNX
        from insightface.model_zoo.scrfd import SCRFD

        opcje = ort.SessionOptions()
        opcje.intra_op_num_threads = wspolne["watki_onnx"]
        opcje_detektora = ort.SessionOptions()
        opcje_detektora.intra_op_num_threads = wspolne["watki_onnx"]
        # SCRFD deklaruje wyjście dla 640×640 mimo wejścia o zmiennym rozmiarze.
        opcje_detektora.log_severity_level = 3
        self.wersja = wersja
        self.blokada = Lock()
        try:
            detektor = ort.InferenceSession(str(wagi["det_10g.onnx"]), sess_options=opcje_detektora,
                                            providers=["CPUExecutionProvider"])
            rozpoznawanie = ort.InferenceSession(str(wagi["w600k_r50.onnx"]), sess_options=opcje,
                                                  providers=["CPUExecutionProvider"])
        except (ort.capi.onnxruntime_pybind11_state.Fail, ort.capi.onnxruntime_pybind11_state.InvalidProtobuf,
                ort.capi.onnxruntime_pybind11_state.InvalidArgument) as blad:
            raise RuntimeError(f"Nie udało się załadować lokalnych wag buffalo_l: {blad}") from blad
        self.detektor = SCRFD(str(wagi["det_10g.onnx"]), detektor)
        self.detektor.prepare(0, det_thresh=progi["prog_detekcji"], nms_thresh=wspolne["prog_nms"])
        self.rozpoznawanie = ArcFaceONNX(str(wagi["w600k_r50.onnx"]), rozpoznawanie)

    def wykryj(self, raster: Macierz) -> list[Detekcja]:
        h, w = raster.shape[:2]
        # Głowy SCRFD działają na siatkach o kroku do 32 px.
        wejscie = ceil(w / 32) * 32, ceil(h / 32) * 32
        prostokaty, punkty = self.detektor.detect(raster, input_size=wejscie)
        if punkty is None:
            return []
        return [Detekcja((float(x), float(y), float(x2 - x), float(y2 - y)), p, float(wynik))
                for (x, y, x2, y2, wynik), p in zip(prostokaty, punkty, strict=True)]

    def koduj(self, raster: Macierz, det: Detekcja, wyrownana: Macierz) -> Macierz:
        return self.rozpoznawanie.get_feat(wyrownana).ravel()


class _YuNetSFace:
    def __init__(self, wagi: SciezkiWag, wersja: str, progi: Progi, wspolne: Progi) -> None:
        detektor = "face_detection_yunet_2026may.onnx"
        self.detektor = cv2.FaceDetectorYN.create(str(wagi[detektor]), "", (320, 320),
                                                progi["prog_detekcji"], wspolne["prog_nms"])
        self.rozpoznawanie = cv2.FaceRecognizerSF.create(str(wagi["face_recognition_sface_2021dec.onnx"]), "")
        self.wersja = wersja
        self.blokada = Lock()

    def wykryj(self, raster: Macierz) -> list[Detekcja]:
        h, w = raster.shape[:2]
        self.detektor.setInputSize((w, h))
        _, twarze = self.detektor.detect(raster)
        if twarze is None:
            return []
        return [Detekcja(tuple(map(float, t[:4])), t[4:14].reshape(5, 2), float(t[14])) for t in twarze]

    def koduj(self, raster: Macierz, det: Detekcja, wyrownana: Macierz) -> Macierz:
        wiersz = np.array([*det.prostokat, *det.punkty.ravel(), det.pewnosc], dtype=np.float32)
        sface = self.rozpoznawanie.alignCrop(raster, wiersz)
        return self.rozpoznawanie.feature(sface).ravel()


_BLOKADA = Lock()


def silnik_twarzy(sigiel: str) -> Silnik:
    """Ładuje lokalne sesje raz na proces."""
    with _BLOKADA:
        return _wczytaj_silnik(sigiel)


@cache
def _wczytaj_silnik(sigiel: str) -> Silnik:
    manifest = json.loads((KATALOG_MODELI / f"{sigiel}.json").read_text(encoding="utf-8"))
    katalog = Path(manifest["sciezka"]).resolve()
    wagi = {}
    for nazwa, oczekiwany in manifest["sha256_plikow"].items():
        plik = (katalog / nazwa).resolve()
        if not plik.is_relative_to(katalog) or not plik.is_relative_to("/models"):
            raise ValueError("Wagi twarzy muszą znajdować się w /models")
        with plik.open("rb") as dane:
            if file_digest(dane, "sha256").hexdigest() != oczekiwany:
                raise ValueError(f"Niezgodna suma SHA-256 wag {nazwa}")
        wagi[nazwa] = plik
    cfg = wczytaj_konfiguracje()["twarze"]
    typ = {"buffalo_l": _Buffalo, "yunet_sface": _YuNetSFace}[sigiel]
    return typ(wagi, manifest["wersja"], cfg[sigiel], cfg)


def _ksztalt(det: Detekcja) -> float:
    return det.prostokat[2] * det.prostokat[3]


def _iou_prostokatow(a: Sequence[float], b: Sequence[float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    pole = max(0., min(ax + aw, bx + bw) - max(ax, bx)) * max(0., min(ay + ah, by + bh) - max(ay, by))
    return pole / (aw * ah + bw * bh - pole)


def _iou(a: Detekcja, b: Detekcja) -> float:
    return _iou_prostokatow(a.prostokat, b.prostokat)


def _scal(detekcje: Sequence[Detekcja], prog: float) -> list[Detekcja]:
    scalone: list[Detekcja] = []
    for det in sorted(detekcje, key=lambda d: -d.pewnosc):
        if all(_iou(det, poprzednia) < prog for poprzednia in scalone):
            scalone.append(det)
    return sorted(scalone, key=lambda d: -_ksztalt(d))


def _przebieg(silnik: Silnik, raster: Macierz, bok: int) -> list[Detekcja]:
    h, w = raster.shape[:2]
    skala = bok / max(w, h)
    nowy_w, nowy_h = max(1, round(w * skala)), max(1, round(h * skala))
    zmniejszony = raster if (w, h) == (nowy_w, nowy_h) else cv2.resize(
        raster, (nowy_w, nowy_h), interpolation=cv2.INTER_AREA if skala < 1 else cv2.INTER_CUBIC,
    )
    return [d.na_skale(w / nowy_w, h / nowy_h) for d in silnik.wykryj(zmniejszony)
            if d.prostokat[2] > 0 and d.prostokat[3] > 0]


def _pole(det: Detekcja, w: int, h: int) -> Prostokat | None:
    x, y, szer, wys = det.prostokat
    lewy, gora = max(0, floor(x)), max(0, floor(y))
    prawy, dol = min(w, ceil(x + szer)), min(h, ceil(y + wys))
    if prawy <= lewy or dol <= gora:
        return None
    return lewy, gora, prawy - lewy, dol - gora


def jakosc_twarzy(norma: float, wielkosc: int, pewnosc: float, ostrosc: float,
                  progi_modelu: Progi, progi: Progi, magface: float | None = None) -> float:
    """Zwraca ocenę MagFace lub średnią czterech składników w skali 0–1."""
    if magface is not None and not progi["magface_tylko_norma"]:
        return magface
    norma_w_skali = magface if magface is not None else norma / progi_modelu["norma_dobrej"]
    skladowe = (norma_w_skali, wielkosc / progi["bok_dobrej_px"], pewnosc,
                ostrosc / progi_modelu["ostrosc_dobrej"])
    return float(np.clip(skladowe, 0, 1).mean())


@dataclass(frozen=True)
class PomiarTwarzy:
    norma: float
    ostrosc: float
    wektor: Wektor | None
    wyrownana: Macierz


@dataclass(frozen=True)
class WykrytaTwarz:
    detekcja: Detekcja
    prostokat: Prostokat


def wyrownaj_twarz(raster: Macierz, punkty: Macierz) -> Macierz:
    """Wyznacza wspólny wycinek ArcFace 112 × 112 z pięciu punktów."""
    from insightface.utils.face_align import norm_crop

    return norm_crop(raster, landmark=np.asarray(punkty, dtype=np.float32), image_size=112)


def ostrosc_twarzy(wyrownana: Macierz) -> float:
    """Liczy wariancję Laplasjanu na wyrównanym wycinku twarzy."""
    szary = cv2.cvtColor(wyrownana, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(szary, cv2.CV_64F).var())


def zmierz_twarz(silnik: Silnik, sigiel: str, raster: Macierz, det: Detekcja,
                 wspolny: PomiarTwarzy | None = None) -> PomiarTwarzy:
    """Mierzy ostrość i oblicza wektor rozpoznawania przed filtrem jakości."""
    wyrownana = wspolny.wyrownana if wspolny is not None else wyrownaj_twarz(raster, det.punkty)
    ostrosc = wspolny.ostrosc if wspolny is not None else ostrosc_twarzy(wyrownana)
    try:
        surowy = silnik.koduj(raster, det, wyrownana)
    except cv2.error:
        return PomiarTwarzy(0.0, ostrosc, None, wyrownana)
    norma = float(np.linalg.norm(surowy))
    if not np.isfinite(norma) or norma <= 0:
        return PomiarTwarzy(0.0, ostrosc, None, wyrownana)
    wektor = Wektor(model=MODEL_TWARZY[sigiel], wersja=silnik.wersja,
                    wymiar=surowy.size, wartosci=(surowy / norma).tolist())
    return PomiarTwarzy(norma, ostrosc, wektor, wyrownana)


def _jakosc_magface(wyrownana: Macierz, cechy: CechyObrazu, cfg: Progi) -> float | None:
    if cfg.get("jakosc") != "magface" or any(brak.skladnik == "magface" for brak in cechy.braki):
        return None
    start = perf_counter()
    try:
        skala = cfg["magface"] if cfg["magface_tylko_norma"] else cfg["magface"]["calosc"]
        return ocena_magface(wyrownana, skala)
    except (OSError, RuntimeError, ImportError, KeyError, ValueError, cv2.error) as blad:
        cechy.braki.append(Brak(skladnik="magface", powod=f"Użyto miary zastępczej: {blad}"))
        return None
    finally:
        cechy.czasy_ms["magface"] = cechy.czasy_ms.get("magface", 0.0) + (perf_counter() - start) * 1000


def _prog_jakosci(cfg: Progi, sigiel: str, magface: float | None) -> float:
    progi = cfg[sigiel]
    if magface is None:
        return progi.get("prog_jakosci_zastepcza", progi["prog_jakosci"])
    if cfg["magface_tylko_norma"]:
        return progi["prog_jakosci"]
    return cfg["magface"]["calosc"]["prog_jakosci"]


def _cecha(pomiar: PomiarTwarzy, sigiel: str, kandydat: WykrytaTwarz, obraz: Image.Image,
           cechy: CechyObrazu, cfg: Progi, magface: float | None) -> Twarz:
    det, pole = kandydat.detekcja, kandydat.prostokat
    wielkosc = min(pole[2:])
    oryginal = cechy.przygotowanie.rozmiar_oryginalny
    offset = cechy.przygotowanie.odciete_ramki[:2]
    orientacja = obraz.info.get("orientacja_exif", 1)
    punkty = []
    for px, py in det.punkty:
        ix = max(0, min(obraz.width - 1, round(float(px))))
        iy = max(0, min(obraz.height - 1, round(float(py))))
        punkty.append(na_wejscie((ix, iy, 1, 1), oryginal, offset, orientacja)[:2])
    jakosc = jakosc_twarzy(pomiar.norma, wielkosc, det.pewnosc, pomiar.ostrosc, cfg[sigiel], cfg, magface)
    wektor = pomiar.wektor if jakosc >= _prog_jakosci(cfg, sigiel, magface) else None
    miara = "zastepcza" if magface is None else "skladnik" if cfg["magface_tylko_norma"] else "calosc"
    return Twarz(prostokat=na_wejscie(pole, oryginal, offset, orientacja), punkty=punkty,
                 pewnosc_wykrycia=det.pewnosc, wielkosc_px=wielkosc, wektor=wektor, jakosc=jakosc, miara_jakosci=miara)


def wykryj_detekcje(silnik: Silnik, raster: Macierz, cfg: Progi) -> list[tuple[Detekcja, Prostokat]]:
    """Scala oba przebiegi detektora i zwraca największe wykrycia w rastrze BGR."""
    h, w = raster.shape[:2]
    bok = min(max(w, h), cfg["bok_detekcji_px"])
    ze_slaba_twarza = cfg["min_bok_px"] * cfg["powiekszenie"]
    detekcje = _przebieg(silnik, raster, bok)
    if (not detekcje or bok < cfg["bok_detekcji_px"]
            or any(min(d.prostokat[2:]) < ze_slaba_twarza for d in detekcje)):
        detekcje.extend(_przebieg(silnik, raster, bok * cfg["powiekszenie"]))
    poprawne = [(d, pole) for d in _scal(detekcje, cfg["prog_nms"])
                if (pole := _pole(d, w, h)) is not None and min(pole[2:]) >= cfg["min_bok_px"]]
    poprawne.sort(key=lambda p: -(p[1][2] * p[1][3]))
    return poprawne[:cfg["maks_liczba"]]


def _kandydaci(silnik: Silnik, raster: Macierz, cfg: Progi) -> list[WykrytaTwarz]:
    if min(raster.shape[:2]) < cfg["min_bok_px"]:
        return []
    with silnik.blokada:
        return [WykrytaTwarz(d, p) for d, p in wykryj_detekcje(silnik, raster, cfg)]


def wykryj_twarze(obraz: Image.Image, cechy: CechyObrazu, sigiel: str, silnik: Silnik | None = None) -> list[Twarz]:
    """Wykrywa twarze, scala dwa przebiegi i opisuje największe wykrycia."""
    cfg = wczytaj_konfiguracje()["twarze"]
    if min(obraz.size) < cfg["min_bok_px"]:
        return []
    if silnik is None:
        silnik = silnik_twarzy(sigiel)
    raster = cv2.cvtColor(np.asarray(obraz), cv2.COLOR_RGB2BGR)
    twarze = []
    for k in _kandydaci(silnik, raster, cfg):
        with silnik.blokada:
            pomiar = zmierz_twarz(silnik, sigiel, raster, k.detekcja)
        magface = _jakosc_magface(pomiar.wyrownana, cechy, cfg) if pomiar.wektor is not None else None
        twarze.append(_cecha(pomiar, sigiel, k, obraz, cechy, cfg, magface))
    return twarze


def zgodnosc_glosow(a: str | None, b: str | None) -> tuple[str | None, Literal["rozpoznana", "nieznana", "niepewne"]]:
    """Potwierdza nazwisko tylko dwoma zgodnymi głosami."""
    if a and a == b:
        return a, "rozpoznana"
    return None, "niepewne" if a or b else "nieznana"


class ZProstokatem(Protocol):
    @property
    def prostokat(self) -> Prostokat: ...


Z = TypeVar("Z", bound=ZProstokatem)


def pary_detekcji(a: Sequence[Z], b: Sequence[Z], prog: float) -> list[tuple[Z | None, Z | None]]:
    """Paruje ramki niezależnych detektorów, każdą najwyżej raz."""
    wyniki = sorted(((_iou_prostokatow(x.prostokat, y.prostokat), i, j)
                    for i, x in enumerate(a) for j, y in enumerate(b)), reverse=True)
    pary = {}
    uzyte = set()
    for iou, i, j in wyniki:
        if iou < prog:
            break
        if i not in pary and j not in uzyte:
            pary[i] = j
            uzyte.add(j)
    return [(t, b[pary[i]] if i in pary else None) for i, t in enumerate(a)] + [
        (None, t) for j, t in enumerate(b) if j not in uzyte
    ]


def analizuj_twarze(obraz: Image.Image, cechy: CechyObrazu, *, cfg: Progi | None = None,
                    opisy: bool = True) -> None:
    """Koduje twarze jednym modelem lub sprawdza zgodność dwóch głosów."""
    from .referencje_twarzy import dopasuj_osobe, wczytaj_baze

    start = perf_counter()
    konfiguracja = wczytaj_konfiguracje() if opisy or cfg is None else {}
    if cfg is None:
        cfg = konfiguracja["twarze"]
    oba = cfg.get("tryb") == "oba"
    sigle = tuple(MODEL_TWARZY) if oba else (cfg["model"],)
    bledy_modelu = (OSError, ValueError, RuntimeError, ImportError, KeyError, cv2.error)
    raster = cv2.cvtColor(np.asarray(obraz), cv2.COLOR_RGB2BGR)
    silniki, kandydaci, bazy = {}, {}, {}
    do_opisu = []
    try:
        if min(obraz.size) < cfg["min_bok_px"]:
            return
        for sigiel in sigle:
            try:
                silniki[sigiel] = silnik_twarzy(sigiel)
            except bledy_modelu as blad:
                cechy.niedostepne_modele_twarzy.append(sigiel)
                cechy.braki.append(Brak(skladnik="twarze", powod=f"{sigiel}: {blad}"))
                continue
            try:
                kandydaci[sigiel] = _kandydaci(silniki[sigiel], raster, cfg)
                if not kandydaci[sigiel]:
                    continue
                progi = cfg[sigiel]
                baza = wczytaj_baze(sigiel) if "T_osoba" in progi and "margines" in progi else {}
                bazy[sigiel] = baza
                if not baza:
                    cechy.braki.append(Brak(skladnik="dopasowanie_osoby",
                                            powod=f"{sigiel}: brak bazy referencyjnej lub kalibracji"))
            except bledy_modelu as blad:
                cechy.braki.append(Brak(skladnik="twarze", powod=f"{sigiel}: {blad}"))
        pierwsze = kandydaci.get(sigle[0], [])
        drugie = kandydaci.get(sigle[1], []) if oba else []
        pary = pary_detekcji(pierwsze, drugie, cfg["prog_iou_glosow"])
        pary.sort(key=lambda p: -max((t.prostokat[2] * t.prostokat[3] for t in p if t is not None), default=0))
        for para in pary[:cfg["maks_liczba"]]:
            pomiary = {}
            wspolny = None
            for sigiel, k in zip(sigle, para):
                if k is None:
                    continue
                try:
                    silnik = silniki[sigiel]
                    with silnik.blokada:
                        pomiar = zmierz_twarz(silnik, sigiel, raster, k.detekcja, wspolny)
                    pomiary[sigiel] = (k, pomiar)
                    if wspolny is None and pomiar.wektor is not None:
                        wspolny = pomiar
                except bledy_modelu as blad:
                    cechy.braki.append(Brak(skladnik="twarze", powod=f"{sigiel}: {blad}"))
            magface = _jakosc_magface(wspolny.wyrownana, cechy, cfg) if wspolny is not None else None
            glosy = {}
            for sigiel, (k, pomiar) in pomiary.items():
                t = _cecha(pomiar, sigiel, k, obraz, cechy, cfg, magface)
                progi, baza = cfg[sigiel], bazy.get(sigiel)
                if baza and t.wektor is not None and t.wektor.wersja == progi.get("wersja_kalibracji"):
                    dopasuj_osobe(t, baza, progi["T_osoba"], progi["margines"])
                t.status_osoby = "rozpoznana" if t.osoba else "nieznana"
                glosy[sigiel] = t
            glowny = glosy.get(sigle[0])
            drugi = glosy.get(sigle[1]) if oba else None
            baza_twarzy = glowny or drugi
            if baza_twarzy is None:
                continue
            if oba:
                osoba, status = zgodnosc_glosow(glowny.osoba if glowny else None, drugi.osoba if drugi else None)
                baza_twarzy = baza_twarzy.model_copy(update={
                    "osoba": osoba, "status_osoby": status,
                    "wektor_drugi": drugi.wektor if glowny is not None and drugi is not None else None,
                    "jakosc_druga": drugi.jakosc if glowny is not None and drugi is not None else None,
                    "miara_jakosci_druga": drugi.miara_jakosci if glowny is not None and drugi is not None else None,
                })
            cechy.twarze.append(baza_twarzy)
            sigiel_glowny = sigle[0] if glowny is not None else sigle[1]
            prostokat = pomiary[sigiel_glowny][0].prostokat
            do_opisu.append((baza_twarzy, prostokat, baza_twarzy.jakosc >= _prog_jakosci(cfg, sigiel_glowny, magface)))
    finally:
        cechy.czasy_ms["twarze"] = (perf_counter() - start) * 1000
    if (opisy_cfg := konfiguracja.get("facetorch")) is not None and opisy:
        opisz_twarze(raster, do_opisu, cechy, Path(opisy_cfg["manifest"]), opisy_cfg["maks_twarzy_facetorch"])


def wektory_twarzy(twarz: Twarz, cfg: Progi) -> dict[str, Wektor]:
    """Zwraca tylko wektory zaakceptowane przez prog właściwej miary jakości."""
    wyniki = {}
    for wektor, jakosc, miara in (
        (twarz.wektor, twarz.jakosc, twarz.miara_jakosci),
        (twarz.wektor_drugi, twarz.jakosc_druga if twarz.jakosc_druga is not None else twarz.jakosc,
         twarz.miara_jakosci_druga or twarz.miara_jakosci),
    ):
        if wektor is None:
            continue
        for sigiel, model in MODEL_TWARZY.items():
            if wektor.model != model:
                continue
            progi = cfg.get(sigiel, {})
            prog = progi.get("prog_jakosci")
            if miara == "zastepcza" or (miara is None and cfg.get("jakosc") == "zastepcza"):
                prog = progi.get("prog_jakosci_zastepcza", prog)
            elif miara == "calosc" or (miara is None and cfg.get("magface_tylko_norma") is False):
                prog = cfg.get("magface", {}).get("calosc", {}).get("prog_jakosci", prog)
            if prog is None or jakosc >= prog:
                wyniki[sigiel] = wektor
    return wyniki


def porownaj_modele_twarzy(a: Sequence[Twarz], b: Sequence[Twarz], cfg: Progi,
                          niedostepne: frozenset[str] = frozenset(),
                          pary: list[CosinusyParyTwarzy] | None = None) -> tuple[dict[str, float | None], list[Dowod], set[str]]:
    """Raportuje cosinusy obu modeli i dopuszcza jeden głos po awarii drugiego."""
    oba = cfg.get("tryb") == "oba"
    wymagane = set(MODEL_TWARZY) - niedostepne if oba else {cfg["model"]} if "model" in cfg else set()
    najlepsze: dict[str, float | None] = dict.fromkeys(MODEL_TWARZY)
    mismatch = set()
    dowody = {}
    prawa_strona = [(t, wektory_twarzy(t, cfg)) for t in b]
    for i, lewa in enumerate(a):
        wa = wektory_twarzy(lewa, cfg)
        for j, (prawa, wb) in enumerate(prawa_strona):
            pomiary = {}
            for s in sorted(wa.keys() & wb.keys()):
                wynik = cosinus(wa[s], wb[s])
                if wynik is None:
                    mismatch.add(s)
                    continue
                pomiary[s] = wynik
                if najlepsze[s] is None or wynik > najlepsze[s]:
                    najlepsze[s] = wynik
            if pary is not None and pomiary:
                pary.append(CosinusyParyTwarzy(indeks_a=i, indeks_b=j,
                                              cosinusy={s: pomiary.get(s) for s in MODEL_TWARZY}))
            wymagane_pary = wymagane if oba or wymagane else set(pomiary)
            if not wymagane_pary or not wymagane_pary <= pomiary.keys():
                continue
            glosy = {s: pomiary[s] for s in sorted(wymagane_pary)}
            wersje_ok = all(cfg.get(s, {}).get("wersja_kalibracji", wa[s].wersja) == wa[s].wersja for s in glosy)
            if not wersje_ok:
                mismatch.update(s for s in glosy if cfg.get(s, {}).get("wersja_kalibracji", wa[s].wersja) != wa[s].wersja)
                continue
            opis = ", ".join(f"{s} {v:.3f}" for s, v in glosy.items())
            awaryjny = oba and len(glosy) == 1
            if (lewa.osoba and lewa.osoba == prawa.osoba
                    and (not oba or lewa.status_osoby == prawa.status_osoby == "rozpoznana")):
                rodzaj, powod = "ta_sama_osoba", f"Ta sama osoba ({opis})"
            elif not (lewa.osoba and prawa.osoba) and all(
                s in cfg and "T_f" in cfg[s] and v >= cfg[s]["T_f"] for s, v in glosy.items()
            ):
                rodzaj, powod = "ta_sama_twarz", f"Ta sama twarz (cosinusy {opis})"
                if awaryjny:
                    powod += ", 1 głos po awarii drugiego modelu"
            else:
                continue
            wynik = max(0.0, min(glosy.values()))
            if rodzaj not in dowody or wynik > dowody[rodzaj].wynik:
                dowody[rodzaj] = Dowod(rodzaj=rodzaj, wynik=wynik, uzasadnienie=powod,
                                       miejsca_a=[lewa.prostokat], miejsca_b=[prawa.prostokat],
                                       szczegoly={"cosinusy": glosy, "tryb_awaryjny": awaryjny})
    return najlepsze, [dowody[s] for s in ("ta_sama_osoba", "ta_sama_twarz") if s in dowody], mismatch


def porownaj_twarze(a: Sequence[Twarz], b: Sequence[Twarz], cfg: Progi) -> tuple[float | None, Dowod | None]:
    """Zwraca maksymalny cosinus i najlepszy dopuszczony dowód twarzy."""
    pomiary, dowody, _ = porownaj_modele_twarzy(a, b, cfg)
    return max((v for v in pomiary.values() if v is not None), default=None), next(iter(dowody), None)
