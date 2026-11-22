"""Porównanie dwóch materiałów: każdy moduł porównuje swoje cechy, potem łączymy wyniki w jeden.

Łączenie (wyjaśnialne):
  1. każdy moduł daje wynik s (0–1) i pewność c (0–1),
  2. wynik poniżej progu szumu modułu nic nie wnosi – różne strony oszustów zawsze mają trochę
     wspólnego (te same słowa, Cloudflare, ta sama znana twarz):  s' = max(0, (s − próg) / (1 − próg)),
  3. wkład modułu:  w = rzetelność × c × s',
  4. wynik pary:  S = 1 − Π(1 − w)   (jak w module OSINT: jeden pewny dowód wystarcza,
     kilka średnich się wzmacnia, a brakujący moduł nie obniża wyniku – po prostu nic nie dodaje).
Dla przejrzystości zwracamy też średnią ważoną Σ w·c·s / Σ w·c ze wszystkich modułów.

Dwa poziomy powiązań:
  * KAMPANIA – ta sama treść: tekst (szablon), obraz (te same kreacje, twarz lektora), nagranie
    (ta sama ścieżka, głos), OSINT tylko w zakresie „tożsamości” (ta sama domena, ta sama reklama Meta),
  * OPERATOR – ta sama infrastruktura: piksele, konta reklamowe, kit, serwery (OSINT poza „tożsamością”).
    Różne narracje jednego operatora to różne kampanie powiązane relacją „ten sam operator”.
Ślady obecne w dużej części bazy (favicon/obrazki/beacony podszywanego serwisu) pomijamy:
to szablon skopiowanej strony (Gazeta.pl, TVN24), a nie ślad konkretnego operatora.

Dwie reguły dla przypadków z kilkoma stronami (np. DANE/2: artykuł o Brzosce + strona o Olejnik):
  * wspólna domena łączy w kampanię tylko materiały o tym samym zestawie domen stron docelowych;
    przy różnych zestawach (jedna strona z kilku) to ślad operatora, nie kampanii,
  * te same pliki ZASOBÓW stron (zdjęcia w artykułach) to ślad operatora (wspólny zestaw grafik),
    a te same KREACJE, zrzuty i klatki filmów – ślad kampanii.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# rzetelność modułu i próg szumu (wynik, od którego moduł zaczyna coś wnosić)
MODULES = {
    "text":  {"reliability": 1.00, "noise": 0.25, "label": "tekst"},
    "osint": {"reliability": 1.00, "noise": 0.30, "label": "infrastruktura (OSINT)"},
    "av":    {"reliability": 0.95, "noise": 0.30, "label": "audio i wideo"},
    "image": {"reliability": 0.90, "noise": 0.40, "label": "obraz"},
}
ASSIGN = 0.75      # od tego wyniku materiały uznajemy za tę samą kampanię (albo operatora)
REVIEW = 0.50      # od tego – podobne, ale decyzję podejmuje człowiek

# siła dowodu z modułu obrazu (config/obraz.toml [dowody]) -> mnożnik wyniku
IMAGE_STRENGTH = {"silny": 1.0, "sredni": 0.75, "slaby": 0.35}
IMAGE_EVIDENCE_PL = {
    "identyczny_plik": "identyczny plik obrazu", "duplikat_kreacji": "ta sama kreacja",
    "ten_sam_kadr": "ten sam kadr", "podobny_wyglad": "podobny wygląd",
    "wspolne_elementy_szablonu": "wspólne elementy szablonu", "ta_sama_osoba": "ta sama znana osoba",
    "ta_sama_twarz": "ta sama twarz", "wspolne_logo": "wspólne logo", "wspolny_qr": "ten sam kod QR",
}
MAX_IMAGE_PAIRS = 400
UBIQUITY_SHARE = 0.30   # ślad obecny w > 30% materiałów (i w > 3) nie łączy materiałów…
# …ale tylko ślady, które kopiują się razem z wyglądem podszywanego serwisu. Identyfikatory kont
# (Pixel Meta, GA4, GTM), katalog wdrożenia kitu, serwery aktora czy certyfikaty zostają zawsze:
# dominujący operator też może być „wszechobecny” w zbiorze.
CLONE_PRONE = {"favicon_mmh3", "endpoint", "image_hash", "css_hash", "js_hash", "inline_js_hash",
               "path_layout", "dom_simhash", "url_param_schema", "kit_family", "form_signature", "media_host"}
IDENTITY_SCOPES = {"identity"}
DOMAIN_IDENTITY = {"registered_domain", "landing_host"}     # same ID reklamy Meta zostają zawsze
SAME_FILE_EVIDENCE = {"identyczny_plik", "duplikat_kreacji", "ten_sam_kadr"}


@dataclass
class Context:
    """Statystyki bazy potrzebne do porównań: w ilu materiałach występuje dany ślad."""
    n: int = 0
    osint_df: Counter = field(default_factory=Counter)
    image_df: Counter = field(default_factory=Counter)

    def ubiquitous(self, df: int) -> bool:
        return self.n >= 6 and df > max(3, UBIQUITY_SHARE * self.n)


def load_context() -> Context:
    from fakecollector.record.store import connect
    with connect() as conn:
        n = conn.execute("SELECT count(*) FROM materials").fetchone()[0]
        rows = conn.execute("SELECT kind, value, count(*) FROM material_keys "
                            "WHERE kind LIKE 'osint:%' OR kind = 'img_sha256' GROUP BY kind, value").fetchall()
    ctx = Context(n=n)
    for kind, value, cnt in rows:
        if kind == "img_sha256":
            ctx.image_df[value] = cnt
        else:
            ctx.osint_df[(kind[len("osint:"):], value)] = cnt
    return ctx


@dataclass
class PairResult:
    a: str
    b: str
    score: float = 0.0
    weighted_avg: float | None = None
    decision: str = "brak_powiazania"          # ta_sama_kampania / do_weryfikacji / brak_powiazania
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    operator_score: float = 0.0                # kampania LUB wspólna infrastruktura
    operator_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"a": self.a, "b": self.b, "score": self.score, "weighted_avg": self.weighted_avg,
                "decision": self.decision, "operator_score": self.operator_score,
                "same_operator": self.operator_score >= ASSIGN, "modules": self.modules,
                "reasons": self.reasons, "operator_reasons": self.operator_reasons}


def decision_for(score: float) -> str:
    return "ta_sama_kampania" if score >= ASSIGN else "do_weryfikacji" if score >= REVIEW else "brak_powiazania"


# ============================================================================ moduły

def _text(fa: dict, fb: dict) -> dict[str, Any] | None:
    from fakecollector.features import text
    r = text.compare(fa, fb)
    return {"score": r["text_score"], "confidence": r["confidence"], "reasons": r["reasons"],
            "signals": r["signals"]}


def _osint(fa: dict, fb: dict, ctx: Context) -> dict[str, Any] | None:
    from fakecollector.features.osint.correlate import compare
    r = compare(fa, fb, ctx.osint_df)
    best: dict[str, dict] = {}            # w obrębie rodzaju liczy się najmocniejszy ślad
    skipped = []
    for e in r["evidence"]:
        if e["kind"] in CLONE_PRONE and ctx.ubiquitous(ctx.osint_df.get((e["kind"], e["value"]), 0)):
            skipped.append(e["kind"])
            continue
        if e["kind"] not in best or e["contribution"] > best[e["kind"]]["contribution"]:
            best[e["kind"]] = e

    def noisy_or(items):
        return round(1 - math.prod(1 - min(e["contribution"], 0.999) for e in items), 3)

    doms_a = set((fa.get("landing") or {}).get("registered_domains") or [])
    doms_b = set((fb.get("landing") or {}).get("registered_domains") or [])
    same_landing = bool(doms_a) and doms_a == doms_b

    def campaign_level(e: dict) -> bool:
        if e.get("scope") not in IDENTITY_SCOPES:
            return False
        return same_landing or e["kind"] not in DOMAIN_IDENTITY
    ident = [e for e in best.values() if campaign_level(e)]
    oper = [e for e in best.values() if not campaign_level(e)]

    def why(items):
        return [f"{e.get('description') or e['kind']}: {str(e['value'])[:60]}"
                for e in sorted(items, key=lambda e: -e["contribution"]) if e["contribution"] >= 0.3][:4]
    return {"score": noisy_or(best.values()), "confidence": 1.0, "label": r["label"],
            "identity_score": noisy_or(ident), "operator_score": noisy_or(oper),
            "reasons": why(ident), "operator_reasons": why(oper),
            "ignored_common": sorted(set(skipped))}


def _av(fa: dict, fb: dict) -> dict[str, Any] | None:
    from fakecollector.features import av
    r = av.compare(fa, fb)
    if r["av_score"] is None:
        return None
    return {"score": r["av_score"], "confidence": r["confidence"], "reasons": r["reasons"],
            "signals": r["signals"]}


def _image(fa: dict, fb: dict, ctx: Context) -> dict[str, Any] | None:
    """Pary obrazów wybiera wyszukiwarka kandydatów modułu obrazu, porównuje je `porownaj`.

    Wynik kampanii: najlepszy dowód, w którym co najmniej jeden obraz to kreacja, zrzut albo klatka.
    Wynik operatora: najlepszy dowód między dwoma obrazkami ze stron (wspólny zestaw grafik).
    """
    from fakecollector.features.image import porownaj
    from fakecollector.features.image.kandydaci import klucze_wyszukiwania, szukaj_kandydatow
    from fakecollector.record.image_bridge import load_features

    def load(feats: dict) -> list[tuple[str, str, Any]]:
        out, seen = [], set()
        for i, item in enumerate(feats.get("items", [])):
            c = load_features(item)
            key = c.sha256 or f"#{i}"
            if key in seen:
                continue
            seen.add(key)
            out.append((item.get("plik") or item.get("path") or f"obraz {i}", item.get("rola", "kreacja"), c))
        return out

    A, B = load(fa), load(fb)
    if not A or not B:
        return None
    index_b = {str(j): klucze_wyszukiwania(c) for j, (_, _, c) in enumerate(B)}
    best: dict[str, tuple[float, Any, str, str]] = {}      # "campaign" / "operator" -> najlepszy dowód
    pairs = 0
    for name_a, role_a, ca in A:
        for j in szukaj_kandydatow(klucze_wyszukiwania(ca), index_b):
            name_b, role_b, cb = B[int(j)]
            pairs += 1
            if pairs > MAX_IMAGE_PAIRS:
                break
            common = (ctx.ubiquitous(ctx.image_df.get(ca.sha256, 0))
                      or ctx.ubiquitous(ctx.image_df.get(cb.sha256, 0)))
            level = "operator" if role_a == role_b == "obrazek_strony" else "campaign"
            for d in porownaj(ca, cb).dowody:
                if common and d.rodzaj in SAME_FILE_EVIDENCE:
                    continue          # obrazek obecny w dużej części bazy (logo, grafika podszywanego serwisu)
                s = float(d.wynik) * IMAGE_STRENGTH.get(d.sila, 0.35)
                if level not in best or s > best[level][0]:
                    best[level] = (s, d, name_a, name_b)

    def describe(entry):
        s, d, name_a, name_b = entry
        return f"{IMAGE_EVIDENCE_PL.get(d.rodzaj, d.rodzaj)} ({d.sila}): {d.uzasadnienie} – {name_a} i {name_b}"
    out: dict[str, Any] = {"score": 0.0, "confidence": 1.0, "reasons": [], "operator_score": 0.0,
                           "operator_reasons": [], "pairs_compared": pairs}
    if "campaign" in best:
        out.update(score=round(best["campaign"][0], 3), reasons=[describe(best["campaign"])],
                   evidence=best["campaign"][1].rodzaj, strength=best["campaign"][1].sila)
    if "operator" in best:
        out.update(operator_score=round(best["operator"][0], 3), operator_reasons=[describe(best["operator"])])
    return out


# ============================================================================ łączenie

def _lift(name: str, s: float, c: float) -> float:
    cfg = MODULES[name]
    return round(cfg["reliability"] * c * max(0.0, (s - cfg["noise"]) / (1 - cfg["noise"])), 3)


def compare_features(a_id: str, fa: dict[str, Any], b_id: str, fb: dict[str, Any],
                     ctx: Context | None = None, duplicate: bool = False) -> PairResult:
    """Porównanie dwóch materiałów na podstawie zapisanych cech (słowniki moduł -> cechy)."""
    ctx = ctx or Context()
    res = PairResult(a=a_id, b=b_id)
    runners = {"text": lambda x, y: _text(x, y), "osint": lambda x, y: _osint(x, y, ctx),
               "av": _av, "image": lambda x, y: _image(x, y, ctx)}
    for name, fn in runners.items():
        if name not in fa or name not in fb:
            continue
        try:
            r = fn(fa[name], fb[name])
        except Exception as exc:  # noqa: BLE001 – błąd jednego modułu nie psuje porównania
            log.exception("Porównanie %s", name)
            res.modules[name] = {"error": f"{exc.__class__.__name__}: {exc}"}
            continue
        if r is None:
            continue
        c = float(r.get("confidence") or 0)
        if name == "osint":
            # do kampanii liczy się tylko tożsamość (ta sama domena / reklama), reszta – do operatora
            r["contribution"] = _lift(name, float(r["identity_score"]), c)
            r["operator_contribution"] = _lift(name, float(r["operator_score"]), c)
        elif name == "image":
            r["contribution"] = _lift(name, float(r["score"] or 0), c)
            r["operator_contribution"] = _lift(name, float(r.get("operator_score") or 0), c)
        else:
            r["contribution"] = _lift(name, float(r["score"] or 0), c)
        res.modules[name] = r

    contribs = [m["contribution"] for m in res.modules.values() if "contribution" in m]
    res.score = round(1 - math.prod(1 - min(x, 0.999) for x in contribs), 3) if contribs else 0.0
    num = sum(MODULES[n]["reliability"] * m["confidence"] * m["score"] for n, m in res.modules.items() if "score" in m)
    den = sum(MODULES[n]["reliability"] * m["confidence"] for n, m in res.modules.items() if "score" in m)
    res.weighted_avg = round(num / den, 3) if den else None
    if duplicate:
        res.score = 1.0
        res.reasons.append("ten sam materiał zgłoszony ponownie (duplikat)")
    res.decision = decision_for(res.score)
    infra = [m.get("operator_contribution", 0.0) for m in res.modules.values()]
    res.operator_score = round(1 - (1 - min(res.score, 0.999))
                               * math.prod(1 - min(x, 0.999) for x in infra), 3)

    # przesłanki: najpierw moduły, które najbardziej wpłynęły na wynik
    for name, m in sorted(res.modules.items(), key=lambda kv: -kv[1].get("contribution", 0)):
        if m.get("contribution", 0) <= 0:
            continue
        for r in m.get("reasons", [])[:3]:
            res.reasons.append(f"{MODULES[name]['label']}: {r}")
    for name, prefix in (("osint", "infrastruktura"), ("image", "wspólne grafiki stron")):
        m = res.modules.get(name, {})
        if m.get("operator_contribution", 0) > 0:
            res.operator_reasons += [f"{prefix}: {r}" for r in m.get("operator_reasons", [])[:3]]
    return res


def compare_materials(a_id: str, b_id: str, cache: dict[str, dict] | None = None,
                      ctx: Context | None = None) -> PairResult:
    """Porównanie dwóch materiałów zapisanych w bazie."""
    from fakecollector.record import load_features, load_summary
    cache = cache if cache is not None else {}
    ctx = ctx or load_context()
    for mid in (a_id, b_id):
        if mid not in cache:
            cache[mid] = load_features(mid)
    sa, sb = load_summary(a_id) or {}, load_summary(b_id) or {}
    duplicate = sa.get("duplicate_of") == b_id or sb.get("duplicate_of") == a_id or (
        bool(sa.get("fingerprint")) and sa.get("fingerprint") == sb.get("fingerprint"))
    return compare_features(a_id, cache[a_id], b_id, cache[b_id], ctx, duplicate)
