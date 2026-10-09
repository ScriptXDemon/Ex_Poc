#!/usr/bin/env bash
# Sync code from this local folder (source of truth) to the Algorithmtest server.
# The server has no rsync (and no sudo), so code is streamed with tar over SSH into a staging folder and the code
# directories are swapped in with 'mv' (atomic per directory), so runs in progress never see a half-deleted tree.
# Server-only directories (data/, runs/, var/, models/, .venv/) are never touched.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="${DX_REMOTE:-algotest}"
REMOTE_DIR="${DX_REMOTE_DIR:-projects/defence-extractor}"
CODE_DIRS="src tests scripts docs eval"
cd "$HERE"
export COPYFILE_DISABLE=1
tar -czf - \
  --exclude './.git' --exclude './.venv' --exclude '*/__pycache__' --exclude '*.pyc' \
  --exclude './.pytest_cache' --exclude './.ruff_cache' --exclude '*.DS_Store' \
  --exclude './spec_pages_1000' --exclude './data' --exclude './runs' --exclude './var' \
  --exclude './models' --exclude './extraction_outputs' --exclude '*.docx' --exclude '*.zip' --exclude './spec_bench_small.html' \
  . | ssh -o BatchMode=yes "$REMOTE" "set -e; mkdir -p ~/$REMOTE_DIR && cd ~/$REMOTE_DIR && rm -rf .sync_stage && mkdir .sync_stage \
      && tar --warning=no-unknown-keyword -xzf - -C .sync_stage \
      && for d in $CODE_DIRS; do if [ -e .sync_stage/\$d ]; then rm -rf \$d.prev; [ -e \$d ] && mv \$d \$d.prev; mv .sync_stage/\$d \$d; rm -rf \$d.prev; fi; done \
      && cp -f .sync_stage/*.* . 2>/dev/null || true; cp -f .sync_stage/.env .sync_stage/.gitignore . 2>/dev/null || true; \
      rm -rf .sync_stage; chmod 600 .env 2>/dev/null || true"
echo "synced -> $REMOTE:~/$REMOTE_DIR"
