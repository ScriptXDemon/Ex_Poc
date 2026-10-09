"""Large-document run report: accuracy by position, wrong-owner rate, dynamic-spec linkage, structure / vision / budget
facts per document. Reads runs/<run>/eval.json (from `dx eval <run> --gold eval/gold_large`) and the per-document
state / result files. Stdlib only.

    python scripts/large_report.py <run_id> [--json out.json]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DYN = ("dynamic_ok", "dynamic_other")


def main(argv: list[str]) -> None:
    run = argv[0]
    rd = ROOT / "runs" / run
    ev = json.loads((rd / "eval.json").read_text())
    rows_out = []
    tot = Counter()
    pos_tot: dict[str, Counter] = {}
    for d in ev["docs"]:
        did = d["doc_id"]
        sp = rd / "docs" / did / "state.json"
        st = json.loads(sp.read_text()) if sp.exists() else {}
        res_p = rd / "docs" / did / "result.json"
        res = json.loads(res_p.read_text()) if res_p.exists() else {}
        doc = (st.get("ingest") or {}).get("doc") or {}
        meta = doc.get("metadata") or {}
        blocks = doc.get("blocks") or []
        secs = doc.get("sections") or []
        scope = {b["block_id"]: b.get("scope") for b in blocks}
        vision_blocks = {b["block_id"] for b in blocks if (b.get("attributes") or {}).get("vision")}
        errs = st.get("errors") or []
        budget_skip = next((e for e in errs if "document budget" in e and e.startswith("A5B")), "")
        core = d.get("core") or {}
        n, m, mis, un = core.get("n", 0), core.get("matched", 0), core.get("misattributed", 0), core.get("unresolved", 0)
        # dynamic-spec linkage among gold facts found by the system
        dyn_matched = sum(1 for r in d.get("rows", []) if r["outcome"] == "matched" and r.get("param") in DYN)
        dyn_mis = sum(1 for r in d.get("rows", []) if r["outcome"] == "misattributed" and ((r.get("pred") or {}).get("ontology_status") == "dynamic"))
        # accepted specifications in the result: canonical / candidate / dynamic, evidence scope and vision
        acc = Counter()
        comment_vals = 0
        vision_vals = 0
        for p in res.get("products", []):
            for s in p.get("specifications", []):
                if s.get("verification") in ("verified", "verified_low_confidence"):
                    acc[(s.get("parameter") or {}).get("status") or "?"] += 1
                    evb = [e.get("block_id") for e in s.get("evidence") or []]
                    if evb and all(scope.get(b) in ("related_content", "footer", "navigation", "form") for b in evb):
                        comment_vals += 1
                    if any(b in vision_blocks for b in evb):
                        vision_vals += 1
        owners_src = Counter((f.get("attribution_note") or "").split(":")[0] for f in st.get("facts") or [] if f.get("attribution_note"))
        row = {
            "doc_id": did, "format": doc.get("format"), "pages": meta.get("pages"), "blocks": len(blocks),
            "chars": sum(len(b.get("text") or "") for b in blocks), "cost_usd": round(d.get("cost_usd") or 0, 3),
            "gold_n": n, "matched": m, "misattributed": mis, "unresolved": un, "missed": core.get("missed", 0),
            "recall": round(m / n, 3) if n else None, "wrong_owner_rate": round(mis / (m + mis), 3) if (m + mis) else None,
            "by_pos": d.get("by_pos"), "dyn_matched": dyn_matched, "dyn_misattributed": dyn_mis,
            "accepted_specs": dict(acc), "comment_values": comment_vals, "vision_values": vision_vals,
            "vision_pages": meta.get("vision_pages"), "budget_skip": budget_skip[:160],
            "sections": len(secs), "sections_with_owner": len(st.get("segments") or {}),
            "segment_sources": dict(Counter((st.get("segment_source") or {}).values())),
            "row_owner_rows": len(st.get("row_owners") or {}), "col_owner_tables": len(st.get("col_owners") or {}),
            "owner_notes": dict(owners_src), "other_product_facts": d.get("other_product_facts"),
            "errors": [e[:140] for e in errs if not e.startswith("A5B: document budget")][:4],
        }
        rows_out.append(row)
        for k in ("gold_n", "matched", "misattributed", "unresolved", "missed", "dyn_matched", "dyn_misattributed",
                  "comment_values", "vision_values"):
            tot[k] += row[k] or 0
        tot["cost_usd"] += row["cost_usd"]
        for pos, c in (d.get("by_pos") or {}).items():
            pos_tot.setdefault(pos, Counter()).update(c)
    print(f"{'doc':24} {'fmt':4} {'pages':>5} {'chars':>8} {'cost':>6} {'recall':>12} {'mis':>4} {'unres':>5} {'wrong%':>6}  by position")
    for r in rows_out:
        bp = " | ".join(f"{k} {v['matched']}/{v['n']}" for k, v in sorted((r["by_pos"] or {}).items()))
        print(f"{r['doc_id']:24} {r['format'] or '':4} {r['pages'] or '':>5} {r['chars']:>8} {r['cost_usd']:>6.2f} "
              f"{r['matched']:>4}/{r['gold_n']:<4} {r['recall'] if r['recall'] is not None else '-':>5} {r['misattributed']:>4} "
              f"{r['unresolved']:>5} {(r['wrong_owner_rate'] or 0) * 100:>5.1f}%  {bp}")
    print()
    m, mis = tot["matched"], tot["misattributed"]
    print(f"TOTAL gold {tot['gold_n']}: matched {m} ({m / max(1, tot['gold_n']):.1%}), misattributed {mis}, unresolved "
          f"{tot['unresolved']}, missed {tot['missed']}; wrong-owner rate {mis / max(1, m + mis):.1%}; cost ${tot['cost_usd']:.2f}")
    print("by position: " + " | ".join(f"{k}: {v['matched']}/{v['n']} ({v['matched'] / max(1, v['n']):.0%}), mis {v['misattributed']}"
                                       for k, v in sorted(pos_tot.items())))
    print(f"dynamic specs among matched gold facts: {tot['dyn_matched']}; dynamic among misattributed: {tot['dyn_misattributed']}")
    print(f"accepted values with evidence only in comments/nav/footer: {tot['comment_values']}; values read from page images: {tot['vision_values']}")
    for r in rows_out:
        extra = []
        if r["budget_skip"]:
            extra.append("budget: " + r["budget_skip"])
        if r["vision_pages"]:
            vp = r["vision_pages"]
            extra.append(f"vision: {len(vp.get('checked') or [])} pages checked, {sum((vp.get('blocks_added') or {}).values())} blocks added")
        extra.append(f"product map: {r['sections_with_owner']}/{r['sections']} sections {r['segment_sources']}; "
                     f"product-keyed rows {r['row_owner_rows']}, column-keyed tables {r['col_owner_tables']}")
        extra.append(f"accepted specs {r['accepted_specs']}")
        if r["errors"]:
            extra.append("errors: " + " || ".join(r["errors"]))
        print(f"- {r['doc_id']}: " + "; ".join(extra))
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps({"docs": rows_out, "total": tot, "by_pos": pos_tot}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
