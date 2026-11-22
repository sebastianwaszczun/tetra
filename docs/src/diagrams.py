"""Ręcznie rysowane schematy do dokumentacji (styl prezentacji TETRA). Wynik: img/*.png.

    python diagrams.py            # wymaga Pillow; fonty w fonts/
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
OUT = HERE / "img"
PX, SS = 230, 2                     # pikseli na cal w wyniku, nadpróbkowanie
INK, RED, LINE, MUTED, BG = "141210", "C9302B", "A89E90", "6B635A", "F3EBDC"


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


class Dia:
    def __init__(self, w, h, seed=1):
        self.w, self.h, self.k = w, h, PX * SS
        self.img = Image.new("RGBA", (int(w * self.k), int(h * self.k)), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.rnd = random.Random(seed)
        self._fonts = {}

    # --- geometria
    def p(self, x, y):
        return (x * self.k, y * self.k)

    def _wob(self, pts, amp):
        a, b = self.rnd.uniform(0, 6.3), self.rnd.uniform(0, 6.3)
        n = len(pts)
        return [(x + amp * (math.sin(2 * math.pi * 2.3 * i / n + a) + .5 * math.sin(2 * math.pi * 5.1 * i / n + b)),
                 y + amp * (math.cos(2 * math.pi * 1.7 * i / n + b) + .5 * math.sin(2 * math.pi * 4.3 * i / n + a)))
                for i, (x, y) in enumerate(pts)]

    def _stroke(self, pts, color, lw):
        self.d.line([self.p(*q) for q in pts], fill=rgb(color), width=max(1, int(lw * self.k)), joint="curve")
        r = lw * self.k / 2
        for q in (pts[0], pts[-1]):
            cx, cy = self.p(*q)
            self.d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rgb(color))

    @staticmethod
    def _dense(pts, step=0.02):
        out = []
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            n = max(2, int(math.hypot(x1 - x0, y1 - y0) / step))
            out += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n)]
        return out + [pts[-1]]

    def _rrect(self, x, y, w, h, r):
        pts = []
        for (cx, cy, a0) in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
            pts += [(cx + r * math.cos(math.radians(a0 + 90 * i / 8)), cy + r * math.sin(math.radians(a0 + 90 * i / 8)))
                    for i in range(9)]
        pts.append(pts[0])
        return self._dense(pts)

    # --- elementy
    def box(self, x, y, w, h, label=None, color=INK, fill=None, text=None, size=12, lw=0.024, tick=True, r=0.06):
        pts = self._wob(self._rrect(x, y, w, h, r), 0.006)
        if fill:
            self.d.polygon([self.p(*q) for q in pts], fill=rgb(fill))
        self._stroke(pts, color, lw)
        if tick:
            self._stroke([(x + .02, y - .012), (x + min(.25, w * .3), y - .006)], color, lw * .9)
        if label:
            self.text(x + w / 2, y + h / 2, label, size, text or (BG if fill else color))
        return (x, y, w, h)

    def group(self, x, y, w, h, label=None, color=RED):
        self.box(x, y, w, h, color=color, lw=0.02, tick=False, r=0.05)
        if label:
            self.text(x + .1, y + .13, label, 12, color, anchor="lm")

    def line(self, pts, color=INK, lw=0.02):
        self._stroke(self._wob(self._dense(pts), 0.003), color, lw)

    def arrow(self, x1, y1, x2, y2, color=INK, lw=0.02, head=0.08, bend=0.0):
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        nx, ny = -(y2 - y1), x2 - x1
        ln = math.hypot(nx, ny) or 1
        cx, cy = mx + nx / ln * bend, my + ny / ln * bend
        pts = [((1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t * t * x2, (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t * t * y2)
               for t in (i / 30 for i in range(31))]
        self._stroke(self._wob(pts, 0.003), color, lw)
        if head:
            ang = math.atan2(y2 - pts[-4][1], x2 - pts[-4][0])
            for s in (1, -1):
                a = ang + math.pi - s * 0.55
                self._stroke([(x2 + head * math.cos(a), y2 + head * math.sin(a)), (x2, y2)], color, lw)

    def circle(self, cx, cy, r, color=INK, fill=None, lw=0.018):
        pts = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in (2 * math.pi * i / 48 + .4 for i in range(52))]
        pts = self._wob(pts, r * 0.05)
        if fill:
            self.d.polygon([self.p(*q) for q in pts], fill=rgb(fill))
        self._stroke(pts, color, lw)

    def font(self, size, face="Caveat.ttf", bold=True):
        key = (size, face, bold)
        if key not in self._fonts:
            f = ImageFont.truetype(str(HERE / "fonts" / face), int(size / 72 * self.k))
            try:
                f.set_variation_by_name("Bold" if bold else "Regular")
            except Exception:
                pass
            self._fonts[key] = f
        return self._fonts[key]

    def text(self, x, y, t, size=12, color=INK, anchor="mm", face="Caveat.ttf", bold=True):
        """Tekst; znak „→” rysujemy fontem Inter (Caveat go nie ma)."""
        f, fa = self.font(size, face, bold), self.font(size * .8, "Inter.ttf", False)
        lines = t.split("\n")
        lh = size / 72 * 1.08
        y0 = y - lh * (len(lines) - 1) / 2 if anchor[1] == "m" else y
        for i, ln in enumerate(lines):
            parts = [(seg, f) for seg in ln.split("→")]
            runs = []
            for j, (seg, ff) in enumerate(parts):
                if j:
                    runs.append(("→", fa))
                if seg:
                    runs.append((seg, ff))
            widths = [ff.getlength(r) / self.k for r, ff in runs]
            total = sum(widths)
            xs = x - total / 2 if anchor[0] == "m" else (x - total if anchor[0] == "r" else x)
            for (r, ff), wdt in zip(runs, widths):
                self.d.text(self.p(xs, y0 + i * lh), r, font=ff, fill=rgb(color), anchor="l" + anchor[1])
                xs += wdt

    def save(self, name):
        OUT.mkdir(exist_ok=True)
        self.img.resize((int(self.w * PX), int(self.h * PX)), Image.LANCZOS).save(OUT / f"{name}.png", optimize=True)


def mr(b):
    return (b[0] + b[2] + .02, b[1] + b[3] / 2)


def ml(b):
    return (b[0] - .03, b[1] + b[3] / 2)


def chain(d, boxes, **kw):
    for a, b in zip(boxes, boxes[1:]):
        d.arrow(*mr(a), *ml(b), **kw)


# ============================================================================ schematy
def potok():
    d = Dia(7.0, 2.75, seed=3)
    h, y1 = .4, .55
    a = d.box(.05, y1, .85, h, "wejście")
    b = d.box(1.2, y1, 1.25, h, "preprocessing")
    d.group(2.75, .12, 1.55, 1.33, "moduły cech")
    mods = [d.box(2.9, .42 + i * .25, 1.25, .19, t, size=10, tick=False, r=.04)
            for i, t in enumerate(["tekst", "OSINT", "obraz", "audio i wideo"])]
    c = d.box(4.6, y1, 1.0, h, "rekord")
    d.arrow(*mr(a), *ml(b))
    for m in mods:
        d.arrow(*mr(b), 2.72, m[1] + m[3] / 2, lw=.016, head=.06)
        d.arrow(m[0] + m[2] + .02, m[1] + m[3] / 2, 4.57, y1 + h / 2, lw=.016, head=.0001)
    d.arrow(4.33, y1 + h / 2, *ml(c))
    d.text(5.1, y1 + h + .2, "Postgres + pgvector", 10.5, MUTED)
    y2 = 1.95
    e = d.box(.05, y2, 1.1, h, "kandydaci")
    f = d.box(1.45, y2, 1.5, h, "porównanie par")
    g = d.box(3.25, y2, 1.85, h, "kampanie + operatorzy")
    k = d.box(5.4, y2, 1.5, h, "GUI · API · Neo4j", fill=INK)
    d.line([(c[0] + c[2] / 2, y1 + h + .37), (c[0] + c[2] / 2, 1.62), (.6, 1.62)], lw=.02)
    d.arrow(.6, 1.62, .6, y2 - .03, lw=.02)
    chain(d, [e, f, g, k])
    for bx, t in ((e, "klucze + wektory"), (f, "wkłady modułów"), (g, "spójne składowe")):
        d.text(bx[0] + bx[2] / 2, y2 + h + .17, t, 10.5, MUTED)
    d.text(6.15, 1.35, "przesłanki\nprzy każdym\npowiązaniu", 11, RED)
    d.arrow(6.15, 1.62, 6.15, y2 - .04, RED, lw=.016, head=.06)
    d.save("potok")


def decyzja():
    d = Dia(7.0, 2.25, seed=5)
    h, yc = .4, 1.1
    a = d.box(.05, yc - h / 2, 1.15, h, "nowy materiał")
    b = d.box(1.5, yc - h / 2, .95, h, "duplikat?")
    c = d.box(2.75, yc - h / 2, 1.0, h, "kandydaci")
    e = d.box(4.05, yc - h / 2, 1.1, h, "wynik S")
    chain(d, [a, b, c, e])
    d.arrow(1.97, yc + h / 2 + .03, 1.97, 1.82, RED, lw=.018, head=.07)
    d.text(1.97, 1.97, "tak → S = 1,0", 11, RED)
    outs = [("≥ 0,75  przypisany", INK, INK), ("pasuje do kilku → scalenie", INK, None),
            ("0,50–0,75  do weryfikacji", INK, None), ("< 0,50  nowy wzorzec", RED, None)]
    for i, (t, col, fill) in enumerate(outs):
        y = .18 + i * .5
        d.box(5.5, y, 1.45, .34, t, color=col, fill=fill, size=10, tick=False)
        d.arrow(5.18, yc, 5.47, y + .17, lw=.016, head=.06)
    d.save("decyzja")


def uslugi():
    d = Dia(7.0, 2.3, seed=7)
    h = .38
    pg = d.box(.05, .25, 1.15, h, "postgres")
    nj = d.box(.05, 1.0, 1.15, h, "neo4j")
    ini = d.box(1.55, .62, 1.0, h, "init")
    d.text(2.05, .38, "schemat, modele", 10.5, MUTED)
    api = d.box(2.95, .62, .9, h, "api")
    seed = d.box(2.95, 1.45, .9, h, "seed")
    app = d.box(1.55, 1.45, 1.0, h, "app", )
    web = d.box(4.3, .25, 1.2, h, "web  :8080", fill=INK)
    sch = d.box(4.3, 1.0, 1.2, h, "scheduler")
    for s in (pg, nj):
        d.arrow(*mr(s), *ml(ini), lw=.016, head=.06)
    d.arrow(*mr(ini), *ml(api))
    d.arrow(2.05, ini[1] + h + .02, 2.05, app[1] - .03, lw=.016, head=.06)
    d.arrow(2.4, ini[1] + h + .02, 3.15, seed[1] - .03, lw=.016, head=.06)
    d.arrow(*mr(api), *ml(web), lw=.016, head=.06)
    d.arrow(*mr(api), *ml(sch), lw=.016, head=.06)
    d.text(2.05, 2.05, "JupyterLab :8888", 10.5, MUTED)
    d.text(3.4, 2.05, "paczka z data/ (raz)", 10.5, MUTED)
    d.text(4.9, 1.6, "co 3 h → /analyze", 10.5, MUTED)
    d.group(5.85, .12, 1.1, 1.6, "przeglądarka")
    d.text(6.4, .7, "GUI\nlocalhost:8080", 11, INK)
    d.text(6.4, 1.3, "API\n:8000/docs", 11, INK)
    d.arrow(5.53, web[1] + h / 2, 5.82, .7, RED, lw=.016, head=.06)
    d.save("uslugi")


def wdrozenie():
    d = Dia(7.0, 1.25, seed=9)
    h, y = .4, .3
    a = d.box(.05, y, 1.05, h, "push → main")
    b = d.box(1.4, y, 1.35, h, "GitHub Actions")
    c = d.box(3.05, y, .85, h, "SSH")
    e = d.box(4.2, y, 1.25, h, "VPS: compose")
    f = d.box(5.75, y, 1.2, h, "tetracore.pl", fill=INK)
    chain(d, [a, b, c, e, f])
    d.text(2.08, y + h + .2, "kontrola: składnia,\ncompose, build frontu", 10, MUTED)
    d.text(4.83, y + h + .2, "Caddy: HTTPS\nLet's Encrypt", 10, MUTED)
    d.save("wdrozenie")


def operatorzy():
    d = Dia(7.0, 3.0, seed=11)

    def op(cx, cy, name, nodes):
        d.box(cx - .45, cy - .2, .9, .4, name, color=RED, fill=RED, size=12, tick=False)
        for x, y, t, big, where in nodes:
            ang = math.atan2(y - cy, x - cx)
            d.line([(cx + .42 * math.cos(ang), cy + .19 * math.sin(ang)),
                    (x - .1 * math.cos(ang), y - .1 * math.sin(ang))], RED, .014)
            d.circle(x, y, .1 if big else .07, INK, fill=INK if big else BG)
            off = {"r": (.16, 0, "lm"), "l": (-.16, 0, "rm"), "t": (0, -.24, "mm"), "b": (0, .24, "mm")}[where]
            d.text(x + off[0], y + off[1], t, 10.5, INK, anchor=off[2])
    op(2.15, 1.3, "operator A", [
        (2.15, .3, "Brzoska (6)", True, "r"), (3.35, .7, "napaść (2)", True, "r"),
        (3.45, 1.6, "FinNews (2)", True, "r"), (3.0, 2.3, "Olejnik – premia", False, "r"),
        (1.35, 2.5, "Olejnik – samochód", False, "r"), (.9, 1.3, "T. Lis", False, "l"),
        (1.15, .45, "Glowpixel", False, "l")])
    op(5.65, 1.05, "operator B", [
        (4.85, .4, "Kardivix", False, "t"), (6.45, .4, "Metabo RX", False, "t"),
        (4.85, 1.7, "Uromexil", False, "b"), (6.45, 1.7, "Cactus Recipe", False, "b")])
    for i in range(7):
        d.circle(4.65 + i * .32, 2.5, .06, LINE, fill=BG)
    d.text(5.6, 2.78, "7 pojedynczych wzorców", 10.5, MUTED)
    d.circle(.12, 2.86, .06, INK, fill=INK)
    d.text(.24, 2.86, "kampania z ≥ 2 materiałami", 10, MUTED, anchor="lm")
    d.circle(2.0, 2.86, .05, INK, fill=BG)
    d.text(2.1, 2.86, "jeden materiał", 10, MUTED, anchor="lm")
    d.save("operatorzy")


if __name__ == "__main__":
    for fn in (potok, decyzja, uslugi, wdrozenie, operatorzy):
        fn()
    print("ok:", sorted(p.name for p in OUT.glob("*.png")))
