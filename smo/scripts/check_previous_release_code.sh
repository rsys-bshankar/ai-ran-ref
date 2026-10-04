#!/usr/bin/env bash
# PR-OPS-5.2: the previous release's code runs on this commit's schema.
#
#   scripts/check_previous_release_code.sh [previous-tag]
#
# The rule (CLAUDE.md, "Schema changes"): a schema revision is additive, so the release before it keeps working on the upgraded database while
# a rolling upgrade is half done. This proves it: build the previous release (newest `smo-v*` tag, or the one given) from its own source,
# start its compose stack on a database migrated to THIS commit's head by THIS commit's scripts/migrate.py (the previous release's own
# `migrate` service is replaced by a no-op: it does not know the newer revisions), and replay the previous release's own demo runbook
# against it. Then, as a control, break the schema on purpose and check the same replay fails: a check that cannot fail proves nothing.
#
# Needs docker and, on the host, the runtime dependencies (for migrate.py). Leaves nothing behind (the stack and the worktree are removed).
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(git -C "$here" rev-parse --show-toplevel)"
tag="${1:-$(git -C "$repo" tag --list 'smo-v*' --sort=-version:refname | grep -v -- '-' | head -1 || true)}"
if [ -z "$tag" ]; then echo "no release tag to test against (git fetch --tags?): skipped"; exit 0; fi
rel="${here#"$repo"/}"
scratch="$(mktemp -d)"
prev_smo="$scratch/prev/$rel"
project="prevrel"
compose() { (cd "$prev_smo" && GUI_COOKIE_SECURE=false docker compose -p "$project" -f docker-compose.yml -f docker-compose.skipmigrate.yml "$@"); }
cleanup() {
  [ -d "$prev_smo" ] && compose down -v --remove-orphans >/dev/null 2>&1 || true
  git -C "$repo" worktree remove --force "$scratch/prev" >/dev/null 2>&1 || true
  rm -rf "$scratch"
}
trap cleanup EXIT

git -C "$repo" worktree add --detach "$scratch/prev" "refs/tags/$tag" >/dev/null
echo "== previous release: $tag ($(git -C "$repo" rev-parse --short "refs/tags/$tag")); this commit: $(git -C "$repo" rev-parse --short HEAD)"
cd "$prev_smo"
scripts/init_secrets.sh >/dev/null
cp .env.example .env
cat > docker-compose.skipmigrate.yml <<'Y'
# the schema comes from the newer commit; the old release's migrate would not know its revisions
services:
  migrate:
    command: ["true"]
Y

fresh_stack() {
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  compose up -d --wait postgres
  url="postgresql+psycopg://smo:$(cat secrets/db_password)@localhost:5432/smo"
  (cd "$here" && SMO_DATABASE_URL="$url" python scripts/migrate.py)
  (cd "$here" && SMO_DATABASE_URL="$url" python scripts/check_migration_matches_models.py)
}
replay() {
  docker run --rm --network "${project}_default" --network-alias demo-consumer \
    -e SMO_E2E_LIVE=1 -e PYTHONPATH=/work/smo/shared:/work/smo/sdk \
    -v "$scratch/prev:/work" -w /work/smo python:3.11-slim@sha256:9f6ef439f51f4b36dc5c6bb265c2d3dd810bde94c5bbec77ae86b6650c488cf9 sh -c "
      pip install -q --require-hashes -r requirements/dev.txt &&
      pip install -q --no-deps -e shared &&
      python -m pytest $1 -v -p no:cacheprovider"
}

# The control comes first, on a stack that is thrown away: a replay changes the stack's data (it onboards packages, deploys instances), so a
# second replay on the same stack is not a fair test. Break one column the previous release needs, replay the first runbook test, expect it to fail.
echo "== control: a schema that breaks the previous release must make the replay fail"
fresh_stack
compose exec -T postgres psql -U smo -d smo -v ON_ERROR_STOP=1 -c "ALTER TABLE write_config_job RENAME COLUMN msac_role TO msac_role_broken" >/dev/null
compose up -d --build --wait
if replay "tests_integration/test_demo_runbook.py::test_full_runbook_sequence_succeeds" >/dev/null 2>&1; then
  echo "FAIL: the replay passed on a schema with a column the previous release needs renamed: this check cannot fail" >&2
  exit 1
fi
echo "ok: the replay fails on a breaking schema"

echo "== the real check: this commit's head schema (this commit's scripts/migrate.py), the previous release's code built from its own source"
fresh_stack
compose up -d --build --wait
compose exec -T r1-termination python3 - < scripts/compose_e2e.py
replay tests_integration/test_demo_runbook.py
echo "OK: $tag runs on this commit's schema, and the check fails when it should"
