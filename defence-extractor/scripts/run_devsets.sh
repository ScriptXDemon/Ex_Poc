#!/usr/bin/env bash
# Run the quick dev sets one after the other on the configured endpoint and score each against its baseline.
#   bash scripts/run_devsets.sh <tag> [sets]   e.g. tag=a -> runs weak_a then guard_a; sets="guard weak" changes the order
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
tag="${1:?tag}"
G=eval/gold_large,eval/gold_corpus,eval/gold
for set in ${2:-weak guard}; do
  run="${set}_${tag}"
  echo "[$(date +%H:%M:%S)] $run start"
  dx run --devset "eval/devset_${set}.yaml" --run-id "$run" --concurrency "${DOC_CONCURRENCY:-2}" > "runs/${run}.log" 2>&1 || echo "$run: run exited $?"
  dx eval "$run" --gold "$G" --devset "eval/devset_${set}.yaml" >> "runs/${run}.log" 2>&1 || true
  echo "[$(date +%H:%M:%S)] $run done"
done
