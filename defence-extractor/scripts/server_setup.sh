#!/usr/bin/env bash
# One-time (idempotent) user-space setup of this checkout on a Linux server. No sudo required.
#   - uv (Python package manager) in ~/.local/bin
#   - project virtualenv (Python 3.12) with CPU-only torch, docling, FlagEmbedding (BGE-M3)
#   - Temporal CLI (dev server) in ~/.local/bin
set -euo pipefail
PROJ="${PROJ:-$(cd "$(dirname "$0")/.." && pwd)}"
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$PROJ"/{data,runs,var,models} "$HOME/.local/bin"
cd "$PROJ"

if ! command -v uv >/dev/null 2>&1; then
  echo "[setup] installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

if [ ! -d .venv ]; then
  echo "[setup] creating venv"
  uv venv --python 3.12 .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[setup] torch (CPU)"
uv pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.4" "torchvision>=0.19"
echo "[setup] project + ml + dev extras"
uv pip install -e ".[ml,dev]"

if ! command -v temporal >/dev/null 2>&1; then
  echo "[setup] installing Temporal CLI"
  curl -sSf https://temporal.download/cli.sh | sh -s -- --dir "$HOME/.temporalio"
  ln -sf "$HOME/.temporalio/bin/temporal" "$HOME/.local/bin/temporal"
fi
temporal --version
python -c "import torch, docling, FlagEmbedding; print('torch', torch.__version__, '| docling ok | FlagEmbedding ok')"
echo "[setup] done"
