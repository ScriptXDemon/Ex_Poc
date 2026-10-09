"""Collect the numbers for the final report: benchmark runs, corpus gold scores, corpus statistics, true per-doc cost.

usage: python scripts/final_summary.py corpus1 bench1,bench2,bench3,bench4 > runs/final_summary.json
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

RUNS = Path("runs")


def bench_row(run: str) -> dict | None:
    p = RUNS / run / "eval.json"
    if not p.exists():
        return None
    e = json.loads(p.read_text())
    return {"run": run, "docs": e["scored"], "core": e["core"], "text": e["text"], "param": e["param"],
            "verbatim_share": e["verbatim_share"], "extras": e["extras"], "other_product_facts": e["other_product_facts"],
            "negative_violations": e["negative_violations"], "ledger_open": e["ledger_open"], "old_key": e["old_key"],
            "cost_usd": e["cost_usd"]}


def true_costs(run: str) -> dict[str, float]:
    """Cost per document over the whole run log (includes calls made before worker restarts / cache re-runs)."""
    out: dict[str, float] = collections.defaultdict(float)
    log = RUNS / run / "llm_calls.jsonl"
    for line in log.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        out[r.get("doc_id") or "?"] += r.get("cost_usd") or 0.0
    return out


def main() -> None:
    corpus, benches = sys.argv[1], sys.argv[2].split(",")
    sel = {r["doc_id"]: r for r in json.loads((RUNS / corpus / "selection.json").read_text())}
    costs = true_costs(corpus)
    stats = json.loads((RUNS / corpus / "run_stats.json").read_text()) if (RUNS / corpus / "run_stats.json").exists() else {}
    props = json.loads((RUNS / corpus / "ontology_proposals.json").read_text()) if (RUNS / corpus / "ontology_proposals.json").exists() else []
    gold = json.loads((RUNS / corpus / "eval.json").read_text()) if (RUNS / corpus / "eval.json").exists() else None

    done, failed, capped, by_group = [], [], [], collections.defaultdict(lambda: {"docs": 0, "cost": 0.0, "seconds": 0.0})
    budget_skipped_items = 0
    degraded: list[str] = []
    for d in sorted((RUNS / corpus / "docs").glob("*")):
        rp = d / "result.json"
        st = json.loads((d / "state.json").read_text()) if (d / "state.json").exists() else {}
        if not rp.exists():
            if st.get("status") == "failed":
                failed.append({"doc_id": d.name, "errors": st.get("errors", [])[-2:]})
            continue
        r = json.loads(rp.read_text())
        ref = sel.get(d.name, {})
        key = f"L{ref.get('level')}-{ref.get('format')}"
        g = by_group[key]
        g["docs"] += 1
        g["cost"] += costs.get(d.name, 0.0)
        g["seconds"] += r["execution"]["duration_s"]
        done.append(d.name)
        errs = r["execution"].get("errors") or []
        if any("document budget" in e for e in errs):
            capped.append({"doc_id": d.name, "level": ref.get("level"), "format": ref.get("format"), "url": ref.get("url"),
                           "cost": round(costs.get(d.name, 0.0), 3), "note": next(e for e in errs if "document budget" in e)})
        budget_skipped_items += sum(1 for li in (r.get("ledger") or []) if li.get("reason") == "not_processed_document_budget")
        if any("BudgetExceeded" in e for e in errs):  # finished while the run budget ran out: some steps were cut short
            degraded.append(d.name)

    gold_docs = []
    if gold:
        for d in gold["docs"]:
            if d.get("missing_result"):
                continue
            ref = sel.get(d["doc_id"], {})
            gold_docs.append({"doc_id": d["doc_id"], "level": ref.get("level"), "format": ref.get("format"), "url": ref.get("url"),
                              "core": d["core"], "text": d["text"], "param": d["param"], "extras": d["extras"],
                              "other_product_facts": d["other_product_facts"], "negative_violations": len(d["negative_violations"]),
                              "cost": round(costs.get(d["doc_id"], 0.0), 4)})
    total_cost = sum(costs.values())
    selected_by_group = collections.Counter(f"L{r.get('level')}-{r.get('format')}" for r in sel.values())
    out = {
        "corpus_run": corpus, "selected": len(sel), "selected_by_group": dict(selected_by_group),
        "completed": len(done), "failed": failed, "degraded_by_budget_stop": degraded,
        "stopped_midway": sum(1 for d in (RUNS / corpus / "docs").glob("*/state.json")
                              if not (d.parent / "result.json").exists() and json.loads(d.read_text()).get("status") == "budget_exhausted"),
        "total_llm_cost_usd": round(total_cost, 3),
        "cost_per_completed_doc": round(total_cost / max(1, len(done)), 4),
        "by_group_true_cost": {k: {**v, "cost": round(v["cost"], 3), "cost_per_doc": round(v["cost"] / max(1, v["docs"]), 4),
                                   "seconds_per_doc": round(v["seconds"] / max(1, v["docs"]), 1)} for k, v in sorted(by_group.items())},
        "budget_capped_docs": capped, "budget_skipped_ledger_items": budget_skipped_items,
        "run_stats": stats, "ontology_proposals_top": props[:25], "ontology_proposals_total": len(props),
        "gold": None if not gold else {"core": gold["core"], "text": gold["text"], "param": gold["param"],
                                       "verbatim_share": gold["verbatim_share"], "negative_violations": gold["negative_violations"],
                                       "other_product_facts": gold["other_product_facts"], "extras": gold["extras"],
                                       "docs": gold_docs},
        "benchmarks": [b for b in (bench_row(x) for x in benches) if b],
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
