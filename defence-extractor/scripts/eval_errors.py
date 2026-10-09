"""Print the error rows of an evaluation (missed / misattributed / unresolved, wrong parameters, negative violations).

usage: python scripts/eval_errors.py <run_id> [--extras DOC_ID]
"""

from __future__ import annotations

import argparse
import json

from defence_extractor.config import get_settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--extras", default=None)
    ap.add_argument("--text", action="store_true", help="also list text-fact misses")
    a = ap.parse_args()
    sc = json.loads((get_settings().runs_dir / a.run_id / "eval.json").read_text())
    for d in sc["docs"]:
        if d.get("missing_result"):
            print(f"== {d['doc_id']} MISSING RESULT")
            continue
        bad = [r for r in d["rows"] if r["outcome"] != "matched" and (a.text or r["gold"]["k"] != "text")]
        wrong = [r for r in d["rows"] if r.get("param") in ("wrong_canonical", "dynamic_other")]
        if a.extras == d["doc_id"]:
            print(f"== {d['doc_id']} extras:")
            for x in d["extra_list"]:
                print(f"   {x['param']!s:28} [{x['label']}] {x['value'][:90]!r} {x['status']}")
        if not bad and not wrong and not d["negative_violations"]:
            continue
        print(f"\n== {d['doc_id']} core {d['core']['matched']}/{d['core']['n']} text {d['text']['matched']}/{d['text']['n']} aligned={d['aligned']}")
        for r in bad:
            p = r.get("pred") or {}
            print(f"   {r['outcome']:13} {r['gold']['k']:6} {r['gold'].get('param')!s:22} gold={r['gold']['v'][:60]!r} "
                  f"out={p.get('value', '')[:50]!r} owner={p.get('entity_id', '')}")
        for r in wrong:
            print(f"   PARAM {r['param']:15} gold={r['gold'].get('param')} out={r['pred']['param']} [{r['pred']['label']}] "
                  f"value={r['pred']['value'][:50]!r}")
        for v in d["negative_violations"]:
            print(f"   NEGATIVE {v['negative']!r} -> {v['pred']['value'][:80]!r} ({v['pred']['param']})")


if __name__ == "__main__":
    main()
