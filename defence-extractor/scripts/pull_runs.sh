#!/usr/bin/env bash
# Pull run outputs (final JSON, ledgers, reports) back from the server into the local runs/ folder.
# Usage: scripts/pull_runs.sh <run_id>      (omit run_id to pull everything under runs/)
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="${DX_REMOTE:-algotest}"
REMOTE_DIR="${DX_REMOTE_DIR:-projects/defence-extractor}"
RUN="${1:-.}"
mkdir -p "$HERE/runs"
ssh -o BatchMode=yes "$REMOTE" "cd ~/$REMOTE_DIR/runs && tar -czf - '$RUN'" | tar -C "$HERE/runs" -xzf -
echo "pulled runs/$RUN -> $HERE/runs"
