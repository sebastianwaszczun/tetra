"""Walidacja ręcznych adnotacji i bezpieczny odczyt obrazów do podglądu."""
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import Annotated, Literal, Self

from PIL import Image
from pydantic import AfterValidator, Field, NonNegativeInt, RootModel, model_validator

from .konfiguracja import wczytaj_konfiguracje
from .model import Model, Prostokat
from .przygotowanie import czytaj_bajty, otworz_raster


def _wzgledna(wartosc: str) -> str:
    sciezka = PureWindowsPath(wartosc)
    if not sciezka.parts or sciezka.anchor or ".." in sciezka.parts or "\\" in wartosc:
        raise ValueError("Wymagana ścieżka względna w data/ z separatorem /")
    return wartosc


Sciezka = Annotated[str, AfterValidator(_wzgledna)]
Nazwa = Annotated[str, Field(min_length=1)]


class Oczekiwane(Model):
    # None oznacza brak adnotacji, a pusta lista oznacza potwierdzony brak.
    logo: list[str] | None = None
    elementy: list[str] | None = None
    liczba_twarzy: NonNegativeInt | None = None
    ubogi_w_tresc: bool | None = None
    prostokat_odtwarzania: Prostokat | None = None


class Kreacja(Model):
    id: Nazwa
    plik: Sciezka
    zgloszenie: Nazwa
    prostokat_kreacji: Prostokat
    oczekiwane: Oczekiwane
    status: Literal["szkic", "zatwierdzone"]
    uwagi: str = ""


class ParaKreacji(Model):
    a: Nazwa
    b: Nazwa
    relacja: Literal["duplikat", "ta_sama_reklama", "ten_sam_szablon", "rozne"]
    pewnosc: Nazwa
    uzasadnienie: Nazwa


class ZbiorKreacji(Model):
    wersja: Literal[1]
    kreacje: list[Kreacja]
    pary: list[ParaKreacji]

    @model_validator(mode="after")
    def sprawdz_odwolania(self) -> Self:
        ids = {k.id for k in self.kreacje}
        if len(ids) != len(self.kreacje):
            raise ValueError("Powtórzone id kreacji")
        for para in self.pary:
            if para.a == para.b or not {para.a, para.b} <= ids:
                raise ValueError("Para musi wskazywać dwie różne kreacje z tego zbioru")
        return self


class ParaObrazow(Model):
    a: Sciezka
    b: Sciezka
    relacja: Literal["identyczne", "rozne", "sporne"]
    zrodlo: Nazwa
    uwagi: str = ""

    @model_validator(mode="after")
    def sprawdz_pare(self) -> Self:
        if Path(self.a) == Path(self.b):
            raise ValueError("Para musi zawierać dwa różne pliki")
        return self


def plik_danych(dane: Path, nazwa: str) -> Path:
    """Rozwiązuje ścieżkę względem data/ i sprawdza także dowiązania."""
    baza = dane.resolve()
    plik = (baza / _wzgledna(nazwa)).resolve()
    if not plik.is_relative_to(baza):
        raise ValueError("Plik poza katalogiem danych")
    if not plik.is_file():
        raise FileNotFoundError(plik)
    return plik


@contextmanager
def _sprawdz_obraz(plik: Path) -> Iterator[Image.Image]:
    limity = wczytaj_konfiguracje()["wczytywanie"]
    with otworz_raster(czytaj_bajty(plik, limity["maks_bajtow"]), limity["maks_pikseli"]) as obraz:
        yield obraz


def otworz_obraz(plik: Path) -> Image.Image:
    """Sprawdza limity przed dekodowaniem obrazu na potrzeby narzędzi ewaluacyjnych."""
    with _sprawdz_obraz(plik) as obraz:
        return obraz.convert("RGBA" if obraz.has_transparency_data else "RGB")


def wczytaj_kreacje(dane: Path) -> ZbiorKreacji:
    """Sprawdza schemat, referencje i granice prostokątów w obrazach."""
    zbior = ZbiorKreacji.model_validate_json((dane / "wzorzec/kreacje.json").read_bytes(), strict=True)
    for kreacja in zbior.kreacje:
        plik = plik_danych(dane, kreacja.plik)
        with _sprawdz_obraz(plik) as obraz:
            x, y, szer, wys = kreacja.prostokat_kreacji
            if x + szer > obraz.width or y + wys > obraz.height:
                raise ValueError(f"Prostokąt poza obrazem dla {kreacja.id}")
            przycisk = kreacja.oczekiwane.prostokat_odtwarzania
            if przycisk:
                px, py, pw, ph = przycisk
                if px < x or py < y or px + pw > x + szer or py + ph > y + wys:
                    raise ValueError(f"Prostokąt przycisku poza kreacją dla {kreacja.id}")
    return zbior


def wczytaj_pary(dane: Path) -> list[ParaObrazow]:
    """Sprawdza schemat listy oraz istnienie obu obrazów każdej pary."""
    sciezka = dane / "wzorzec/pary_obrazow.json"
    pary = RootModel[list[ParaObrazow]].model_validate_json(sciezka.read_bytes(), strict=True).root
    for para in pary:
        plik_danych(dane, para.a)
        plik_danych(dane, para.b)
    return pary
