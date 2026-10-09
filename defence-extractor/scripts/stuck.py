"""Unfinished docs of a run: last stages, errors, idle time, and the most recent LLM calls.  usage: stuck.py <run>"""
import json, sys, time
from pathlib import Path
run = Path("runs") / sys.argv[1]
now = time.time()
for d in sorted(run.glob("docs/*")):
    sp = d / "state.json"
    if sp.exists() and not (d / "result.json").exists():
        js = json.loads(sp.read_text())
        print(d.name, js["done"][-2:], js["errors"][-2:], "idle=%ds" % (now - sp.stat().st_mtime))
rows = [json.loads(l) for l in open(run / "llm_calls.jsonl") if l.strip()]
for r in rows[-5:]:
    print(r["agent"], r.get("doc_id"), r.get("latency_s"), "ago=%ds" % (now - r["ts"]), r.get("status"))
