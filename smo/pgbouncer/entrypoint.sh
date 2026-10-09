#!/bin/sh
# Builds PgBouncer's user list and configuration from the compose secrets, then runs it (PR-DB-5.1).
#
# Every file /run/secrets/db_password[_<module>] is a database role: `db_password` is the owner `smo`, `db_password_<module>` is `smo_<module>` with
# "-" written "_" (scripts/db_roles.py). Clients and PgBouncer's own connections to Postgres authenticate with the same password (SCRAM against
# the plaintext in the list), so a rotation (docs/SECRETS.md) needs `docker compose restart pgbouncer` after the new files are in place.
#
#   PGBOUNCER_POOL_MODE         transaction   transaction | session
#   PGBOUNCER_POOL_SIZE         20            server connections per (database, user): size it against Postgres's max_connections (README, "Pool sizing")
#   PGBOUNCER_MAX_CLIENT_CONN   1000          client connections accepted
#   PGBOUNCER_MAX_PREPARED      100           prepared statements PgBouncer tracks per client (0: none); protocol-level ones need PgBouncer 1.21+
#   PGBOUNCER_SERVER_TLS_SSLMODE   prefer   how PgBouncer treats TLS to Postgres: disable | allow | prefer | require | verify-ca | verify-full (PR-SEC-2.4)
#   PGBOUNCER_SERVER_TLS_CA_FILE   (unset)  the CA file for verify-ca and verify-full (required for those two)
#   POSTGRES_STATEMENT_TIMEOUT_MS / POSTGRES_IDLE_IN_TRANSACTION_TIMEOUT_MS
#                               30000 / 300000   what the services ask for with `options` when they connect direct; PgBouncer refuses that
#                               startup parameter, so it sets the same limits itself on every server connection it opens
set -eu
umask 077
users=/tmp/userlist.txt
: > "$users"
for f in /run/secrets/db_password /run/secrets/db_password_*; do
  [ -s "$f" ] || continue
  name=${f#/run/secrets/db_password}
  if [ -z "$name" ]; then user=smo; else user="smo_$(printf '%s' "${name#_}" | tr '-' '_')"; fi
  printf '"%s" "%s"\n' "$user" "$(cat "$f")" >> "$users"
done
[ -s "$users" ] || { echo "no database password files in /run/secrets" >&2; exit 1; }

# PR-SEC-2.4: with PGBOUNCER_SERVER_TLS_SSLMODE=verify-full (docker-compose.pgtls.yml sets it) PgBouncer checks the certificate of Postgres against this CA file and the
# host name in [databases]; a wrong CA stops the server connection. The default stays `prefer`, as before.
server_ca_line=""
[ -n "${PGBOUNCER_SERVER_TLS_CA_FILE:-}" ] && server_ca_line="server_tls_ca_file = ${PGBOUNCER_SERVER_TLS_CA_FILE}"
case "${PGBOUNCER_SERVER_TLS_SSLMODE:-prefer}" in
  verify-ca|verify-full) [ -n "$server_ca_line" ] || { echo "PGBOUNCER_SERVER_TLS_SSLMODE=${PGBOUNCER_SERVER_TLS_SSLMODE} needs PGBOUNCER_SERVER_TLS_CA_FILE" >&2; exit 1; } ;;
esac

statement=${POSTGRES_STATEMENT_TIMEOUT_MS:-30000}
idle=${POSTGRES_IDLE_IN_TRANSACTION_TIMEOUT_MS:-300000}
cat > /tmp/pgbouncer.ini <<EOF
[databases]
smo = host=${PGBOUNCER_UPSTREAM_HOST:-postgres} port=${PGBOUNCER_UPSTREAM_PORT:-5432} dbname=smo connect_query='SET statement_timeout=${statement}; SET idle_in_transaction_session_timeout=${idle}'

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = scram-sha-256
auth_file = $users
pool_mode = ${PGBOUNCER_POOL_MODE:-transaction}
default_pool_size = ${PGBOUNCER_POOL_SIZE:-20}
max_client_conn = ${PGBOUNCER_MAX_CLIENT_CONN:-1000}
max_prepared_statements = ${PGBOUNCER_MAX_PREPARED:-100}
server_tls_sslmode = ${PGBOUNCER_SERVER_TLS_SSLMODE:-prefer}
${server_ca_line}
ignore_startup_parameters = extra_float_digits
admin_users =
unix_socket_dir =
logfile =
pidfile =
EOF
exec pgbouncer /tmp/pgbouncer.ini
