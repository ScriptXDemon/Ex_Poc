#!/usr/bin/env bash
# Publish this framework (code, tests, scripts, docs, answer keys) into a git checkout of the team repository, as the
# sub-folder defence-extractor/, on a branch. Never copies .env, data, runs or documents; refuses if a key-like string
# is found in what would be committed.
#   usage: bash scripts/publish_github.sh /path/to/repo-checkout [branch] ["commit message"]
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "${1:?path to a git checkout of the repository}" && pwd)"
BRANCH="${2:-agentic-framework}"
MSG="${3:-Update defence-extractor agentic framework}"
DEST="$REPO/defence-extractor"
cd "$REPO"
git fetch -q origin
if git rev-parse -q --verify "origin/$BRANCH" >/dev/null; then git checkout -q -B "$BRANCH" "origin/$BRANCH"; else git checkout -q -B "$BRANCH" origin/main; fi
mkdir -p "$DEST"
for d in src tests scripts docs eval; do
  rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' "$HERE/$d/" "$DEST/$d/"
done
for f in README.md pyproject.toml .env.example .gitignore; do cp "$HERE/$f" "$DEST/$f"; done
if grep -rIl -E "sk-(or|mssu)-[A-Za-z0-9]{8}|ghp_[A-Za-z0-9]{20}|github[_]pat_|BEGIN [A-Z ]*PRIVATE[ ]KEY" "$DEST"; then
  echo "refusing to publish: key-like strings found in the files above" >&2; exit 1
fi
git add -A defence-extractor
git status --short | head -20
echo "files staged: $(git diff --cached --name-only | wc -l)"
git diff --cached --quiet && { echo "nothing to commit"; exit 0; }
git commit -q -m "$MSG"
git push -u origin "$BRANCH"
