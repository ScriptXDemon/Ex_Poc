#!/usr/bin/env bash
# Publish this framework (code, tests, scripts, docs, answer keys) into a git checkout of the team repository, as the
# sub-folder defence-extractor/, on a branch. Never copies .env, data, runs or documents; refuses if a key-like string
# is found in what would be committed.
#   usage: [PUSH_REMOTE=fork] bash scripts/publish_github.sh /path/to/repo-checkout [branch] ["commit message"]
# PUSH_REMOTE: the remote to push the branch to (default origin; a fork when the team repository takes pull requests).
# A new branch starts from origin/main; an existing branch on PUSH_REMOTE is continued (its pull request updates).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "${1:?path to a git checkout of the repository}" && pwd)"
BRANCH="${2:-agentic-framework}"
MSG="${3:-Update defence-extractor agentic framework}"
PUSH_REMOTE="${PUSH_REMOTE:-origin}"
DEST="$REPO/defence-extractor"
cd "$REPO"
git fetch -q origin
[ "$PUSH_REMOTE" = origin ] || git fetch -q "$PUSH_REMOTE"
if git rev-parse -q --verify "$PUSH_REMOTE/$BRANCH" >/dev/null; then git checkout -q -B "$BRANCH" "$PUSH_REMOTE/$BRANCH"
else git checkout -q -B "$BRANCH" origin/main; fi
mkdir -p "$DEST"
for d in src tests scripts docs eval; do
  rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' "$HERE/$d/" "$DEST/$d/"
done
for f in README.md pyproject.toml .env.example .gitignore; do cp "$HERE/$f" "$DEST/$f"; done
if grep -rIl -E "sk-[a-z]+-[A-Za-z0-9]{8}|ghp_[A-Za-z0-9]{20}|github[_]pat_|BEGIN [A-Z ]*PRIVATE[ ]KEY" "$DEST"; then
  echo "refusing to publish: key-like strings found in the files above" >&2; exit 1
fi
if grep -rIl -E "i3soft[l]ab|mssu-[l]lm|farm-[l]lm" "$DEST"; then  # private endpoints stay out of the public repository
  echo "refusing to publish: private endpoint addresses found in the files above" >&2; exit 1
fi
git add -A defence-extractor
git status --short | head -20
echo "files staged: $(git diff --cached --name-only | wc -l)"
git diff --cached --quiet && { echo "nothing to commit"; exit 0; }
git commit -q -m "$MSG"
git push -u "$PUSH_REMOTE" "$BRANCH"
