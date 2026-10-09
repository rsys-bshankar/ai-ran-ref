# Sourced by db_backup.sh and db_restore.sh (PR-DB-6): turns SMO_DATABASE_URL (a SQLAlchemy URL such as
# postgresql+psycopg://user:password@host:5432/db) into the libpq environment variables, so the password
# never appears on a command line where `ps` would show it.
# A URL without a password takes it from the file named by SMO_DATABASE_PASSWORD_FILE (as smo_shared/db.py does), unless PGPASSWORD is already
# set; the compose `db-backup` service uses the file. A URL or a password file that cannot be read stops the caller (returns 1) instead of
# leaving the libpq variables unset, which would send psql to whatever database the defaults name.
smo_pg_env() {
  : "${SMO_DATABASE_URL:?SMO_DATABASE_URL is not set (the same URL the services use)}"
  local exports
  exports="$(python3 - <<'PY'
import os, shlex, sys
from urllib.parse import parse_qs, unquote, urlsplit

parts = urlsplit(os.environ["SMO_DATABASE_URL"])
if not parts.scheme.startswith("postgresql"):
    sys.exit("SMO_DATABASE_URL is not a postgresql URL")
env = {"PGHOST": parts.hostname or "", "PGPORT": str(parts.port or 5432), "PGUSER": unquote(parts.username or ""),
       "PGPASSWORD": unquote(parts.password or ""), "PGDATABASE": unquote(parts.path.lstrip("/"))}
if not env["PGPASSWORD"] and not os.environ.get("PGPASSWORD") and os.environ.get("SMO_DATABASE_PASSWORD_FILE"):
    try:
        with open(os.environ["SMO_DATABASE_PASSWORD_FILE"]) as handle:
            env["PGPASSWORD"] = handle.read().strip()
    except OSError as error:
        sys.exit(f"cannot read SMO_DATABASE_PASSWORD_FILE: {error}")
query = parse_qs(parts.query)
# A query parameter wins, as in libpq; PGSSLMODE / PGSSLROOTCERT already in the environment (docker-compose.pgtls.yml sets them) are left alone when the URL has none.
for parameter, variable in (("sslmode", "PGSSLMODE"), ("sslrootcert", "PGSSLROOTCERT")):
    if query.get(parameter):
        env[variable] = query[parameter][0]
for key, value in env.items():
    if value:
        print(f"export {key}={shlex.quote(value)}")
PY
)" || return 1
  eval "$exports"
}
