"""Progress of a run: documents finished / failed / in flight, LLM spend, stage errors.  usage: python scripts/progress.py <run_id>"""

from __future__ import annotations

import collections
import json
import sys
import time
from pathlib import Path

from defence_extractor.config import get_settings


def main() -> None:
    run_id = sys.argv[1]
    s = get_settings()
    run = s.runs_dir / run_id
    sel = json.loads((run / "selection.json").read_text()) if (run / "selection.json").exists() else []
    status = collections.Counter()
    by_level = collections.Counter()
    done_level = collections.Counter()
    for r in sel:
        by_level[(r.get("level"), r["format"])] += 1
    errors = collections.Counter()
    for d in (run / "docs").glob("*"):
        st = d / "state.json"
        if not st.exists():
            continue
        try:
            js = json.loads(st.read_text())
        except Exception:
            continue
        stt = js.get("status", "?")
        if (d / "result.json").exists():
            stt = "completed"
            done_level[(js["ref"].get("level"), js["ref"]["format"])] += 1
        status[stt] += 1
        for e in js.get("errors", []):
            errors[e.split(":")[0]] += 1
    cost, calls, last = 0.0, 0, 0.0
    log = run / "llm_calls.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                x = json.loads(line)
            except Exception:
                continue
            calls += 1
            cost += x.get("cost_usd") or 0
            last = max(last, x.get("ts", 0))
    print(f"{run_id}: selected {len(sel)} | {dict(status)} | LLM calls {calls} cost ${cost:.3f} | last call {time.time() - last:.0f}s ago")
    print("done by (level, format): " + ", ".join(f"L{k[0]}-{k[1]} {done_level[k]}/{v}" for k, v in sorted(by_level.items(), key=lambda kv: (kv[0][0] or 9, kv[0][1]))))
    if errors:
        print("stage errors:", dict(errors.most_common(8)))


if __name__ == "__main__":
    main()
