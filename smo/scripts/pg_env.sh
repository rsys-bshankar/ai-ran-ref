# Sourced by db_backup.sh and db_restore.sh (PR-DB-6): turns SMO_DATABASE_URL (a SQLAlchemy URL such as
# postgresql+psycopg://user:password@host:5432/db) into the libpq environment variables, so the password
# never appears on a command line where `ps` would show it.
smo_pg_env() {
  : "${SMO_DATABASE_URL:?SMO_DATABASE_URL is not set (the same URL the services use)}"
  eval "$(python3 - <<'PY'
import os, shlex, sys
from urllib.parse import parse_qs, unquote, urlsplit

parts = urlsplit(os.environ["SMO_DATABASE_URL"])
if not parts.scheme.startswith("postgresql"):
    sys.exit("echo 'SMO_DATABASE_URL is not a postgresql URL' >&2; exit 2")
env = {"PGHOST": parts.hostname or "", "PGPORT": str(parts.port or 5432), "PGUSER": unquote(parts.username or ""),
       "PGPASSWORD": unquote(parts.password or ""), "PGDATABASE": unquote(parts.path.lstrip("/"))}
sslmode = parse_qs(parts.query).get("sslmode")
if sslmode:
    env["PGSSLMODE"] = sslmode[0]
for key, value in env.items():
    if value:
        print(f"export {key}={shlex.quote(value)}")
PY
)"
}
