"""Metryki grupowania – czyste funkcje, bez bazy (łatwe do sprawdzenia ręcznie).

Wszystkie funkcje przyjmują przypisania jako słowniki  materiał -> etykieta grupy.
  * pary:  TP – ta sama grupa u nas i w etykietach, FP – połączyliśmy, a nie powinniśmy,
           FN – rozdzieliliśmy materiały jednej kampanii;  precyzja, czułość, F1,
  * ARI (Adjusted Rand Index): zgodność dwóch podziałów skorygowana o przypadek
           (1 – identyczne, 0 – jak losowe, < 0 – gorzej niż losowe).
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
from math import comb
from typing import Callable, Hashable, Iterable


def union_find(ids: Iterable[str], linked: Callable[[str, str], bool]) -> dict[str, str]:
    """Grupy = spójne składowe grafu, w którym krawędź to linked(a, b). Zwraca materiał -> korzeń."""
    ids = list(ids)
    parent = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in combinations(ids, 2):
        if linked(a, b):
            parent[find(a)] = find(b)
    return {i: find(i) for i in ids}


def pair_counts(pred: dict[str, Hashable], gold: dict[str, Hashable]) -> dict[str, int]:
    ids = [i for i in gold if i in pred]
    out = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for a, b in combinations(ids, 2):
        p, g = pred[a] == pred[b], gold[a] == gold[b]
        out["tp" if p and g else "fp" if p else "fn" if g else "tn"] += 1
    return out


def prf(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    """Precyzja/czułość dla par. Brak par pozytywnych -> None (nie udajemy 100%)."""
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision is not None and recall is not None:
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    else:
        f1 = 0.0 if recall is not None else None
    r = lambda x: None if x is None else round(x, 3)  # noqa: E731
    return {"precision": r(precision), "recall": r(recall), "f1": r(f1)}


def adjusted_rand(pred: dict[str, Hashable], gold: dict[str, Hashable]) -> float:
    ids = [i for i in gold if i in pred]
    n = len(ids)
    if n < 2:
        return 1.0
    cells = Counter((pred[i], gold[i]) for i in ids)
    rows = Counter(pred[i] for i in ids)
    cols = Counter(gold[i] for i in ids)
    index = sum(comb(v, 2) for v in cells.values())
    sum_rows = sum(comb(v, 2) for v in rows.values())
    sum_cols = sum(comb(v, 2) for v in cols.values())
    expected = sum_rows * sum_cols / comb(n, 2)
    maximum = (sum_rows + sum_cols) / 2
    if maximum == expected:
        return 1.0 if dict(rows) and len(rows) == len(cols) else 0.0
    return round((index - expected) / (maximum - expected), 3)


def summary(pred: dict[str, Hashable], gold: dict[str, Hashable]) -> dict:
    c = pair_counts(pred, gold)
    return {**c, **prf(c["tp"], c["fp"], c["fn"]), "ari": adjusted_rand(pred, gold),
            "groups_pred": len({pred[i] for i in gold if i in pred}), "groups_gold": len(set(gold.values()))}
