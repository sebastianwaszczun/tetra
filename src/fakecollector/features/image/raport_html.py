"""Samodzielny raport par obrazów bez zewnętrznych zasobów."""
import json
from base64 import b64encode
from dataclasses import dataclass
from html import escape
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from .model import CechyObrazu, Prostokat, WynikPorownania


@dataclass(frozen=True)
class MaterialHTML:
    nazwa: str
    obraz: Image.Image | None
    cechy: CechyObrazu


@dataclass(frozen=True)
class ParaHTML:
    a: MaterialHTML
    b: MaterialHTML
    relacja: str
    wynik: WynikPorownania


def _podglad(material: MaterialHTML, orb: list[Prostokat]) -> str:
    nazwa = escape(material.nazwa)
    obraz = material.obraz
    if obraz is None:
        return f"<figure><p>Brak rastra</p><figcaption>{nazwa}</figcaption></figure>"
    mini = obraz.convert("RGB")
    mini.thumbnail((540, 400), Image.Resampling.LANCZOS)
    ow, oh = material.cechy.przygotowanie.rozmiar_oryginalny
    sx, sy = mini.width / (ow or obraz.width), mini.height / (oh or obraz.height)
    rys = ImageDraw.Draw(mini)
    for kolor, miejsca in (
        ("#63e6be", [t.prostokat for t in material.cechy.twarze]),
        ("#ffd43b", [l.prostokat for l in material.cechy.logo_obraz]),
        ("#74c0fc", [q.prostokat for q in material.cechy.qr]),
        ("#f783ac", orb),
    ):
        for x, y, w, h in miejsca:
            pole = (round(x * sx), round(y * sy), round((x + w) * sx), round((y + h) * sy))
            rys.rectangle(pole, outline=kolor, width=2)
    with BytesIO() as bufor:
        mini.save(bufor, "JPEG", quality=80)
        dane = b64encode(bufor.getvalue()).decode("ascii")
    return f'<figure><img src="data:image/jpeg;base64,{dane}" alt="{nazwa}"><figcaption>{nazwa}</figcaption></figure>'


def zapisz_raport_html(plik: Path, pary: list[ParaHTML], podsumowanie: str) -> None:
    """Osadza przekodowane miniatury i wyłącznie publiczne metryki porównania."""
    with plik.open("w", encoding="utf-8") as strumien:
        strumien.write('''<!doctype html><html lang="pl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<title>Porównanie obrazów</title><style>
body{font:16px system-ui;color:#1a2530;background:#eef2f5;margin:2rem auto;max-width:1120px;padding:0 1rem}
section{background:white;margin:1rem 0;padding:1rem;border:1px solid #bccad6;border-radius:.5rem}
.para{display:grid;grid-template-columns:1fr 1fr;gap:.75rem}figure{margin:0;min-width:0}img{max-width:100%;height:auto}
pre{white-space:pre-wrap;overflow-wrap:anywhere}figcaption{overflow-wrap:anywhere}summary{cursor:pointer}
</style><h1>Porównanie obrazów</h1>
<p>Ramki: twarze (zielone), logo (żółte), QR (niebieskie), wspólny kadr ORB (różowe).</p>''')
        strumien.write(f"<pre>{escape(podsumowanie)}</pre>")
        for i, para in enumerate(pary, 1):
            strumien.write(f"<section><h2>Para {i} · {escape(para.relacja)}</h2><div class=para>")
            for material, strona in ((para.a, "miejsca_a"), (para.b, "miejsca_b")):
                obszary = [p for d in para.wynik.dowody if d.rodzaj == "ten_sam_kadr" for p in getattr(d, strona)]
                strumien.write(_podglad(material, obszary))
            strumien.write("</div>")
            if para.wynik.dowody:
                strumien.write("<ul>")
                for d in para.wynik.dowody:
                    strumien.write(f"<li><strong>{escape(d.rodzaj)} · {escape(d.sila)}</strong> {escape(d.uzasadnienie)}</li>")
                strumien.write("</ul>")
            else:
                strumien.write("<p>Brak dowodów</p>")
            metryki = json.dumps(para.wynik.metryki.model_dump(), ensure_ascii=False, indent=2)
            braki = "\n".join(escape(f"{b.skladnik} {b.powod}") for b in para.wynik.braki)
            strumien.write(f"<details><summary>Metryki i braki</summary><pre>{escape(metryki)}\n{braki}</pre></details></section>")
        strumien.write("</html>")
