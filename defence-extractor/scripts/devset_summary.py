"""Per-document view of scored runs, and the facts whose outcome changed between two runs (no model calls).

    python scripts/devset_summary.py weak_a weak_b guard_a          # score per document (after `dx eval`)
    python scripts/devset_summary.py --diff weak_a weak_b           # facts that changed outcome from weak_a to weak_b
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RUNS = Path(__file__).resolve().parents[1] / "runs"


def _docs(run: str) -> list[dict]:
    sc = json.loads((RUNS / run / "eval.json").read_text())
    key = next(k for k, v in sc.items() if isinstance(v, list) and v and isinstance(v[0], dict) and "doc_id" in v[0])
    return [d for d in sc[key] if not d.get("missing_result")]


def summary(run: str) -> None:
    sc = json.loads((RUNS / run / "eval.json").read_text())
    c = sc["core"]
    print(f"{run}: core {c['matched']}/{c['n']} = {c['recall']}  wrong product {c['misattributed']}  unresolved "
          f"{c['unresolved']}  missed {c['missed']}  unit errors {sc.get('unit_errors', '-')}")
    for d in _docs(run):
        k, t = d["core"], d.get("text", {})
        pos = " ".join(f"{p}:{v['matched']}/{v['n']}" for p, v in d.get("by_pos", {}).items() if v["n"])
        print(f"   {d['doc_id']:24s} core {k['matched']:3d}/{k['n']:3d} wrong {k['misattributed']} unres {k['unresolved']}"
              f" | text {t.get('matched')}/{t.get('n')} | {pos}")


def diff(base: str, new: str) -> None:
    rows = {}
    for run in (base, new):
        rows[run] = {d["doc_id"]: {r["gold"]["idx"]: r for r in d.get("rows", [])} for d in _docs(run)}
    for doc, nrows in sorted(rows[new].items()):
        brows = rows[base].get(doc, {})
        changed = [i for i in nrows if (brows.get(i) or {}).get("outcome") != nrows[i]["outcome"]]
        if not changed:
            continue
        print(f"##### {doc}")
        for i in sorted(changed):
            g, p = nrows[i]["gold"], nrows[i].get("pred") or {}
            print(f"  [{(brows.get(i) or {}).get('outcome', '-'):>13} -> {nrows[i]['outcome']:<13}] {g['p']} {g.get('param')} = "
                  f"{g['v'][:40]!r} | now: {p.get('entity_id')} {str(p.get('value'))[:40]!r}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--diff"] and len(args) == 3:
        diff(args[1], args[2])
    else:
        for r in args:
            summary(r)
