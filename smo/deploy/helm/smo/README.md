# AI-RAN SMO Helm chart

Installs the SMO on Kubernetes: R1 Termination and every module of `docker-compose.yml`, the four reference rApps, the operator GUI, and (by default) a Postgres of its own. Docker Compose stays the way to run it on one machine; the chart is the same stack on a cluster. `tests_integration/test_helm_chart.py` keeps the two in step (every compose service is a chart module, with the same image, the same secrets, the same environment).

## Install

```
helm install smo deploy/helm/smo -n smo --create-namespace
```

Use a namespace of its own: the Services are named as in compose (`sme`, `dme`, `postgres`, ...), which is what the modules and the GUI's nginx address each other by, so there is one release per namespace.

The images are `ghcr.io/rsys-bshankar/ai-ran-smo/smo-<module>:<appVersion>`, the ones the release workflow publishes (`image.registry`, `image.prefix`, `image.tag`). A private registry needs `image.pullSecrets`.

Reach it:

```
kubectl -n smo port-forward svc/gui 3000:8080            # the operator GUI
kubectl -n smo port-forward svc/r1-termination 8080:8000 # the gateway rApps call: GET /bootstrap
```

or set `ingress.enabled` with `ingress.gui.host` and `ingress.r1.host` (and `ingress.r1.publicBaseUrl`, what `/bootstrap` advertises to rApps outside the cluster).

## The database

| | |
|---|---|
| Bundled (default) | One Postgres pod (StatefulSet, volume `pgdata-postgres-0`). A lab or trial. |
| External | `--set postgres.enabled=false --set postgres.external.host=db.example.com` (and `port`, `database`, `user`, `sslmode`). Run a managed or HA Postgres, such as a CloudNativePG cluster (`ci/cnpg-cluster.yaml`, `ci/cnpg-values.yaml` are the CI example); `targetSessionAttrs: read-write` with a comma-separated host list finds the primary by itself; the password goes in the Secret below. |

Kubernetes keeps a StatefulSet's volume when the release is uninstalled. To reinstall from nothing, delete it too: `kubectl -n smo delete pvc pgdata-postgres-0` (the new install makes a new password, which the old data would not accept).

## Secrets

The chart makes the Secret `smo-secrets` with the database password and the enrollment secret (PR-SEC-14) and keeps their values across upgrades. To bring your own, create a Secret with the keys `db-password` and `enrollment-secret` and set `secrets.existingSecret`; the chart then never touches it. Each pod is mounted only what it needs, as files under `/run/secrets` (the `*_FILE` convention of `docs/SECRETS.md`): an rApp never gets the enrollment secret, which is what would make it an SMO module.

**rApp credentials.** An rApp instance's credentials are made when the instance is created. With `--set rappCredentials.delivery=kubernetes` rApp Management writes them to a Secret `rapp-<instanceId>-credentials` in the release's namespace, so the workload NFO deploys can take them with `envFrom: [{secretRef: {name: rapp-<instanceId>-credentials}}]` (`SMO_INVOKER_ID`, `SMO_INVOKER_SECRET`, `SMO_IDENTITY_KIND=rapp`); the Secret is replaced when the credentials are rotated and deleted when the instance is terminated, and no person sees the secret. This is the one pod with Kubernetes access: a service account whose Role can create, update and delete Secrets in the namespace and read none. Off by default (`none`).

The GUI backend's own settings (`GUI_JWT_SECRET`, `GUI_ADMIN_PASSWORD`, ...) go in `gui.env`; left empty they are generated on first start.

## Migrations and upgrades

The schema is brought to the release's by one Job (`scripts/migrate.py`, the same as compose's `migrate` service):

* **install**: a plain Job `migrate`. The bundled database is part of the same release, so a pre-install hook would run before it exists.
* **upgrade**: a `pre-upgrade` hook Job, so it has finished, once, before any Deployment is touched; if it fails the upgrade is aborted and every old pod keeps serving. `kubectl -n smo logs job/migrate-<revision>` says what it did.

Every module that uses the database has an init container, `wait-for-schema`, that blocks until the database is at (or past) the head revision of its own image. A new pod never serves on an older schema, and a rolling update (`maxUnavailable: 0`) leaves the old pods in service until the new ones are ready. Because schema changes follow the expand/contract rule (`smo/CLAUDE.md`), the old pods run on the new schema in between.

`modules.<name>.replicas`, `podDisruptionBudget.enabled` and `autoscaling.enabled` are in the chart, off by default: running a module with more than one replica is the work of the HA release (`OPEN_ITEMS.md`, `PR-HA`). The GUI backend and Onboarding hold a volume and stay at one replica (they use the `Recreate` strategy).

## What is not in the chart

`netconf-lab` (a throwaway lab server) and `edge-tls` (the compose TLS terminator: use `ingress` with a TLS secret instead).

## Values

`values.yaml` is commented. The modules are one map (`modules`) and one template; a module is described by `image`, `kind` (`service` with `/live` and `/ready`, `worker` with a heartbeat file, `static` with a TCP probe), `database`, `enrollment`, `env`, `persistence`, `resources`; `moduleDefaults` is what each starts from. Every pod runs as the unprivileged user, with no capability, no privilege escalation and a read-only root filesystem (PR-SEC-13).

## CI

`.github/workflows/smo-tests.yml`, job `helm`: lint, render with the options on, build the images from the checkout, install on kind, check the database is at the head revision, run the compose smoke scripts inside the cluster (`compose_e2e.py`, `compose_e2e_roles.py`), check no rApp can read the enrollment secret, upgrade (every module rolls, no pod fails), uninstall.

## Where the replicas land

With more than one replica of a module (`modules.<name>.replicas`, or `ci/ha-values.yaml` as an example), `placement.mode` decides how the pods are spread: `soft` (default) prefers different nodes and still starts on a cluster with fewer nodes than replicas, `hard` requires different nodes (a replica that cannot be placed stays Pending), `off` sets nothing. `placement.zoneKey: topology.kubernetes.io/zone` adds a preference across zones. The chart's CI checks that each mode renders as described; it runs on one node, so it does not show pods landing on different nodes.

## Database roles

A module with a `databaseRole` in `values.yaml` (Onboarding today) connects as `smo_<role>`, which can use its own schema and the shared tables it is given and nothing else (`docs/SECRETS.md`, per-module database roles). `databaseRoles.enabled` (default true) turns this on for every module that names a role; the passwords are in the Secret `smo-role-secrets` (made once, kept across upgrades, left in place by `helm uninstall`), and the migrate Job runs `scripts/db_roles.py` after the migration. Turning it off and on again on a running release is safe and is tested.

