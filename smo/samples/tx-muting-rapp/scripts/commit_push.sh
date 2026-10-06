#!/usr/bin/env bash
# Commit this sample (code, scripts, README) and push the current branch.
#
#   scripts/commit_push.sh "message"             # commit, then push to origin
#   scripts/commit_push.sh "message" --no-push   # commit only
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

message="${1:-}"
[ -n "$message" ] || { echo "usage: $0 \"commit message\" [--no-push]" >&2; exit 1; }

cd "$REPO_DIR"
branch="$(git rev-parse --abbrev-ref HEAD)"
case "$branch" in
  main|master) echo "refusing to commit on $branch: switch to the sample branch first" >&2; exit 1 ;;
esac

git add -A -- "smo/samples/$SAMPLE_NAME"
if git diff --cached --quiet; then
  echo "nothing to commit"
else
  git commit -m "$message"
fi

if [ "${2:-}" != "--no-push" ]; then
  git push -u origin "$branch"
fi
