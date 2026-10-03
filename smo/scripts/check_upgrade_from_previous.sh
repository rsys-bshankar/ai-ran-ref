#!/usr/bin/env bash
# PR-OPS-1.6: a database at the previous commit's schema upgrades to this commit's head.
#
#   SMO_DATABASE_URL=postgresql+psycopg://... scripts/check_upgrade_from_previous.sh [previous-ref]
#
# Check out the previous commit (default: this commit's first parent: on a pull request's merge commit the base branch tip, on a push to main
# the previous main commit) into a scratch worktree, migrate an EMPTY database to ITS head with ITS scripts/migrate.py, then run THIS commit's
# scripts/migrate.py and its models check against the same database. It catches a revision that only works on a fresh database (a column the
# previous release already has, a constraint the old rows break). The database must be empty and is left at the new head.
set -euo pipefail

: "${SMO_DATABASE_URL:?set SMO_DATABASE_URL to an empty Postgres database}"
here="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(git -C "$here" rev-parse --show-toplevel)"
previous="${1:-$(git -C "$repo" rev-parse HEAD^1 2>/dev/null || true)}"
if [ -z "$previous" ]; then echo "no previous commit to upgrade from (shallow or first commit): skipped"; exit 0; fi

scratch="$(mktemp -d)"
trap 'git -C "$repo" worktree remove --force "$scratch/prev" >/dev/null 2>&1 || true; rm -rf "$scratch"' EXIT
git -C "$repo" worktree add --detach "$scratch/prev" "$previous" >/dev/null
rel="${here#"$repo"/}"                                        # smo/ inside the repository
prev_smo="$scratch/prev/$rel"
if [ ! -f "$prev_smo/scripts/migrate.py" ]; then
  echo "the previous commit ($previous) has no scripts/migrate.py (before OPS-1): skipped"; exit 0
fi

echo "== previous commit ${previous:0:12}: migrate the empty database to its head"
(cd "$prev_smo" && python scripts/migrate.py)
echo "== this commit: upgrade the same database to head"
(cd "$here" && python scripts/migrate.py)
echo "== this commit: the models match the upgraded schema"
(cd "$here" && python scripts/check_migration_matches_models.py)
echo "OK: the previous schema upgrades to this commit's head"
