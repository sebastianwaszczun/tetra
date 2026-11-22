"""Ewaluacja grupowania na materiałach z etykietami (data/labels.csv).

Co liczymy (odpowiada punktom walidacji z treści zadania):
  1. jakość grupowania        – pary (precyzja, czułość, F1) i ARI względem etykiet,
  2. przypisanie nowego materiału („wyjmij jeden”): każdy materiał po kolei udaje nowy –
     czy trafia do właściwej kampanii, a materiał z nowej kampanii dostaje „nowy wzorzec”,
  3. nieznana kampania („wyjmij kampanię”): cała kampania udaje nową – czy jej materiałów
     nie doklejamy do innych kampanii,
  4. ablacja – wynik bez każdego modułu i z samym modułem: ile wnosi tekst, OSINT, obraz, AV
     (to też odporność na niekompletne dane: brak strony = brak OSINT, brak nagrania = brak AV),
  5. przegląd progów – F1 i ARI dla progów 0.50–0.95 (uzasadnienie progu ASSIGN),
  6. poziom operatora – jeśli w etykietach wypełniono kolumnę „operator”.

Porównania par liczymy raz (wszystkie pary, dokładnie tym samym kodem co grupowanie) i zapisujemy
w data/eval/pary.json; ablacja i progi tylko przeliczają zapisane wkłady modułów.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Callable

from . import metrics
from .labels import LABELS_FILE, load_labels

EVAL_DIR = Path(os.environ.get("EVAL_DIR", "data/eval"))
MODULE_NAMES = ("text", "osint", "image", "av")
THRESHOLDS = [round(0.5 + 0.05 * i, 2) for i in range(10)]


@dataclass
class Pair:
    a: str
    b: str
    duplicate: bool = False
    contrib: dict[str, float] = field(default_factory=dict)
    op_contrib: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def score(self, use: frozenset[str]) -> float:
        if self.duplicate:
            return 1.0
        ws = [w for m, w in self.contrib.items() if m in use]
        return round(1 - math.prod(1 - min(w, 0.999) for w in ws), 3) if ws else 0.0

    def operator(self, use: frozenset[str]) -> float:
        s = self.score(use)
        ws = [w for m, w in self.op_contrib.items() if m in use]
        return round(1 - (1 - min(s, 0.999)) * math.prod(1 - min(w, 0.999) for w in ws), 3)


def labeled_materials(labels: dict) -> tuple[dict[str, str], list[str], list[str]]:
    """id materiału -> source (pierwszy materiał z danego źródła), źródła bez materiału, materiały bez etykiety."""
    from fakecollector.record.store import connect
    with connect() as conn:
        rows = conn.execute("SELECT id, source FROM materials ORDER BY created_at").fetchall()
    ids: dict[str, str] = {}
    seen: set[str] = set()
    unlabeled = []
    for mid, src in rows:
        if src in labels and src not in seen:
            ids[mid] = src
            seen.add(src)
        elif src not in labels and str(src).startswith("data/"):
            unlabeled.append(src)
    return ids, sorted(set(labels) - seen), sorted(set(unlabeled))


def collect_pairs(ids: list[str], fresh: bool = False, progress: Callable | None = None) -> dict[tuple, Pair]:
    """Porównanie wszystkich par (z zapisem wyników, żeby kolejne uruchomienia były szybkie)."""
    from fakecollector.grouping.campaigns import METHOD
    from fakecollector.grouping.compare import MODULES, compare_materials, load_context
    cache_file = EVAL_DIR / "pary.json"
    key = {"method": METHOD, "modules": MODULES, "ids": sorted(ids)}
    if not fresh and cache_file.is_file():
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if data.get("key") == json.loads(json.dumps(key)):
            return {(p["a"], p["b"]): Pair(**p) for p in data["pairs"]}
    ctx, feats = load_context(), {}
    pairs: dict[tuple, Pair] = {}
    todo = list(combinations(sorted(ids), 2))
    for n, (a, b) in enumerate(todo, 1):
        r = compare_materials(a, b, feats, ctx)
        pairs[(a, b)] = Pair(
            a, b, duplicate="ten sam materiał zgłoszony ponownie (duplikat)" in r.reasons,
            contrib={m: float(v.get("contribution", 0) or 0) for m, v in r.modules.items() if "contribution" in v},
            op_contrib={m: float(v.get("operator_contribution", 0) or 0) for m, v in r.modules.items()
                        if v.get("operator_contribution")},
            reasons=r.reasons[:4] + [f"operator: {x}" for x in r.operator_reasons[:2]])
        if progress:
            progress(n, len(todo))
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"key": key, "pairs": [asdict(p) for p in pairs.values()]},
                                     ensure_ascii=False), encoding="utf-8")
    return pairs


def _getter(pairs: dict[tuple, Pair], use: frozenset[str], level: str = "campaign") -> Callable[[str, str], float]:
    def s(a: str, b: str) -> float:
        p = pairs.get((a, b)) or pairs.get((b, a))
        if p is None:
            return 0.0
        return p.score(use) if level == "campaign" else p.operator(use)
    return s


def evaluate(ids: list[str], gold: dict[str, str], s: Callable[[str, str], float], threshold: float,
             details: bool = False) -> dict[str, Any]:
    pred = metrics.union_find(ids, lambda a, b: s(a, b) >= threshold)
    out: dict[str, Any] = {"pairs": metrics.summary(pred, gold)}

    known = {"total": 0, "correct": 0, "new_instead": 0, "wrong_campaign": 0}
    new = {"total": 0, "correct": 0, "merged": 0}
    loo_errors = []
    for m in ids:
        others = [o for o in ids if o != m]
        best = max(others, key=lambda o: s(m, o), default=None)
        best_s = s(m, best) if best else 0.0
        has_campaign = any(gold[o] == gold[m] for o in others)
        if best_s >= threshold:
            groups = metrics.union_find(others, lambda a, b: s(a, b) >= threshold)
            members = [o for o in others if groups[o] == groups[best]]
            label = Counter(gold[o] for o in members).most_common(1)[0][0]
            ok = has_campaign and label == gold[m]
        else:
            label, ok = None, not has_campaign
        bucket = known if has_campaign else new
        bucket["total"] += 1
        if ok:
            bucket["correct"] += 1
        elif has_campaign:
            known["new_instead" if label is None else "wrong_campaign"] += 1
        else:
            new["merged"] += 1
        if not ok:
            loo_errors.append({"material": m, "kampania": gold[m], "przypisano_do": label,
                               "najblizszy": best, "wynik": round(best_s, 3)})
    out["assign_known"] = {**known, "accuracy": round(known["correct"] / known["total"], 3) if known["total"] else None}
    out["assign_new"] = {**new, "accuracy": round(new["correct"] / new["total"], 3) if new["total"] else None}

    held = [c for c, k in Counter(gold.values()).items() if k >= 2]
    merged, total = 0, 0
    loco_errors = []
    for c in held:
        rest = [o for o in ids if gold[o] != c]
        for m in (i for i in ids if gold[i] == c):
            total += 1
            best = max(rest, key=lambda o: s(m, o), default=None)
            if best and s(m, best) >= threshold:
                merged += 1
                loco_errors.append({"material": m, "kampania": c, "doklejony_do": gold[best],
                                    "przez": best, "wynik": round(s(m, best), 3)})
    out["unseen_campaign"] = {"materials": total, "false_merges": merged,
                              "kept_apart": round(1 - merged / total, 3) if total else None}
    if details:
        fp, fn = [], []
        for a, b in combinations(ids, 2):
            same_pred, same_gold = pred[a] == pred[b], gold[a] == gold[b]
            if same_pred and not same_gold:
                fp.append({"a": a, "b": b, "wynik": s(a, b), "posrednio": s(a, b) < threshold})
            elif same_gold and not same_pred:
                fn.append({"a": a, "b": b, "wynik": s(a, b)})
        out["errors"] = {"false_links": fp, "missed_links": sorted(fn, key=lambda x: -x["wynik"]),
                         "assign": loo_errors, "unseen": loco_errors}
        out["clusters"] = sorted(([i for i in ids if pred[i] == r] for r in set(pred.values())), key=len, reverse=True)
    return out


def run(threshold: float | None = None, fresh: bool = False, labels_path: Path | str = LABELS_FILE,
        progress: Callable | None = None, save: bool = True) -> dict[str, Any]:
    from fakecollector.grouping.campaigns import METHOD
    from fakecollector.grouping.compare import ASSIGN, MODULES, REVIEW
    t0 = time.time()
    threshold = ASSIGN if threshold is None else threshold
    labels = load_labels(labels_path)
    id_src, missing, unlabeled = labeled_materials(labels)
    ids = sorted(id_src)
    if len(ids) < 2:
        raise ValueError("Za mało materiałów z etykietami (potrzeba co najmniej 2).")
    gold = {i: labels[id_src[i]].campaign for i in ids}
    pairs = collect_pairs(ids, fresh=fresh, progress=progress)
    every = frozenset(MODULE_NAMES)
    main = evaluate(ids, gold, _getter(pairs, every), threshold, details=True)

    def brief(r: dict) -> dict:
        return {"f1": r["pairs"]["f1"], "precision": r["pairs"]["precision"], "recall": r["pairs"]["recall"],
                "ari": r["pairs"]["ari"], "assign_known": r["assign_known"]["accuracy"],
                "assign_new": r["assign_new"]["accuracy"], "kept_apart": r["unseen_campaign"]["kept_apart"]}

    ablation = {}
    for m in MODULE_NAMES:
        ablation[f"bez_{m}"] = brief(evaluate(ids, gold, _getter(pairs, every - {m}), threshold))
        ablation[f"tylko_{m}"] = brief(evaluate(ids, gold, _getter(pairs, frozenset({m})), threshold))
    sweep = []
    for t in THRESHOLDS:
        r = evaluate(ids, gold, _getter(pairs, every), t)
        sweep.append({"threshold": t, **brief(r)})
    present = Counter(m for p in pairs.values() for m in p.contrib)

    op_gold = {i: labels[id_src[i]].operator for i in ids if labels[id_src[i]].operator}
    operator = None
    if len(op_gold) >= 2:
        op_ids = sorted(op_gold)
        pred = metrics.union_find(op_ids, lambda a, b: _getter(pairs, every, "operator")(a, b) >= threshold)
        operator = metrics.summary(pred, op_gold)

    src = lambda i: id_src.get(i, i)  # noqa: E731
    for e in main["errors"]["false_links"] + main["errors"]["missed_links"]:
        p = pairs.get((e["a"], e["b"])) or pairs.get((e["b"], e["a"]))
        e["przeslanki"] = p.reasons if p else []
        e["a"], e["b"] = src(e["a"]), src(e["b"])
    for e in main["errors"]["assign"]:
        e["material"], e["najblizszy"] = src(e["material"]), src(e["najblizszy"]) if e["najblizszy"] else None
    for e in main["errors"]["unseen"]:
        e["material"], e["przez"] = src(e["material"]), src(e["przez"])
    main["clusters"] = [[src(i) for i in g] for g in main["clusters"]]

    verified = sum(1 for i in ids if labels[id_src[i]].verified)
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": METHOD, "threshold": threshold, "review_threshold": REVIEW,
        "weights": {m: {k: v for k, v in cfg.items() if k != "label"} for m, cfg in MODULES.items()},
        "materials": len(ids), "campaigns_gold": len(set(gold.values())),
        "campaigns_gold_multi": sum(1 for k in Counter(gold.values()).values() if k >= 2),
        "labels_verified": verified,
        "warning": None if verified == len(ids) else
        f"Sprawdzono {verified} z {len(ids)} etykiet – niesprawdzony szkic to kopia wyniku systemu, "
        "więc metryki są zawyżone. Przejrzyj data/labels.csv i wpisz „tak” w kolumnie sprawdzone.",
        "pairs_compared": len(pairs), "module_pairs_with_data": dict(present),
        "main": main, "ablation": ablation, "thresholds": sweep, "operator": operator,
        "labels_without_material": missing, "materials_without_label": unlabeled,
        "seconds": round(time.time() - t0, 1),
    }
    if save:
        EVAL_DIR.mkdir(parents=True, exist_ok=True)
        (EVAL_DIR / "raport.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        (EVAL_DIR / "raport.md").write_text(to_markdown(report), encoding="utf-8")
    return report


def last_report() -> dict[str, Any] | None:
    f = EVAL_DIR / "raport.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


LABEL_PL = {"text": "tekst", "osint": "OSINT", "image": "obraz", "av": "audio i wideo"}


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.0f}%"


def to_markdown(r: dict[str, Any]) -> str:
    m = r["main"]
    p = m["pairs"]
    lines = [f"# Ewaluacja grupowania ({r['created_at'][:16].replace('T', ' ')})", ""]
    if r.get("warning"):
        lines += [f"> **Uwaga:** {r['warning']}", ""]
    lines += [
        f"Materiałów z etykietami: **{r['materials']}**, kampanii w etykietach: **{r['campaigns_gold']}** "
        f"(w tym {r['campaigns_gold_multi']} z ≥2 materiałami). Próg kampanii: **{r['threshold']}**.", "",
        "## Wynik główny", "",
        "| Miara | Wartość | Co znaczy |", "|---|---|---|",
        f"| Precyzja par | {_pct(p['precision'])} | z połączonych par – ile słusznie ({p['tp']} dobrych, {p['fp']} błędnych) |",
        f"| Czułość par | {_pct(p['recall'])} | z par jednej kampanii – ile połączyliśmy ({p['fn']} przeoczonych) |",
        f"| F1 par | {_pct(p['f1'])} | średnia harmoniczna precyzji i czułości |",
        f"| ARI | {p['ari']} | zgodność podziału z etykietami (1 = identyczny, 0 = losowy) |",
        f"| Nowy wariant znanej kampanii | {_pct(m['assign_known']['accuracy'])} | "
        f"trafia do właściwej kampanii ({m['assign_known']['correct']}/{m['assign_known']['total']}) |",
        f"| Materiał z nowej kampanii | {_pct(m['assign_new']['accuracy'])} | "
        f"dostaje „nowy wzorzec” ({m['assign_new']['correct']}/{m['assign_new']['total']}) |",
        f"| Nieznana kampania | {_pct(m['unseen_campaign']['kept_apart'])} | "
        f"jej materiały nie zostały doklejone do innych ({m['unseen_campaign']['false_merges']} doklejonych) |",
        "", "## Ablacja – ile wnosi każdy moduł", "",
        "| Wariant | F1 | ARI | Nowy wariant → dobra kampania | Nowa kampania osobno |", "|---|---|---|---|---|",
        f"| wszystkie moduły | {_pct(p['f1'])} | {p['ari']} | {_pct(m['assign_known']['accuracy'])} | "
        f"{_pct(m['unseen_campaign']['kept_apart'])} |",
    ]
    for key, v in r["ablation"].items():
        kind, mod = key.split("_", 1)
        name = ("bez " if kind == "bez" else "tylko ") + LABEL_PL.get(mod, mod)
        lines.append(f"| {name} | {_pct(v['f1'])} | {v['ari']} | {_pct(v['assign_known'])} | {_pct(v['kept_apart'])} |")
    lines += ["", "## Dobór progu", "", "| Próg | Precyzja | Czułość | F1 | ARI |", "|---|---|---|---|---|"]
    for t in r["thresholds"]:
        mark = " ←" if abs(t["threshold"] - r["threshold"]) < 1e-9 else ""
        lines.append(f"| {t['threshold']:.2f}{mark} | {_pct(t['precision'])} | {_pct(t['recall'])} | "
                     f"{_pct(t['f1'])} | {t['ari']} |")
    if r.get("operator"):
        o = r["operator"]
        lines += ["", "## Poziom operatora", "",
                  f"Precyzja {_pct(o['precision'])}, czułość {_pct(o['recall'])}, F1 {_pct(o['f1'])}, ARI {o['ari']}."]
    err = m["errors"]
    lines += ["", "## Błędy", ""]
    if not any(err.values()):
        lines.append("Brak błędów na tym zbiorze.")
    for e in sorted(err["false_links"], key=lambda x: x.get("posrednio", False)):
        why = ("pośrednio, przez łańcuch innych materiałów" if e.get("posrednio")
               else "; ".join(e["przeslanki"][:2]))
        lines.append(f"- **błędnie połączone**: {e['a']} ~ {e['b']} (wynik {e['wynik']}): {why}")
    for e in err["missed_links"][:15]:
        lines.append(f"- **przeoczone**: {e['a']} ~ {e['b']} (wynik {e['wynik']})")
    for e in err["assign"]:
        where = e["przypisano_do"] or "nowy wzorzec"
        lines.append(f"- **przypisanie**: {e['material']} (kampania {e['kampania']}) → {where}, "
                     f"najbliższy {e['najblizszy']} ({e['wynik']})")
    for e in err["unseen"]:
        lines.append(f"- **doklejony z nieznanej kampanii**: {e['material']} → {e['doklejony_do']} "
                     f"przez {e['przez']} ({e['wynik']})")
    if r["labels_without_material"] or r["materials_without_label"]:
        lines += ["", "## Braki w danych", ""]
        if r["labels_without_material"]:
            lines.append(f"- etykiety bez materiału w bazie: {', '.join(r['labels_without_material'])}")
        if r["materials_without_label"]:
            lines.append(f"- materiały bez etykiety: {', '.join(r['materials_without_label'])}")
    lines += ["", f"_Porównanych par: {r['pairs_compared']}, czas: {r['seconds']} s, metoda: {r['method']}._", ""]
    return "\n".join(lines)
