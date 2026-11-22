"""Pobiera i zapisuje wagi obrazu. Analiza korzysta później z plików lokalnych."""
import argparse
import json
from hashlib import file_digest
from pathlib import Path
from shutil import copyfileobj
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen
from zipfile import ZipFile, is_zipfile

KATALOG = Path("/models/obraz")
SIGLIP_REPO = "timm/ViT-B-16-SigLIP2"
MAGFACE_URL = (
    "https://drive.usercontent.google.com/download"
    "?id=1Bd87admxOZvbIOAyTkGEntsEz3fyMt7H&export=download&confirm=t"
)
MAGFACE_SHA256 = "cfeba792dada6f1f30d1e118aff077d493dd95dd76c77c30f57f90fd0164ad58"
FACETORCH_GDRIVE = {
    "deepfake.pt": "1GjDTwQpvrkCjXOdiBy1oMkzm7nt-bXFg",
    "fer.pt": "1i5f8vy1dZv_u8vsJsj9CHEzHwsokIknr",
}
FACETORCH_SHA256 = {
    "deepfake.pt": "e36197ae83fa7c050e8ceba267a1f028f2d74d8ad805bf5d5037a26e8ca96c13",
    "fer.pt": "39d8046b1fe3eb06d5edb094307f0cd465a40b7f8886d4b477ca4bbaf7cbb62e",
}
BUFFALO_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
BUFFALO_PLIKI = ("det_10g.onnx", "w600k_r50.onnx")
OPENCV_PLIKI = {
    "face_detection_yunet/face_detection_yunet_2026may.onnx":
        "ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0",
    "face_recognition_sface/face_recognition_sface_2021dec.onnx":
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
}
MODELE_TWARZY = {"buffalo_l", "yunet_sface"}


def _sha256(plik: Path) -> str:
    with plik.open("rb") as wejscie:
        return file_digest(wejscie, "sha256").hexdigest()


def _pobierz_plik(url: str, plik: Path, limit: int) -> None:
    print(f"Pobieranie {plik.name}", flush=True)
    with urlopen(url, timeout=60) as wejscie, plik.open("wb") as wyjscie:
        razem = 0
        while fragment := wejscie.read(1024 * 1024):
            razem += len(fragment)
            if razem > limit:
                raise ValueError(f"Przekroczono oczekiwany rozmiar {plik.name}")
            wyjscie.write(fragment)


def _pobierz_twarze(sigiel: str) -> tuple[Path, str]:
    if sigiel == "buffalo_l":
        cel = Path("/models/insightface/models/buffalo_l")
        nazwy = BUFFALO_PLIKI
    else:
        cel = Path("/models/opencv")
        nazwy = tuple(Path(zrodlo).name for zrodlo in OPENCV_PLIKI)
    cel.mkdir(parents=True, exist_ok=True)
    plik_opisu = KATALOG / f"{sigiel}.json"
    dotychczas = json.loads(plik_opisu.read_text(encoding="utf-8")) if plik_opisu.is_file() else {}
    sumy = dotychczas.get("sha256_plikow", {})
    if not all((cel / n).is_file() and _sha256(cel / n) == sumy.get(n) for n in nazwy):
        with TemporaryDirectory(dir=cel) as katalog:
            roboczy = Path(katalog)
            if sigiel == "buffalo_l":
                archiwum = roboczy / "buffalo_l.zip"
                _pobierz_plik(BUFFALO_URL, archiwum, 400_000_000)
                with ZipFile(archiwum) as zip_:
                    for n in nazwy:
                        wpis = zip_.getinfo(n)
                        if wpis.file_size > 300_000_000:
                            raise ValueError(f"Przekroczono oczekiwany rozmiar {n}")
                        with zip_.open(wpis) as wejscie, (roboczy / n).open("wb") as wyjscie:
                            copyfileobj(wejscie, wyjscie)
            else:
                for zrodlo, suma in OPENCV_PLIKI.items():
                    plik = roboczy / Path(zrodlo).name
                    url = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/{zrodlo}"
                    _pobierz_plik(url, plik, 50_000_000)
                    if _sha256(plik) != suma:
                        raise ValueError(f"Niezgodna suma SHA-256 dla {plik.name}")
            sumy = {n: _sha256(roboczy / n) for n in nazwy}
            for n in nazwy:
                (roboczy / n).replace(cel / n)
    wersja = "+".join(sumy[n] for n in nazwy)
    zapisz_manifest(sigiel, cel, wersja, sumy)
    return cel, wersja


def _pobierz_magface() -> tuple[Path, str]:
    cel = KATALOG / "magface" / "magface_epoch_00025.pth"
    cel.parent.mkdir(parents=True, exist_ok=True)
    manifest = KATALOG / "magface.json"
    if cel.is_file() and manifest.is_file():
        suma = _sha256(cel)
        zapis = json.loads(manifest.read_text(encoding="utf-8"))
        if suma == MAGFACE_SHA256 == zapis.get("sha256_plikow", {}).get(cel.name):
            return cel, suma

    with TemporaryDirectory(dir=cel.parent) as katalog:
        plik = Path(katalog) / cel.name
        _pobierz_plik(MAGFACE_URL, plik, 283_040_192)
        suma = _sha256(plik)
        if suma != MAGFACE_SHA256:
            raise ValueError("Niezgodna suma SHA-256 oficjalnych wag MagFace")
        plik.replace(cel)
    zapisz_manifest("magface", cel, suma, {cel.name: suma})
    return cel, suma


def _pobierz_facetorch() -> tuple[Path, str]:
    cel = KATALOG / "facetorch"
    cel.mkdir(parents=True, exist_ok=True)
    manifest = KATALOG / "facetorch.json"
    sumy = json.loads(manifest.read_text(encoding="utf-8")).get("sha256_plikow", {}) if manifest.is_file() else {}
    if not all((cel / n).is_file() and _sha256(cel / n) == FACETORCH_SHA256[n] == sumy.get(n)
               for n in FACETORCH_GDRIVE):
        with TemporaryDirectory(dir=cel) as katalog:
            roboczy = Path(katalog)
            for nazwa, identyfikator in FACETORCH_GDRIVE.items():
                plik = roboczy / nazwa
                url = f"https://drive.usercontent.google.com/download?id={identyfikator}&export=download&confirm=t"
                _pobierz_plik(url, plik, 400_000_000)
                if not is_zipfile(plik):
                    raise ValueError(f"Google Drive nie zwrócił archiwum TorchScript {nazwa}")
            sumy = {n: _sha256(roboczy / n) for n in FACETORCH_GDRIVE}
            if sumy != FACETORCH_SHA256:
                raise ValueError("Niezgodne sumy SHA-256 oficjalnych wag FaceTorch")
            for nazwa in FACETORCH_GDRIVE:
                (roboczy / nazwa).replace(cel / nazwa)
    wersja = "+".join(sumy[n] for n in FACETORCH_GDRIVE)
    zapisz_manifest("facetorch", cel, wersja, sumy)
    return cel, wersja


def pobierz(sigiel: str) -> tuple[Path, str]:
    """Zwraca ścieżkę oraz wersję pobranych wag."""
    if sigiel in MODELE_TWARZY:
        return _pobierz_twarze(sigiel)
    if sigiel == "magface":
        return _pobierz_magface()
    if sigiel == "facetorch":
        return _pobierz_facetorch()
    if sigiel == "clip":
        from open_clip import get_pretrained_cfg
        from open_clip.pretrained import download_pretrained_from_url

        url = get_pretrained_cfg("ViT-B-32", "openai")["url"]
        suma = url.split("/")[-2]
        cel = KATALOG / "clip" / url.rsplit("/", 1)[-1]
        cel.parent.mkdir(parents=True, exist_ok=True)
        if cel.is_file():
            with cel.open("rb") as plik:
                if file_digest(plik, "sha256").hexdigest() == suma:
                    return cel, suma
        with TemporaryDirectory(dir=cel.parent) as katalog:
            Path(download_pretrained_from_url(url, cache_dir=katalog)).replace(cel)
        return cel, suma
    from huggingface_hub import snapshot_download

    migawka = Path(snapshot_download(
        SIGLIP_REPO, cache_dir=KATALOG / "hf", token=False,
        allow_patterns=["open_clip_config.json", "open_clip_model.safetensors", "tokenizer*",
                        "special_tokens_map.json", "config.json", "*.model"],
    ))
    for plik in ("open_clip_config.json", "open_clip_model.safetensors", "tokenizer_config.json"):
        if not (migawka / plik).is_file():
            raise FileNotFoundError(f"Brak {plik} w {SIGLIP_REPO}")
    return migawka, migawka.name


def zapisz_manifest(sigiel: str, sciezka: Path, wersja: str, sumy: dict[str, str] | None = None) -> None:
    plik = KATALOG / f"{sigiel}.json"
    manifest = {"sciezka": str(sciezka), "wersja": wersja}
    if sumy is not None:
        manifest["sha256_plikow"] = sumy
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=KATALOG, delete=False) as tymczasowy:
        json.dump(manifest, tymczasowy)
    Path(tymczasowy.name).replace(plik)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modele", default="siglip2,clip", help="Sigle po przecinku, np. siglip2,clip")
    args = parser.parse_args()
    sigle = args.modele.split(",")
    if set(sigle) - {"siglip2", "clip", "magface", "facetorch"} - MODELE_TWARZY:
        parser.error("Dozwolone wartości to siglip2, clip, buffalo_l, yunet_sface, magface i facetorch")
    KATALOG.mkdir(parents=True, exist_ok=True)
    for sigiel in dict.fromkeys(sigle):
        try:
            sciezka, wersja = pobierz(sigiel)
        except (URLError, TimeoutError, ValueError) as blad:
            parser.exit(1, f"{sigiel}: {blad}\n")
        if sigiel in {"siglip2", "clip"}:
            zapisz_manifest(sigiel, sciezka, wersja)
        print(f"{sigiel}: {wersja}", flush=True)


if __name__ == "__main__":
    main()
