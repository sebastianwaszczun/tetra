"""Warianty, podglądy i porównanie raportów na obrazach syntetycznych."""
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from fakecollector.features.image.warianty import generuj_warianty

SKRYPTY = Path(__file__).resolve().parents[2] / "scripts"


def test_warianty_i_podglad(tmp_path: Path) -> None:
    obraz = Image.new("RGB", (160, 100), "navy")
    ImageDraw.Draw(obraz).ellipse((20, 30, 100, 90), fill="yellow")
    plik = tmp_path / "test.png"
    obraz.save(plik)
    katalog = tmp_path / "podglad"
    subprocess.run([sys.executable, str(SKRYPTY / "warianty.py"), str(plik), "--wyjscie", str(katalog)], check=True)
    manifest = json.loads((katalog / "manifest.json").read_text())
    oczekiwania = {w["plik"]: w["oczekiwanie"] for w in manifest["warianty"]}
    obiecane = {"skala_25", "jpeg_q30", "przyciecie_10", "ramka", "jasnosc_80", "jasnosc_120",
                "kontrast_80", "kontrast_120", "odbicie", "pasek_15", "znak_wodny"}
    nieobiecane = {"obrot_8", "przyciecie_30", "kolaz", "wymiana_tla"}
    assert len(oczekiwania) == len(obiecane | nieobiecane)
    for wariant in generuj_warianty(obraz):
        nazwa = wariant.nazwa + ".png"
        assert wariant.nazwa in obiecane | nieobiecane
        assert oczekiwania[nazwa] == ("obiecane" if wariant.nazwa in obiecane else "nieobiecane")
        with Image.open(katalog / nazwa) as zapis:
            assert zapis.size == wariant.obraz.size and zapis.tobytes() == wariant.obraz.tobytes()
        assert wariant.obraz.size != obraz.size or wariant.obraz.tobytes() != obraz.tobytes()


def test_podglad_prostokata(tmp_path: Path) -> None:
    plik = tmp_path / "test.png"
    Image.new("RGB", (20, 15), "white").save(plik)
    wzorzec = tmp_path / "wzorzec"
    wzorzec.mkdir()
    kreacja = {"id": "a", "plik": "test.png", "zgloszenie": "test", "oczekiwane": {},
               "status": "szkic", "prostokat_kreacji": [3, 4, 8, 6]}
    (wzorzec / "kreacje.json").write_text(json.dumps({"wersja": 1, "kreacje": [kreacja], "pary": []}))
    subprocess.run([sys.executable, str(SKRYPTY / "podglad_kreacji.py"), "--dane", str(tmp_path)], check=True)
    with Image.open(tmp_path / "podglad/kreacje/0001.png") as rysunek:
        assert rysunek.getpixel((3, 4)) == rysunek.getpixel((10, 9)) == (255, 0, 0)
        assert rysunek.getpixel((11, 10)) == (255, 255, 255)


def test_roznica_raportow(tmp_path: Path) -> None:
    cmd = [sys.executable, str(SKRYPTY / "ewaluacja_obrazu.py"), "--dane", str(tmp_path), "--pomiar", "bazowy"]
    subprocess.run(cmd, check=True)
    pierwszy, = (tmp_path / "raporty").glob("*.json")
    baza = json.loads(pierwszy.read_text())
    assert baza["poprzedni"] is None and set(baza["zmiany"].values()) == {None}
    assert baza["pomiar"] == "bazowy"
    baza["metryki"]["porownania"] = 2
    pierwszy.write_text(json.dumps(baza))
    subprocess.run(cmd, check=True)
    drugi = max((tmp_path / "raporty").glob("*.json"))
    raport = json.loads(drugi.read_text())
    assert raport["poprzedni"] == pierwszy.name and raport["zmiany"]["porownania"] == -2
    assert set(raport["metryki"].values()) == {0} and raport["braki"]
    assert "-2" in drugi.with_suffix(".txt").read_text()


def test_porownaj_obrazy_demo_offline(tmp_path: Path) -> None:
    a, b, c = (tmp_path / f"{n}.png" for n in "abc")
    Image.new("RGB", (80, 80), "white").save(a)
    b.write_bytes(a.read_bytes())
    Image.new("RGB", (80, 80), "black").save(c)

    uruchomienie = subprocess.run(
        [sys.executable, str(SKRYPTY / "porownaj_obrazy.py"), str(a), str(b), str(c)],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    wynik = json.loads(uruchomienie.stdout)
    ab, ac, bc = wynik["pary"]
    assert (ab["a"], ab["b"]) == ("a.png", "b.png")
    dowod, = ab["dowody"]
    assert (dowod["rodzaj"], dowod["sila"]) == ("identyczny_plik", "silny")
    assert ab["metryki"]["sha256_rowny"] is True
    assert all(p["metryki"]["sha256_rowny"] is False and not p["dowody"] for p in (ac, bc))
    raport = tmp_path / wynik["raport_html"]
    assert raport.parent == tmp_path / "data/raporty"
    html = raport.read_text(encoding="utf-8")
    assert html.count("<section>") == 3 and "data:image/jpeg;base64," in html
    assert "<script" not in html and 'src="http' not in html
