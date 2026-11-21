"""Walidacja formatów 3.2–3.3 na danych tworzonych wyłącznie w katalogu tymczasowym."""
import json
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from fakecollector.features.image.wzorzec import wczytaj_kreacje, wczytaj_pary


@pytest.fixture
def dane(tmp_path: Path) -> Path:
    for nazwa in ("a", "b"):
        Image.new("RGB", (24, 16), "cyan").save(tmp_path / f"{nazwa}.png")
    katalog = tmp_path / "wzorzec"
    katalog.mkdir()
    kreacje = [{"id": k, "plik": f"{k}.png", "zgloszenie": "test", "prostokat_kreacji": [2, 1, 22, 15],
                "oczekiwane": {"logo": [], "liczba_twarzy": 0}, "status": "szkic"} for k in ("a", "b")]
    para = {"a": "a", "b": "b", "relacja": "duplikat", "pewnosc": "pewne", "uzasadnienie": "test"}
    (katalog / "kreacje.json").write_text(json.dumps({"wersja": 1, "kreacje": kreacje, "pary": [para]}))
    (katalog / "pary_obrazow.json").write_text(json.dumps([
        {"a": "a.png", "b": "b.png", "relacja": "identyczne", "zrodlo": "sha256"}]))
    return tmp_path


def test_prawidlowy_wzorzec(dane: Path) -> None:
    zbior = wczytaj_kreacje(dane)
    assert zbior.pary[0].b == zbior.kreacje[1].id
    assert wczytaj_pary(dane)[0].relacja == "identyczne"


@pytest.mark.parametrize(("pole", "wartosc"), [
    ("plik", "../a.png"), ("plik", "C:/a.png"), ("id", "b"), ("id", "nowe"),
    ("prostokat_kreacji", [0, 1, 24, 16]), ("status", "x"),
])
def test_bledna_kreacja(dane: Path, pole: str, wartosc: object) -> None:
    plik = dane / "wzorzec/kreacje.json"
    zapis = json.loads(plik.read_text())
    zapis["kreacje"][0][pole] = wartosc
    plik.write_text(json.dumps(zapis))
    with pytest.raises(ValueError):
        wczytaj_kreacje(dane)


def test_bledna_para(dane: Path) -> None:
    plik = dane / "wzorzec/pary_obrazow.json"
    zapis = json.loads(plik.read_text())
    zapis[0]["relacja"] = "podobne"
    plik.write_text(json.dumps(zapis))
    with pytest.raises(ValidationError):
        wczytaj_pary(dane)


@pytest.mark.dane_nask
@pytest.mark.parametrize("nazwa", ["kreacje", "pary_obrazow"])
def test_lokalne_adnotacje(nazwa: str) -> None:
    dane = Path(__file__).resolve().parents[2] / "data"
    if not (dane / "nask/DANE").is_dir() or not (dane / "wzorzec/kreacje.json").is_file():
        pytest.skip("Wymagane lokalne data/nask/DANE i ręczne data/wzorzec/kreacje.json")
    if nazwa == "kreacje":
        wczytaj_kreacje(dane)
    elif not (dane / "wzorzec/pary_obrazow.json").is_file():
        pytest.skip("Brak lokalnego wzorca par obrazów")
    else:
        wczytaj_pary(dane)
