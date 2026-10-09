#!/usr/bin/env bash
# Full corpus run through Temporal (durable; resumable: re-running with the same RUN_ID skips finished stages/docs,
# and documents already started in the run go first).
#   usage: [ORDER=budget|interleave] [FIRST=@ids.txt] [IDS=@ids.txt] bash scripts/run_corpus.sh <run_id> [concurrency]
#   IDS limits the run to those documents; DOC_BUDGET_USD / DOC_BUDGET_TOKENS set the per-document cap;
#   ROOT=/path/to/folder runs any folder of HTML / PDF files (default data/spec_pages_1000).
# Progress:  python scripts/progress.py <run_id>      Report:  dx report <run_id>      UI: ssh -L 8233:localhost:8233 algotest
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
RUN_ID="${1:?run id}"
CONC="${2:-14}"
ORDER="${ORDER:-budget}"
source .venv/bin/activate
# a re-submission replaces the running batch for this run id (its document workflows are terminated with it;
# their finished stages are on disk and resume)
temporal workflow terminate --workflow-id "batch--$RUN_ID" --reason resubmit >/dev/null 2>&1 || true
# restart the worker so it runs the current code
if [ -f var/worker.pid ] && kill -0 "$(cat var/worker.pid)" 2>/dev/null; then kill "$(cat var/worker.pid)"; sleep 3; fi
rm -f var/worker.pid
LLM_CONCURRENCY="${LLM_CONCURRENCY:-20}" bash scripts/temporal_up.sh --worker 40
[ -f "runs/$RUN_ID.log" ] && mv "runs/$RUN_ID.log" "runs/$RUN_ID.$(date +%s).log"
setsid nohup dx temporal-run --run-id "$RUN_ID" --order "$ORDER" ${FIRST:+--first "$FIRST"} ${IDS:+--ids "$IDS"} ${ROOT:+--root "$ROOT"} --concurrency "$CONC" \
  > "runs/$RUN_ID.log" 2>&1 < /dev/null &
echo "submitted $RUN_ID order=$ORDER (log: runs/$RUN_ID.log)"
