"""Per-document lines of a run's eval.json (after `dx eval`).  usage: python scripts/eval_docs.py <run_id>"""
import json
import sys

e = json.load(open(f"runs/{sys.argv[1]}/eval.json"))
for d in e["docs"]:
    if d.get("missing_result"):
        print(f"{d['doc_id']:22} (no result yet)")
        continue
    c, t = d["core"], d["text"]
    print(f"{d['doc_id']:22} core {c['matched']}/{c['n']} mis {c['misattributed']} unres {c['unresolved']} missed {c['missed']}"
          f" | text {t['matched']}/{t['n']} | params {d['param']} | extras {d['extras']} other {d['other_product_facts']}"
          f" neg {len(d['negative_violations'])} | ${d['cost_usd']:.3f}")
