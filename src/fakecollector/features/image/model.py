"""Kontrakt modułu obrazu. Współrzędne odnoszą się do obrazu wejściowego."""
from typing import Annotated, Any, Literal, Self

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, NonNegativeInt, PositiveInt, PrivateAttr, model_validator

from .konfiguracja import wczytaj_konfiguracje

Wynik = Annotated[float, Field(ge=0, le=1)]
Cosinus = Annotated[float, Field(ge=-1, le=1)]
Nieujemny = Annotated[FiniteFloat, Field(ge=0)]
Rozmiar = tuple[NonNegativeInt, NonNegativeInt]
Prostokat = tuple[NonNegativeInt, NonNegativeInt, PositiveInt, PositiveInt]
Hash = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{16}$")]
Hash64 = Annotated[int, Field(ge=0, lt=2**64)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]
Rodzaj = Literal["identyczny_plik", "duplikat_kreacji", "ten_sam_kadr", "podobny_wyglad",
                 "wspolne_elementy_szablonu", "ta_sama_osoba", "ta_sama_twarz",
                 "wspolne_logo", "wspolny_qr"]
Sila = Literal["silny", "sredni", "slaby"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Kontekst(Model):
    zrodlo: str
    rola: Literal["kreacja", "obrazek_strony", "klatka", "zrzut_caly"]
    czas_klatki: Nieujemny | None = None


class Brak(Model):
    skladnik: str
    powod: str


class Diagnostyka(Model):
    braki: list[Brak] = Field(default_factory=list)


class Przygotowanie(Model):
    rozmiar_oryginalny: Rozmiar = (0, 0)
    odciete_ramki: tuple[NonNegativeInt, NonNegativeInt, NonNegativeInt, NonNegativeInt] = (0, 0, 0, 0)
    maska_nakladek: bool = False
    orientacja_exif: int = 1


class MiaraTresci(Model):
    dominujacy_kolor: Wynik | None = None
    kontrast: Nieujemny | None = None
    krawedzie: Nieujemny | None = None


class Hashe(Model):
    phash: tuple[Hash, Hash, Hash] | tuple[()] = ()
    dhash: tuple[Hash, Hash, Hash] | tuple[()] = ()


class Wektor(Model):
    model: str
    wersja: str
    wymiar: PositiveInt
    wartosci: list[FiniteFloat]

    @model_validator(mode="after")
    def sprawdz_wymiar(self) -> Self:
        if len(self.wartosci) != self.wymiar:
            raise ValueError("Wymiar musi odpowiadać liczbie wartości wektora")
        return self


class Logo(Model):
    marka: str
    rog: Literal["lewy_gorny", "prawy_gorny", "lewy_dolny", "prawy_dolny", "dol"]
    prostokat: Prostokat
    pewnosc: Wynik
    zrodlo: Literal["opis", "wzorzec"]


class Kandydat(Model):
    osoba: str
    dopasowanie: Cosinus


Punkt = tuple[Nieujemny, Nieujemny]


class Emocja(Model):
    etykieta: str
    pewnosc: Wynik


class Twarz(Model):
    prostokat: Prostokat
    pewnosc_wykrycia: Wynik
    punkty: tuple[Punkt, Punkt, Punkt, Punkt, Punkt]
    wielkosc_px: PositiveInt
    wektor: Wektor | None = None
    wektor_drugi: Wektor | None = None
    jakosc: Wynik
    jakosc_druga: Wynik | None = None
    miara_jakosci: Literal["zastepcza", "skladnik", "calosc"] | None = None
    miara_jakosci_druga: Literal["zastepcza", "skladnik", "calosc"] | None = None
    osoba: str | None = None
    status_osoby: Literal["rozpoznana", "nieznana", "niepewne"] | None = None
    dopasowanie: Cosinus | None = None
    drugi_kandydat: Kandydat | None = None
    deepfake: Wynik | None = None
    emocja: Emocja | None = None
    uwaga_facetorch: Literal["orientacyjnie"] | None = None
    brak_facetorch: Literal["niska_jakosc", "brak_wag", "limit_twarzy", "blad_predykcji"] | None = None


class KodQR(Model):
    tekst: str
    prostokat: Prostokat
    metoda: str
    czytnik: str | None = None
    proba: str | None = None
    czy_adres: bool
    domena: str | None = None


class CechyObrazu(Diagnostyka):
    wersja_schematu: Literal[1] = 1
    sha256: Sha256 | Literal[""] = ""
    rozmiar: Rozmiar = (0, 0)
    format: str = ""
    przygotowanie: Przygotowanie = Field(default_factory=Przygotowanie)
    ubogi_w_tresc: bool = False
    za_maly: bool = False
    miara_tresci: MiaraTresci = Field(default_factory=MiaraTresci)
    hashe: Hashe = Field(default_factory=Hashe)
    wektor: Wektor | None = None
    elementy_szablonu: dict[str, Wynik] = Field(default_factory=dict)
    logo_obraz: list[Logo] = Field(default_factory=list)
    twarze: list[Twarz] = Field(default_factory=list)
    qr: list[KodQR] = Field(default_factory=list)
    czasy_ms: dict[str, Nieujemny] = Field(default_factory=dict)
    grupa_podobnych: str | None = None
    jak_obraz: str | None = None
    niedostepne_modele_twarzy: list[str] = Field(default_factory=list)
    wersja_logo: str | None = None
    _raster_orb: Image.Image | None = PrivateAttr(default=None)
    _cechy_orb: dict[tuple[int, int], Any] = PrivateAttr(default_factory=dict)
    _surowe_logo: tuple[Any, Any] | None = PrivateAttr(default=None)

    def dla_frontu(self) -> dict:
        """Serializuje cechy bez wektorów obrazu i twarzy."""
        return self.model_dump(mode="json", exclude={
            "wektor": True, "twarze": {"__all__": {"wektor", "wektor_drugi", "drugi_kandydat"}},
        })


class Dowod(Model):
    rodzaj: Rodzaj
    sila: Sila
    wynik: Wynik
    uzasadnienie: str
    miejsca_a: list[Prostokat] = Field(default_factory=list)
    miejsca_b: list[Prostokat] = Field(default_factory=list)
    szczegoly: dict = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def przypisz_sile(cls, dane: Any) -> Any:
        if isinstance(dane, dict) and "sila" not in dane and "rodzaj" in dane:
            sila = wczytaj_konfiguracje()["dowody"].get(dane["rodzaj"])
            return {**dane, "sila": sila}
        return dane


class CosinusyParyTwarzy(Model):
    indeks_a: NonNegativeInt
    indeks_b: NonNegativeInt
    cosinusy: dict[str, Cosinus | None]


class Metryki(Model):
    sha256_rowny: bool | None = None
    phash_min: Annotated[int, Field(ge=0, le=64)] | None = None
    dhash_min: Annotated[int, Field(ge=0, le=64)] | None = None
    wariant_hash: Literal["caly", "srodek", "odbicie"] | None = None
    cos_wyglad: Cosinus | None = None
    cos_twarzy_max: Cosinus | None = None
    cos_twarzy: dict[str, Cosinus | None] = Field(default_factory=dict)
    cos_par_twarzy: list[CosinusyParyTwarzy] = Field(default_factory=list)
    wspolne_qr: int = 0
    wspolne_elementy: list[str] = Field(default_factory=list)
    wspolne_logo: list[str] = Field(default_factory=list)
    orb: dict[str, Any] | None = None


class WynikPorownania(Diagnostyka):
    dowody: list[Dowod] = Field(default_factory=list)
    metryki: Metryki = Field(default_factory=Metryki)


class CechyZbiorcze(Diagnostyka):
    elementy: dict[str, Wynik] = Field(default_factory=dict)
    logo: list[Logo] = Field(default_factory=list)
    twarze: list[Twarz] = Field(default_factory=list)
    hashe: list[Hashe] = Field(default_factory=list)
    wektor_sredni: Wektor | None = None
    wektory: list[Wektor] = Field(default_factory=list)
    qr: list[KodQR] = Field(default_factory=list)
    zrodla: list[Sha256] = Field(default_factory=list)

    def dla_frontu(self) -> dict:
        """Serializuje zbiorczy wynik reklamy bez wektorów."""
        return self.model_dump(mode="json", exclude={
            "wektor_sredni": True, "wektory": True,
            "twarze": {"__all__": {"wektor", "wektor_drugi", "drugi_kandydat"}},
        })


class KluczeWyszukiwania(Diagnostyka):
    sha256: Sha256 | None = None
    phash: list[Hash64] = Field(default_factory=list)
    dhash: list[Hash64] = Field(default_factory=list)
    wektor: Wektor | None = None
    wektory_twarzy: list[Wektor] = Field(default_factory=list)
    qr: list[str] = Field(default_factory=list)
