#!/usr/bin/env bash
# Restores a db_backup.sh file over the SMO database (PR-DB-6.1). DESTRUCTIVE: every object in the dump
# replaces the one in the database (`pg_restore --clean --if-exists`), in one transaction, so a failed
# restore changes nothing.
#
#   scripts/db_restore.sh [--compose] --yes BACKUP.dump
#
#   (default)   restores into the database named by SMO_DATABASE_URL with the host's pg_restore (PG_RESTORE).
#   --compose   restores into the compose `postgres` container. Run it from smo/.
#   --yes       required. Without it the script prints what it would do and stops.
#
# Stop the services that write to the database first (`docker compose stop`, then
# `docker compose start postgres`); a service holding open transactions can block the restore, and one
# writing during it is lost. Start them again afterwards.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode=host
yes=no
dump=""
for arg in "$@"; do
  case "$arg" in
    --compose) mode=compose ;;
    --yes) yes=yes ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) dump="$arg" ;;
  esac
done
[ -n "$dump" ] || { echo "usage: db_restore.sh [--compose] --yes BACKUP.dump" >&2; exit 2; }
[ -s "$dump" ] || { echo "no such backup (or empty): $dump" >&2; exit 2; }
if [ "$yes" != yes ]; then
  echo "refusing: this replaces the contents of the database with $dump. Re-run with --yes." >&2
  exit 2
fi

flags=(--clean --if-exists --no-owner --no-privileges --exit-on-error --single-transaction)
if [ "$mode" = compose ]; then
  docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" '"${flags[*]}" < "$dump"
else
  # shellcheck source=pg_env.sh
  . "$here/pg_env.sh"
  smo_pg_env
  "${PG_RESTORE:-pg_restore}" "${flags[@]}" --dbname="$PGDATABASE" "$dump"
fi
echo "restored: $dump"
