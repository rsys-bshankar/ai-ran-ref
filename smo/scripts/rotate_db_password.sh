#!/usr/bin/env bash
# Rotates the database password of the compose stack (PR-SEC-4.8; the steps are in docs/SECRETS.md).
#
#   scripts/rotate_db_password.sh
#
# 1. makes a new random value (never printed),
# 2. ALTER ROLE smo in the running database (new connections need it; open ones keep working),
# 3. writes it to secrets/db_password (0644 in the 0700 directory, as scripts/init_secrets.sh does),
# 4. recreates every service so each reads the new file, and waits until they are healthy.
#
# Between 2 and 4 a service that restarts cannot connect; the whole stack is recreated at once to keep that window short. A rotation
# without any window needs two roles or a pooler (PR-DB-5). If step 2 fails nothing has changed; if step 4 fails the new password is
# already in both places, so run `docker compose up -d --force-recreate --wait` again.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"
file="secrets/db_password"
[ -s "$file" ] || { echo "no $file: run scripts/init_secrets.sh first" >&2; exit 1; }

new="$(python3 -c 'import secrets; print(secrets.token_hex(24), end="")')"
# the value goes in on stdin, not on a command line
printf "ALTER ROLE smo PASSWORD '%s'" "$new" | docker compose exec -T postgres psql -U smo -d smo -v ON_ERROR_STOP=1 -q >/dev/null
umask 077
printf '%s' "$new" > "$file"
chmod 644 "$file"
docker compose up -d --force-recreate --wait
echo "rotated: $file (the old password no longer works)"
