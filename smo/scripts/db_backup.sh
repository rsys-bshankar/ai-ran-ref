#!/usr/bin/env bash
# Backs up the SMO database to one pg_dump custom-format file (PR-DB-6.1).
#
#   scripts/db_backup.sh [--compose] [OUTPUT.dump]
#
#   (default)   dumps the database named by SMO_DATABASE_URL with the host's pg_dump (override: PG_DUMP).
#               pg_dump must be the same major version as the server or newer: Postgres 18 in compose.
#   --compose   runs pg_dump inside the compose `postgres` container (always the right version) and
#               writes the file on the host. Run it from smo/.
#   OUTPUT      defaults to smo/backups/smo-<UTC timestamp>.dump (smo/backups/ is git-ignored).
#
# The file is written to OUTPUT.partial, checked with `pg_restore --list`, and only then renamed, so a
# failed dump never leaves something that looks like a backup. It is mode 0600: it holds every table,
# including each module's SME invoker secret and the GUI audit data. Keep it as protected as the database.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode=host
out=""
for arg in "$@"; do
  case "$arg" in
    --compose) mode=compose ;;
    -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) out="$arg" ;;
  esac
done
[ -n "$out" ] || { mkdir -p "$here/../backups"; out="$here/../backups/smo-$(date -u +%Y%m%dT%H%M%SZ).dump"; }
partial="$out.partial"
trap 'rm -f "$partial"' EXIT
umask 077

if [ "$mode" = compose ]; then
  docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' > "$partial"
  docker compose exec -T postgres pg_restore --list < "$partial" > /dev/null
else
  # shellcheck source=pg_env.sh
  . "$here/pg_env.sh"
  smo_pg_env
  "${PG_DUMP:-pg_dump}" --format=custom --no-owner --no-privileges --file="$partial"
  "${PG_RESTORE:-pg_restore}" --list "$partial" > /dev/null
fi

[ -s "$partial" ] || { echo "backup is empty" >&2; exit 1; }
mv "$partial" "$out"
trap - EXIT
echo "backup written: $out ($(wc -c < "$out") bytes)"
