#!/usr/bin/env bash
# Creates the secret files the compose stack mounts (PR-SEC-4.3). Run it once before `docker compose up`.
#
#   scripts/init_secrets.sh
#
# smo/secrets/db_password   the Postgres password: a random 48-hex-character value, never printed. An existing
#                           file is left alone, so running this again does not lock the stack out of its own
#                           database. To rotate it, see docs/SECRETS.md.
#
# The directory is owner-only (0700) and git-ignored. The file itself is 0644: Compose bind-mounts it as it is,
# and the services run as uid 10001, not as you, so it has to be readable by other users; the directory is what
# keeps other accounts on this machine out. Postgres and every module read it at /run/secrets/db_password
# (POSTGRES_PASSWORD_FILE, SMO_DATABASE_PASSWORD_FILE): neither the compose file nor any container's
# environment carries the password.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dir="$here/secrets"
file="$dir/db_password"

umask 077
mkdir -p "$dir"
chmod 700 "$dir"
if [ -s "$file" ]; then
  echo "kept: $file (already exists)"
  exit 0
fi
python3 -c 'import secrets; print(secrets.token_hex(24), end="")' > "$file"
chmod 644 "$file"
echo "created: $file"
