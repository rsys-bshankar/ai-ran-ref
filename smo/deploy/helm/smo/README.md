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

**Disaster recovery** (targets RPO 15 minutes and RTO 1 hour, runbook and gaps in [`docs/DISASTER_RECOVERY.md`](../../../docs/DISASTER_RECOVERY.md)). With a CloudNativePG cluster behind `postgres.external`, give the Cluster `spec.backup.barmanObjectStore` (an S3-compatible bucket; `ci/cnpg-cluster-backup.yaml` is the example, with `archive_timeout: 300`) so the WAL is archived continuously, and ask the chart for the base backups: `--set postgres.cnpgBackup.enabled=true --set postgres.cnpgBackup.cluster=<cluster name>` renders a `ScheduledBackup` (`postgres.cnpgBackup.schedule`, six cron fields, daily 02:00 UTC by default). It needs `postgres.enabled=false`; the render fails with the bundled single-pod Postgres, which has no WAL archiving and no scheduled backup in this chart (run `scripts/dr_backup.sh` from outside). The chart does not back up the GUI backend's SQLite volume: use one shared `GUI_DATABASE_URL` or snapshot the PVC. The recovery from the object store has not run in CI.

Kubernetes keeps a StatefulSet's volume when the release is uninstalled. To reinstall from nothing, delete it too: `kubectl -n smo delete pvc pgdata-postgres-0` (the new install makes a new password, which the old data would not accept).

## Secrets

The chart makes the Secret `smo-secrets` with the database password and the enrollment secret (PR-SEC-14) and keeps their values across upgrades. To bring your own, create a Secret with the keys `db-password` and `enrollment-secret` and set `secrets.existingSecret`; the chart then never touches it. Each pod is mounted only what it needs, as files under `/run/secrets` (the `*_FILE` convention of `docs/SECRETS.md`): an rApp never gets the enrollment secret, which is what would make it an SMO module.

**rApp credentials.** An rApp instance's credentials are made when the instance is created. With `--set rappCredentials.delivery=kubernetes` rApp Management writes them to a Secret `rapp-<instanceId>-credentials` in the release's namespace, so the workload NFO deploys can take them with `envFrom: [{secretRef: {name: rapp-<instanceId>-credentials}}]` (`SMO_INVOKER_ID`, `SMO_INVOKER_SECRET`, `SMO_IDENTITY_KIND=rapp`); the Secret is replaced when the credentials are rotated and deleted when the instance is terminated, and no person sees the secret. This is the one pod with Kubernetes access: a service account whose Role can create, update and delete Secrets in the namespace and read none. Off by default (`none`).

The GUI backend's own settings (`GUI_JWT_SECRET`, `GUI_ADMIN_PASSWORD`, ...) go in `gui.env`; left empty they are generated on first start.

**OIDC login for the GUI** (PR-SEC-6, off by default). Set `gui.env` to `GUI_OIDC_ENABLED: "true"` with `GUI_OIDC_ISSUER`, `GUI_OIDC_CLIENT_ID`, `GUI_OIDC_REDIRECT_URI` (the public URL of `/api/oidc/callback`, which the `gui` ingress already forwards under `/api`) and `GUI_OIDC_GROUP_ROLE_MAP` (`smo-admins=admin,smo-ops=operator,smo-viewers=viewer`; a user in no mapped group is refused unless `GUI_OIDC_DEFAULT_ROLE` is set); the other `GUI_OIDC_*` are in `docs/CONFIGURATION.md`. The client's credential should not sit in values: make a Secret of your own and point `gui.oidcClientSecretRef` at it (`{name: my-oidc-secret, key: client-secret}`, key default `client-secret`), which the chart passes to the pod as `GUI_OIDC_CLIENT_SECRET`. The backend refuses to start on a half-configured provider, so `kubectl -n smo logs deploy/gui-bff` names the variable. The issuer and the redirect URI must be https (a lab provider on http needs `GUI_OIDC_ALLOW_HTTP: "true"`). The local admin stays as the break-glass account; `GUI_LOCAL_LOGIN_ENABLED: "false"` removes it. The sign-ins in flight are rows in the GUI backend's database, so this works with the one replica the chart runs and would with several on one database. The chart itself has not been rendered or run against a provider in CI (the Keycloak check uses compose).

## Migrations and upgrades

The schema is brought to the release's by one Job (`scripts/migrate.py`, the same as compose's `migrate` service):

* **install**: a plain Job `migrate`. The bundled database is part of the same release, so a pre-install hook would run before it exists.
* **upgrade**: a `pre-upgrade` hook Job, so it has finished, once, before any Deployment is touched; if it fails the upgrade is aborted and every old pod keeps serving. `kubectl -n smo logs job/migrate-<revision>` says what it did.

Every module that uses the database has an init container, `wait-for-schema`, that blocks until the database is at (or past) the head revision of its own image. A new pod never serves on an older schema, and a rolling update (`maxUnavailable: 0`) leaves the old pods in service until the new ones are ready. Because schema changes follow the expand/contract rule (`smo/CLAUDE.md`), the old pods run on the new schema in between.

`modules.<name>.replicas`, `podDisruptionBudget.enabled` and `autoscaling.enabled` are in the chart, off by default: running a module with more than one replica is the work of the HA release (`OPEN_ITEMS.md`, `PR-HA`). The GUI backend and Onboarding hold a volume and stay at one replica (they use the `Recreate` strategy).

### Rolling back

`helm rollback smo <revision> -n smo --wait` returns to the code of the release before; it does not undo a migration (the old pods run on the new schema, by the expand/contract rule), and `kubectl -n smo rollout status` shows the pods come back. Two things the upgrade lane (`.github/workflows/smo-upgrade-kind.yml`) found:

* **Roll back to a revision made by `helm upgrade`** (`helm history smo`), not to revision 1 made by `helm install`. The install's migrate Job is a plain Job, so a rollback to revision 1 runs it again with the older image, whose `migrate.py` fails on a schema it does not know ("Can't locate revision"). An upgrade's migrate Job is a `pre-upgrade` hook and is not run by a rollback. (From this release on `migrate.py` leaves a later revision alone, so the problem is only that of the older images.)
* **The rollback is promised to the release before only.** The release before that does not know the newer schema in its own migrate step.

`smo-role-secrets` keeps the password of a role the release no longer has (A1 Related's, for the release that removed it), because the older chart still mounts it.

## Configuration reference

Every variable a module reads is in `docs/CONFIGURATION.md` (default, secret or not, what it does). The chart sets the database, secret and enrollment variables itself; any other goes under `modules.<name>.env` (merged over `moduleDefaults.env`), the GUI backend's under `gui.env`. `tests_integration/test_helm_chart.py` still fails when a service's environment in the chart and in `docker-compose.yml` disagree. A secret has a `*_FILE` form: the chart uses it for the database password and the enrollment secret (a Secret volume), and so should any value you add.

## Build identity

Every module answers `GET /version` (`{module, version, buildSha, builtAt}`, PR-OBS-8.1) from `SMO_VERSION`, `SMO_BUILD_SHA` and `SMO_BUILT_AT`, which are baked into the image as Docker build arguments: the release workflow sets them from the tag, the tag's commit and the build time, so a published `smo-<module>:<version>` image reports its own build and the chart needs no value for it. An image built by hand reports `unknown` unless built with `--build-arg SMO_BUILD_SHA=<commit>` (compose: export `SMO_BUILD_SHA` first). The operator GUI's Module health table shows them, and a module running a different commit than most is marked while a rolling upgrade is in progress.

## What is not in the chart

`netconf-lab` (a throwaway lab server) and `edge-tls` (the compose TLS terminator: use `ingress` with a TLS secret instead).

## Values

`values.yaml` is commented. The modules are one map (`modules`) and one template; a module is described by `image`, `kind` (`service` with `/live` and `/ready`, `worker` with a heartbeat file, `static` with a TCP probe), `database`, `enrollment`, `env`, `persistence`, `resources`; `moduleDefaults` is what each starts from. Every pod runs as the unprivileged user, with no capability, no privilege escalation and a read-only root filesystem (PR-SEC-13).

## Alerts

`prometheusRule.enabled=true` renders `files/smo-alerts.rules.yaml` (20 alerts, the burn-rate rules for three proposed SLOs, `docs/SLOS.md`) as a `PrometheusRule` of the Prometheus Operator; it is off by default because it needs that CRD. `prometheusRule.labels` is what your Prometheus selects rules on (for example `release: kube-prometheus-stack`), `prometheusRule.namespace` where to put it (default: the release's). The rules assume each module is scraped on `/metrics` as a job named after the module; the chart does not make the scrape configuration (a `ServiceMonitor` is not included). Without the Operator, use the same file as a plain `rule_files:` entry. Each alert's `runbook_url` is a page in `docs/runbooks/`.

## CI

`.github/workflows/smo-tests.yml`, job `helm`: lint, render with the options on, build the images from the checkout, install on kind, check the database is at the head revision, run the compose smoke scripts inside the cluster (`compose_e2e.py`, `compose_e2e_roles.py`), check no rApp can read the enrollment secret, upgrade (every module rolls, no pod fails), uninstall.

## Traces and logs

Off by default (`docs/OBSERVABILITY.md` has the queries and the ids). `tracing.endpoint` sets `SMO_OTEL_ENDPOINT` on every module (the OTLP/HTTP base URL of a Tempo or collector; `tracing.sampleRatio` sets `SMO_OTEL_SAMPLE_RATIO`); spans need images built with `--build-arg WITH_TRACING=1`, which the published images are not, and without them the modules propagate a `traceparent` and log the trace id only. `observability.tempo`, `.loki`, `.fluentBit` and `.grafana` (each `enabled: false`) add a lab stack: Tempo and Loki as single Deployments on an emptyDir, Grafana with both data sources provisioned and linked on the trace id, and Fluent Bit as a DaemonSet that ships this release's pod logs (JSON, labelled `service` and `level`) to Loki. With `observability.tempo.enabled` and no `tracing.endpoint` the modules send to `http://tempo:4318`. Their configuration is `files/observability/`, the files `docker-compose.yml` mounts for its `tracing` and `logging` profiles. The GUI's nginx and backend are not given the endpoint.

## GitOps

`deploy/gitops/` has Kustomize overlays (lab, staging, prod) over this chart and Argo CD Applications for them (`deploy/gitops/README.md`).
## Exposure of `/bootstrap` and the rate limiter (PR-SEC-9, PR-SEC-8.5)

`GET /bootstrap` on R1 Termination has no token (an rApp calls it to find SME before it has one) and reveals only SME's address and two API paths (`r1-termination/README.md`). Three controls, all off by default:

* **`bootstrapNetworkPolicy.enabled`** renders a NetworkPolicy on the `r1-termination` pods: ingress to the gateway port only from the pods of this release (the modules and the GUI backend) and `bootstrapNetworkPolicy.allowedSources`, a list of NetworkPolicyPeers (the rApp namespaces, the ingress controller's namespace, a scraper). **A NetworkPolicy selects pods and ports, not URL paths**, so it limits who can reach the whole gateway, `/bootstrap` with it, not `/bootstrap` alone. Once on it denies everything not listed, so list the rApps first; it needs a CNI that enforces NetworkPolicy.
* **`ingress.r1.bootstrapAllowedSourceRanges`** (ingress-nginx; with `ingress.enabled` and `ingress.r1.host`) renders a second Ingress `r1-bootstrap` for the exact path `/bootstrap` carrying `nginx.ingress.kubernetes.io/whitelist-source-range`; the `r1` Ingress and every other path are unchanged. This is the per-path control. Other ingress controllers need their own annotation.
* **`R1_BOOTSTRAP_KEY[_FILE]`**, the gateway's own shared key (`X-Bootstrap-Key`). The chart has no dedicated value, so that the key does not land in `values.yaml`: mount a Secret and set `modules.r1-termination.env.R1_BOOTSTRAP_KEY_FILE` on the gateway and `moduleDefaults.env.SMO_BOOTSTRAP_KEY_FILE` for every client (every module and rApp that reaches `/bootstrap` must send it, or its discovery gets a 401).

The rate limiter's buckets are per gateway replica by default. `modules.r1-termination.env.R1_RATE_STORE=postgres` (the chart sets `memory`, as compose does by default) keeps them in the shared table `rate_bucket` (migration `0028`; the gateway's database role has it) so replicas share one budget, at the price of one database round trip per authenticated request; if the database fails the limiter fails open to the replica's own bucket and counts it in `smo_rate_store_errors_total`. `HorizontalPodAutoscaler` or `replicas` above 1 on `r1-termination` is where this matters.

## Mutual TLS between the services (PR-SEC-2)

Off by default (`mtls.enabled: false`): nothing in the chart changes. With `mtls.enabled=true` every `service` module serves HTTPS and refuses a client without a certificate from the CA, every module's calls present its own certificate (`smo_shared/mtls.py`, `docs/ARCHITECTURE.md`, "Mutual TLS between services"), and each participating module mounts the Secret `<module>-mtls` (keys `tls.crt`, `tls.key`, `ca.crt`) at `/run/mtls`. A pod whose Secret does not exist stays in `ContainerCreating`, and one whose files are unreadable exits: a missing certificate never means plain HTTP. `modules.<name>.mtls` is `server` (default), `client` (calls with a certificate, serves plain HTTP: the GUI backend, whose caller is the GUI's nginx; a worker is always a client) or `off` (`mock-o1-adaptor`, a stand-in for a network function, and the GUI's nginx).

**Certificates, one of two ways.**

* `mtls.certManager.enabled=true`: the chart renders one cert-manager `Certificate` per participant (ECDSA P-256, usages digital signature, server auth and client auth, names `<module>`, `<module>.<namespace>.svc`, `<module>.<namespace>.svc.cluster.local` and `localhost`; `duration` 90 days, `renewBefore` 30 days) into its Secret. With `createCA: true` (default) it also makes the CA: a self-signed `Issuer`, a CA `Certificate` (10 years) and the `Issuer` `smo-mtls-ca` that signs the module certificates. With `createCA: false`, name your own `Issuer` or `ClusterIssuer` in `mtls.certManager.issuerRef` (the chart fails to render without a name). Needs cert-manager's CRDs; `helm template` without them still renders.
* Your own Secrets (`certManager.enabled: false`): create `<module>-mtls` for each participant from your CA, with the same three keys and the module's name and `localhost` among the subject alternative names. For a trial, `scripts/mtls_certs.py init --dir /tmp/mtls` makes a development CA and directories named after the modules, and then:

```bash
for m in $(ls /tmp/mtls | grep -v '^ca$\|^clients$'); do
  kubectl -n smo create secret generic "$m-mtls" --from-file=/tmp/mtls/$m/tls.crt --from-file=/tmp/mtls/$m/tls.key --from-file=/tmp/mtls/$m/ca.crt
done
```
(`SMO_MTLS_NAMES="sme.smo.svc,sme.smo.svc.cluster.local"` style extra names are one value for every certificate: for a cluster prefer cert-manager.) Never keep `ca/ca.key` on the cluster.

**Probes.** The kubelet's `httpGet` cannot present a certificate, so a server module's startup, readiness and liveness probes are `exec` probes running `python -m smo_shared.mtls probe /ready 8000` inside the container with the module's own certificate (`/live` for the other two). The static and `off` modules keep their probes. A plain probe port was not added: it would be an unauthenticated listener on every service.

**Reaching the gateway from outside.** `r1-termination` requires a client certificate, so the `r1` Ingress has to present one: set `mtls.ingressClientSecret` to `<namespace>/<name>` of a `kubernetes.io/tls` Secret that holds a client certificate (and `ca.crt`; `scripts/mtls_certs.py client ingress` makes one) and the chart adds the ingress-nginx annotations `backend-protocol: HTTPS`, `proxy-ssl-secret`, `proxy-ssl-verify: on` and `proxy-ssl-name: r1-termination` to the `r1` and `r1-bootstrap` Ingresses (other ingress controllers need their own). Callers that bypass the ingress (an rApp in the cluster) need a certificate from the same CA. A NetworkPolicy (`bootstrapNetworkPolicy`) is a separate control and still applies.

**Rotation.** Clients reread their mounted files when they change (a Secret update reaches the pod volume in about a minute); a server loads its files at start, so after cert-manager renews (or you replace a Secret) restart the modules one at a time: `kubectl -n smo rollout restart deploy/<module>` (the other modules keep calling it through its Service: the rolling update keeps a pod ready, and the CA is the same). A CA rotation with your own Secrets is three phases with a rolling restart after each (`scripts/mtls_certs.py rotate-ca trust|issue|retire`, its docstring); with cert-manager, put old and new CA into `ca.crt` (trust-manager or a manual bundle) before re-issuing. Expiry: every participating module exports `smo_mtls_cert_not_after_timestamp_seconds{file="cert"|"ca"}`; the alerts `SmoMtlsCertExpiring` and `SmoMtlsCertExpiryImminent` are in `files/smo-alerts.rules.yaml` (runbooks in `docs/runbooks/`).

**What it does not cover.** Postgres (`postgres.external.sslmode: verify-full` is the database's own setting; the bundled Postgres serves no TLS: `PR-SEC-2.4`, open); a service mesh (decided against: `HISTORY.md` PR-SEC-2); the certificate name is not used for identity (the token check is unchanged). A notification destination inside the release must be `https://` when mTLS is on. The render tests (`tests_integration/test_mtls.py`, with `helm`) cover the Secrets, the probes, the Certificates and the ingress annotations.

## Where the replicas land

With more than one replica of a module (`modules.<name>.replicas`, or `ci/ha-values.yaml` as an example), `placement.mode` decides how the pods are spread: `soft` (default) prefers different nodes and still starts on a cluster with fewer nodes than replicas, `hard` requires different nodes (a replica that cannot be placed stays Pending), `off` sets nothing. `placement.zoneKey: topology.kubernetes.io/zone` adds a preference across zones. The chart's CI checks that each mode renders as described; it runs on one node, so it does not show pods landing on different nodes.

## Database roles

A module with a `databaseRole` in `values.yaml` (Onboarding today) connects as `smo_<role>`, which can use its own schema and the shared tables it is given and nothing else (`docs/SECRETS.md`, per-module database roles). `databaseRoles.enabled` (default true) turns this on for every module that names a role; the passwords are in the Secret `smo-role-secrets` (made once, kept across upgrades, left in place by `helm uninstall`), and the migrate Job runs `scripts/db_roles.py` after the migration. Turning it off and on again on a running release is safe and is tested.

