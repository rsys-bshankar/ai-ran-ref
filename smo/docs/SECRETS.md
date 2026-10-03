# Secrets inventory

Every secret the platform holds, where it lives, who owns it and how it is rotated (`PR-SEC-4.1`). Where a
secret can be supplied as a file instead of an environment variable (`*_FILE`, `smo_shared/secretfile.py`),
the file form is what compose uses and the environment form is for development.

"Stored as" is what is kept: **hash** means the original cannot be recovered, **plaintext** means a database
dump, a backup file or anyone with read access to that table holds the secret itself.

| Secret | Owner | Supplied by | Stored as | Rotation today | Planned |
|---|---|---|---|---|---|
| Postgres password (`smo` role) | Operator | Compose secret file `secrets/db_password` (created by `scripts/init_secrets.sh`); `POSTGRES_PASSWORD_FILE` for Postgres, `SMO_DATABASE_PASSWORD_FILE` for every module (`SMO_DATABASE_URL` carries no password) | A file on the Docker host; inside Postgres its own hash | `ALTER ROLE smo PASSWORD '...'` in the database, write the same value to `secrets/db_password`, `docker compose up -d` to recreate the services; not tried end to end (`SEC-4.8`) | Per-module roles (`DB-2`); a secret manager (`SEC-4.7`) |
| GUI session signing key (`GUI_JWT_SECRET`) | Operator | Environment variable; if unset, the first BFF instance generates one and stores it | `gui_setting` row (plaintext) when generated; environment otherwise | Change the variable and restart every BFF instance: all sessions end. A generated key: delete the `gui_setting` row, restart | `*_FILE` (`SEC-4.4`); `SEC-5` signing keys |
| GUI user passwords (`GUI_ADMIN_PASSWORD`, `GUI_OPERATOR_PASSWORD`, `GUI_VIEWER_PASSWORD`) | Operator | Environment variables read once when an empty database is seeded; an unset admin password is generated into `GUI_INITIAL_PASSWORD_FILE` (0600, never logged) | Hash in `gui_user` | A user changes their own password in the GUI; the variables do nothing on a seeded database | `*_FILE` (`SEC-4.4`) |
| The BFF's SME invoker credential | GUI BFF | Self-registered at SME on first start | **Plaintext** in `gui_smo_credential` (the BFF must present it to SME's token endpoint) | Delete the row: the next start registers a new invoker | Secret store (`SEC-4.5`) |
| Each module's SME invoker credential | The module | Self-registered at SME on first use; or `SMO_INVOKER_ID` / `SMO_INVOKER_SECRET` from the environment, which win | **Plaintext** in `module_identity.invoker_secret` | Delete the module's row (and restart it): the next call registers a new invoker; SME's stale-invoker purge removes the old one | `*_FILE` for `SMO_INVOKER_SECRET` (`SEC-4.5`) |
| SME onboarding secrets and access tokens | SME | Issued to API invokers | **Hash** (`invoker_registration.onboarding_secret_hash`, `issued_access_token.access_token_hash`) | An invoker is re-onboarded; tokens expire (`ACCESS_TOKEN_TTL_SECONDS`) | Signed tokens (`SEC-5`) |
| MSAC identity credentials (RAN NF OAM) | RAN NF OAM | Supplied by the API caller creating an Identity | **Hash** (`msac_identity.credential_hash`) | Replace the Identity | none |
| AI/ML datalake token (`feature_group.token`) | AIMgF | Supplied by the API caller registering a feature group (an InfluxDB token) | **Plaintext** in `feature_group.token`, returned by reads of that resource | Update the feature group | A secret reference instead of a value (`SEC-4.6` pattern); found while writing this inventory |
| O1 adaptor credentials (NETCONF over SSH) | RAN NF OAM | Per endpoint: `o1_adaptor_endpoint.credential_ref` names a credential; the service resolves it from its own environment or mounted secrets: `NETCONF_CRED_<NAME>_PASSWORD` (or `_PASSWORD_FILE`) and `NETCONF_CRED_<NAME>_KEY_FILE` (`<NAME>` upper-cased, `-` as `_`). No reference: the shared `NETCONF_SSH_PASSWORD` / `NETCONF_SSH_KEY_FILE` | **Not stored**: the database holds the name only; registration refuses anything that is not a name of a configured credential, without echoing it | Replace the secret file and restart the service (the value is read at each connect, so a mounted file needs no restart) | Per-element rotation runbook (SEC-4.8) |
| NFO `config_secrets` | NFO | Supplied by the API caller | A reference to a secrets store, by design, never a plaintext value | n/a | none |
| Webhook / callback destinations | Callers | Supplied per subscription | A URL, not a secret; guarded against SSRF (`smo_shared/webhook.py`) | n/a | `MSG-5` signing |

## Where a secret must never appear

Source code and compose files (`tests_integration/test_database_credentials.py` fails on the old default URL or
a `POSTGRES_PASSWORD:` value in compose), container images, logs (the BFF writes a generated password to a 0600
file, never to a log; the database URL error messages name the variable and the file, never the value), and
readiness or error bodies (`smo_shared/health.py` reports an exception class, not its text).

## Rotating the database password (the only secret with a compose path today)

1. Pick the new value: `python3 -c "import secrets; print(secrets.token_hex(24), end='')"`.
2. In the database: `ALTER ROLE smo PASSWORD '<new>'` (as `smo` or a superuser; for example
   `docker compose exec postgres psql -U smo -d smo`). New connections must now use it; open ones keep working.
3. Write the same value to `secrets/db_password` (keep it `0644` in the `0700` directory).
4. `docker compose up -d --force-recreate` so every service reads the new file.

Steps 2 and 3 can leave a short window where a restarting module fails to connect; a rotation that avoids it
needs two roles or a pooler (`DB-5`), which is why the runbook (`SEC-4.8`) is still open.
