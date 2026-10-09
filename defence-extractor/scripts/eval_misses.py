"""Unmatched gold facts of a run's eval.json, with what the system produced instead.  usage: eval_misses.py <run> [doc,...]"""
import json
import sys

e = json.load(open(f"runs/{sys.argv[1]}/eval.json"))
only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
for d in e["docs"]:
    if d.get("missing_result") or (only and d["doc_id"] not in only):
        continue
    bad = [r for r in d["rows"] if r["outcome"] != "matched"]
    if not bad:
        continue
    print("##", d["doc_id"])
    for r in bad:
        g, p = r["gold"], r.get("pred") or {}
        got = f" -> {p.get('entity_id')}:{p.get('param')}={str(p.get('value'))[:50]!r} [{p.get('status')}]" if p else ""
        print(f"  {r['outcome']:13} {g['p']} {g.get('param') or g['k']}: {g['v'][:60]!r}{got}")
