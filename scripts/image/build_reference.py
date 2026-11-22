"""Buduje lokalną bazę twarzy z wcześniej dostarczonych zdjęć i źródeł."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fakecollector.features.image.referencje_twarzy import zbuduj_baze
from fakecollector.features.image.twarze import MODEL_TWARZY


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dane", type=Path, default=Path("data"))
    parser.add_argument("--modele", default="buffalo_l,yunet_sface")
    args = parser.parse_args()
    sigle = args.modele.split(",")
    if set(sigle) - set(MODEL_TWARZY):
        parser.error("Dozwolone modele to buffalo_l i yunet_sface")
    raport = {sigiel: zbuduj_baze(args.dane / "reference_people", sigiel) for sigiel in dict.fromkeys(sigle)}
    katalog = args.dane / "raporty"
    katalog.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    plik = katalog / f"referencje-{stamp}.json"
    plik.write_text(json.dumps(raport, ensure_ascii=False, indent=2), encoding="utf-8")
    for model, wynik in raport.items():
        print(f"{model}: {wynik['osoby']} osób, {wynik['zdjecia']} zdjęć, {len(wynik['braki'])} braków")
        for brak in wynik["braki"]:
            print(f"  {brak['osoba']} / {brak.get('plik', 'katalog')}: {brak['blad']}"
                  + (f" ({brak['twarze']})" if "twarze" in brak else ""))
        print(f"  * {len(wynik['bez_ustalonej_licencji'])} pozycji bez ustalonej licencji, wyłącznie lokalnie")
    print(plik)


if __name__ == "__main__":
    main()
