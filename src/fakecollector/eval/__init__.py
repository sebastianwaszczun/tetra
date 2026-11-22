"""Ewaluacja grupowania: etykiety (data/labels.csv), metryki, przypisywanie nowych materiałów, ablacja.

    from fakecollector.eval import run, last_report
    report = run()                 # raport w data/eval/raport.json i raport.md
"""
from .labels import draft_labels, load_labels
from .run import evaluate, last_report, run, to_markdown

__all__ = ["run", "last_report", "evaluate", "to_markdown", "draft_labels", "load_labels"]
