"""Per-agent LLM call statistics for one or more runs.  usage: python scripts/call_stats.py runs/bench1 runs/bench2"""

from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path


def main() -> None:
    for run in sys.argv[1:]:
        rows = [json.loads(line) for line in (Path(run) / "llm_calls.jsonl").read_text().splitlines() if line.strip()]
        by: dict[str, list[dict]] = collections.defaultdict(list)
        for x in rows:
            if x.get("status") == "ok" and not x.get("cache_hit"):
                by[x["agent"]].append(x)
        total = sum(x.get("cost_usd") or 0 for x in rows)
        print(f"== {run}: {len(rows)} calls, ${total:.3f}")
        for a, xs in sorted(by.items(), key=lambda kv: -sum(x.get("cost_usd") or 0 for x in kv[1])):
            cost = sum(x.get("cost_usd") or 0 for x in xs)
            lat = statistics.median(x["latency_s"] for x in xs)
            out = statistics.median(x.get("completion_tokens") or 0 for x in xs)
            rea = statistics.median(x.get("reasoning_tokens") or 0 for x in xs)
            print(f"  {a:20} n={len(xs):4} ${cost:.3f}  latency_med={lat:5.1f}s  out_med={out:6.0f}  reasoning_med={rea:6.0f}")


if __name__ == "__main__":
    main()
