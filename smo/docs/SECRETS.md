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
| O1 adaptor credentials (NETCONF over SSH) | RAN NF OAM | Per endpoint: `o1_adaptor_endpoint.credential_ref` names a credential; the service resolves it from its own environment or mounted secrets: `NETCONF_CRED_<NAME>_PASSWORD` (or `_PASSWORD_FILE`) and `NETCONF_CRED_<NAME>_KEY_FILE` (`<NAME>` upper-cased, `-` as `_`). No reference: the shared `NETCONF_SSH_PASSWORD` / `NETCONF_SSH_KEY_FILE` | **Not stored**: the database holds the name only; registration refuses anything that is not a name of a configured credential, without echoing it | Replace the secret file and restart the service (the value is read at each connect, so a mounted file needs no restart) | none: the runbook is below |
| O1 adaptor client certificates (NETCONF over TLS) | RAN NF OAM | Per endpoint, by the same `credential_ref` as SSH: files named by `NETCONF_CRED_<NAME>_CERT_FILE`, `_KEY_FILE`, `_CA_FILE` (shared: `NETCONF_TLS_CERT_FILE`, `_KEY_FILE`, `_CA_FILE`) | **Not stored**: the database holds the name only; the private key is a mounted file the service reads at each connect | Replace the files (no restart); issue the new certificate from the CA the device trusts | Certificate expiry alerting |
| Pinned SSH host keys (RAN NF OAM) | Operator | `PUT /o1-adaptor-endpoints/{id}/host-keys` (public keys only) | A public key and its fingerprint in `o1_adaptor_host_key`: not a secret | Pin the new key (`replaced: true`) | none |
| NFO `config_secrets` | NFO | Supplied by the API caller | A reference to a secrets store, by design, never a plaintext value | n/a | none |
| Webhook / callback destinations | Callers | Supplied per subscription | A URL, not a secret; guarded against SSRF (`smo_shared/webhook.py`) | n/a | `MSG-5` signing |

## Where a secret must never appear

Source code and compose files (`tests_integration/test_database_credentials.py` fails on the old default URL or
a `POSTGRES_PASSWORD:` value in compose), container images, logs (the BFF writes a generated password to a 0600
file, never to a log; the database URL error messages name the variable and the file, never the value), and
readiness or error bodies (`smo_shared/health.py` reports an exception class, not its text).

## Rotation runbooks (PR-SEC-4.8)

One runbook per secret that can be rotated by an operator. Each was tried once, and the right-hand column says where it is tried again on every change.

| Secret | Runbook | Tried by |
|---|---|---|
| Postgres password | [below](#the-database-password) | CI job "Full docker-compose stack runs end to end", step "Rotate the database password" (the script, the old value refused, the stack back) |
| O1 adaptor credential (password or key, per endpoint or shared) | [below](#an-o1-adaptor-credential) | `ran-nf-oam/tests/test_netconf_ssh.py::test_a_rotated_password_file_is_used_by_the_next_connect_without_a_restart` |
| A module's SME invoker secret | [below](#a-modules-sme-invoker-secret) | `shared/tests/test_module_identity.py::test_deleting_the_stored_invoker_makes_the_next_start_register_a_new_one` |
| GUI session signing key | [below](#the-gui-session-signing-key) | `gui-bff/tests/test_main.py::test_rotating_the_signing_key_ends_every_session_and_a_new_login_works` |
| O1 adaptor client certificate (TLS) | [below](#an-o1-adaptor-client-certificate) | `ran-nf-oam/tests/test_netconf_tls.py::test_a_rotated_client_certificate_is_used_by_the_next_connect_without_a_restart` |

A rotation that touches another system (an element's own password, a certificate authority) is only as tried as the pieces here: the tests cover this
platform's side and the order of steps, not the element.

### The database password

    scripts/rotate_db_password.sh

It makes a new value, runs `ALTER ROLE smo PASSWORD` in the database (the value goes in on stdin), writes `secrets/db_password` (0644 in the 0700
directory) and recreates every service (`docker compose up -d --force-recreate --wait`). Between the `ALTER ROLE` and the recreate a restarting service cannot
connect; recreating all at once keeps that window to seconds. A rotation with no window needs two roles or a pooler (`DB-5`). If the `ALTER ROLE`
fails nothing has changed; if the recreate fails the new value is already in both places, so run the `up` command again. Check afterwards that the old
value is refused: connect to `postgres` from another container with it.

By hand, the same steps: pick a value (`python3 -c "import secrets; print(secrets.token_hex(24), end='')"`), `ALTER ROLE smo PASSWORD '<new>'`,
write the same value to `secrets/db_password`, `docker compose up -d --force-recreate`.

### An O1 adaptor credential

1. Set the new password (or install the new public key) on the element.
2. Replace the file the credential names: `NETCONF_CRED_<NAME>_PASSWORD_FILE` (or `_KEY_FILE`), or the shared `NETCONF_SSH_PASSWORD_FILE` / `NETCONF_SSH_KEY_FILE`.
   The file is read at each connect, so no restart is needed; mounted secrets (a Kubernetes Secret, a Docker secret) update the file in place.
3. Until step 2 is done, writes to that element are refused as `NETCONF_RPC_FAILED` (authentication), not retried forever: the job reports it.

A password given as a plain environment variable (`NETCONF_CRED_<NAME>_PASSWORD`) needs a restart of RAN NF OAM instead. Rotating a pinned host key is a
different operation: pin the new key (`PUT /o1-adaptor-endpoints/{id}/host-keys`, the response says `replaced: true`).

### A module's SME invoker secret

1. `DELETE FROM module_identity WHERE module = '<module>'` (or `DELETE` the row through the module's database).
2. Restart the module (every replica).
3. The module registers a new invoker at SME and stores it; its replicas adopt the stored one. SME's stale-invoker purge removes the old registration.

With `SMO_INVOKER_ID` / `SMO_INVOKER_SECRET` set in the environment, those win: change them and restart instead.

### The GUI session signing key

1. Set the new `GUI_JWT_SECRET` (or delete the generated `gui_setting` row named `jwt_secret` so the first instance generates a new one).
2. Restart every BFF instance. Every session ends; people sign in again. Nothing else holds the key.

### An O1 adaptor client certificate

1. Have the element's trust store accept the new certificate (or its CA) alongside the old one.
2. Replace the files `NETCONF_CRED_<NAME>_CERT_FILE`, `_KEY_FILE` (and `_CA_FILE` when the CA changes). They are read at each connect.
3. Remove the old certificate from the element once writes succeed.

### Not rotated by an operator

Hashes (SME onboarding secrets, access tokens, MSAC credentials) are replaced by replacing the thing they belong to (re-onboard the invoker, create the
Identity again). The AI/ML datalake token is rotated by updating the feature group; until `SEC-4.6` moves it to a reference it is stored in the clear.
