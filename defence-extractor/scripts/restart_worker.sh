#!/usr/bin/env bash
# Graceful worker restart for a running corpus batch (loads new code without paying for killed in-flight LLM calls):
# stop scheduling (terminate the batch), let running stage activities finish and save, then restart worker + batch.
#   usage: [ORDER=budget] [FIRST=@ids.txt] [LLM_CONCURRENCY=32] bash scripts/restart_worker.sh <run_id> [concurrency]
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
RUN_ID="${1:?run id}"
temporal workflow terminate --workflow-id "batch--$RUN_ID" --reason "graceful restart" >/dev/null 2>&1 || true
# terminated document workflows schedule nothing new; wait until the in-flight LLM calls have drained (max ~8 min)
for _ in $(seq 1 48); do
  idle=$(python3 -c "
import json, os, time
p = 'runs/$RUN_ID/llm_calls.jsonl'
last = 0.0
with open(p, 'rb') as f:
    f.seek(max(0, os.path.getsize(p) - 20000))
    for line in f.read().decode('utf-8', 'ignore').splitlines()[1:]:
        try: last = max(last, json.loads(line).get('ts', 0))
        except ValueError: pass
print(int(time.time() - last))")
  [ "$idle" -ge 60 ] && break
  sleep 10
done
echo "LLM idle ${idle}s; restarting worker"
FIRST="${FIRST:-}" ORDER="${ORDER:-budget}" bash scripts/run_corpus.sh "$RUN_ID" "${2:-24}"
