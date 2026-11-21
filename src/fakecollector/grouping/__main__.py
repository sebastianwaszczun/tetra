"""CLI porównania i grupowania.

    python -m fakecollector.grouping rebuild              # kampanie dla całej bazy
    python -m fakecollector.grouping rebuild --dry-run    # tylko pokaż, nic nie zapisuj
    python -m fakecollector.grouping assign <id>          # przypisz jeden materiał
    python -m fakecollector.grouping compare <id_a> <id_b>
    python -m fakecollector.grouping campaigns
    python -m fakecollector.grouping show <campaign_id>
"""
from __future__ import annotations

import argparse
import json
import sys


def _src(mid: str, cache: dict = {}) -> str:  # noqa: B006 – prosty cache na czas jednego uruchomienia
    if mid not in cache:
        from fakecollector.record import load_summary
        s = load_summary(mid) or {}
        cache[mid] = str(s.get("source") or mid)[-24:]
    return cache[mid]


def cmd_rebuild(a) -> int:
    from . import list_campaigns, rebuild
    res = rebuild(write=not a.dry_run,
                  progress=lambda n, total, mid: print(f"  [{n}/{total}] {_src(mid)}", flush=True))
    print(f"\nMateriałów: {res['materials']}, porównanych par: {res['edges']} "
          f"(pewnych: {res['strong_edges']}, do weryfikacji: {res['review_edges']})")
    print(f"Kampanie (≥2 materiały): {res['campaigns']}, do weryfikacji: {res['to_review']}, "
          f"nowe wzorce: {res['new_patterns']}")
    for g in res["clusters"]:
        print("  • " + ", ".join(_src(m) for m in g))
    print(f"\nTen sam operator (wspólna infrastruktura między różnymi kampaniami): "
          f"{len(res['operator_clusters'])} grup, {res['operator_edges']} powiązań")
    for g in res["operator_clusters"]:
        print("  ◦ " + ", ".join(_src(m) for m in g))
    if not a.dry_run:
        print("\nKampanie w bazie:")
        _print_campaigns(list_campaigns())
    return 0


def _print_campaigns(rows) -> None:
    for c in rows:
        op = f"  [operator {c['operator_group']}]" if c.get("operator_group") is not None else ""
        print(f"  [{c['id']:>3}] {c['size']:>2} mat.  {c['label']}{op}")
        if c.get("summary"):
            print(f"         {c['summary']}")


def cmd_assign(a) -> int:
    from . import assign_material
    r = assign_material(a.id)
    print(f"{a.id}: {r['assignment']} → kampania {r['campaign_id']} „{r['campaign']['label']}” "
          f"(wynik {r['score']})")
    if r["merged_campaigns"]:
        print(f"  scalono kampanie: {r['merged_campaigns']}")
    for m in r["matches"]:
        print(f"  {m['score']:.2f} {m['decision']:<17} {_src(m['id'])}")
        for reason in m["reasons"][:3]:
            print(f"        - {reason}")
    return 0


def cmd_compare(a) -> int:
    from . import compare_materials
    r = compare_materials(a.a, a.b)
    if a.json:
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=1, default=str))
        return 0
    print(f"{_src(a.a)} ~ {_src(a.b)}: kampania {r.score} → {r.decision} (średnia ważona {r.weighted_avg}); "
          f"operator {r.operator_score}{' → ten sam operator' if r.operator_score >= 0.75 else ''}")
    for name, m in r.modules.items():
        if "error" in m:
            print(f"  {name:<6} błąd: {m['error']}")
            continue
        extra = ""
        if name == "osint":
            extra = (f"  (tożsamość {m['identity_score']}, infrastruktura {m['operator_score']} -> "
                     f"wkład operatora {m['operator_contribution']})")
            if m.get("ignored_common"):
                extra += f"  pominięte jako powszechne: {', '.join(m['ignored_common'])}"
        elif name == "image" and m.get("operator_score"):
            extra = f"  (grafiki stron {m['operator_score']} -> wkład operatora {m['operator_contribution']})"
        print(f"  {name:<6} wynik {m['score']:.3f}  pewność {m['confidence']:.2f}  wkład {m['contribution']:.3f}{extra}")
    for reason in r.reasons:
        print(f"   - {reason}")
    for reason in r.operator_reasons:
        print(f"   ◦ {reason}")
    return 0


def cmd_campaigns(a) -> int:
    from . import list_campaigns
    _print_campaigns(list_campaigns(min_size=a.min_size))
    return 0


def cmd_show(a) -> int:
    from . import get_campaign
    c = get_campaign(a.id)
    if not c:
        print("Nie ma takiej kampanii.", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(c, ensure_ascii=False, indent=1, default=str))
        return 0
    print(f"Kampania {c['id']}: {c['label']}\n  {c['summary']}")
    print(f"  domeny: {', '.join(c['domains'][:10])}")
    print("  materiały:")
    for m in c["members"]:
        print(f"    {m['id']}  {m['assignment']:<15} {str(m['source'])[-30:]:<30} {m['domain'] or '-'}")
    if c.get("related_campaigns"):
        print("  ten sam operator co kampanie:")
        for rc in c["related_campaigns"]:
            print(f"    [{rc['id']}] {rc['label']} ({rc['size']} mat.)")
            for reason in rc["reasons"][:2]:
                print(f"          ◦ {reason}")
    print("  najmocniejsze powiązania:")
    for link in c["links"][:8]:
        print(f"    {link['score']:.2f}  {_src(link['a'])} ~ {_src(link['b'])}")
        for reason in link["reasons"][:2]:
            print(f"          - {reason}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fakecollector.grouping", description="Porównanie i grupowanie")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("rebuild")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_rebuild)
    p = sub.add_parser("assign")
    p.add_argument("id")
    p.set_defaults(fn=cmd_assign)
    p = sub.add_parser("compare")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_compare)
    p = sub.add_parser("campaigns")
    p.add_argument("--min-size", type=int, default=1)
    p.set_defaults(fn=cmd_campaigns)
    p = sub.add_parser("show")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_show)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
