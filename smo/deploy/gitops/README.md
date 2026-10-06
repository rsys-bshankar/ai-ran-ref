# GitOps example (PR-OPS-6)

Git as the source of truth for what runs: one overlay per environment, rendered from the Helm chart (`deploy/helm/smo`), and Argo CD Applications that keep a cluster equal to it.

```
deploy/gitops/
  base/                  the namespace `smo` (the chart wants a namespace of its own)
  overlays/lab/          the chart's own Postgres and generated secrets, one replica, Tempo + Loki + Fluent Bit + Grafana on
  overlays/staging/      external Postgres, secrets you create, two replicas of the stateless modules, ingress, disruption budgets
  overlays/prod/         pinned release, external HA Postgres, three replicas of the gateway and SME, hard node and zone spread
  argocd/application.yaml            Argo CD renders the chart with an overlay's values.yaml (recommended)
  argocd/application-kustomize.yaml  Argo CD builds an overlay with Kustomize (needs `--enable-helm`)
```

Each overlay is two files: `values.yaml`, only what differs from the chart's defaults, and `kustomization.yaml`, which renders the chart with it (`helmCharts`, `chartHome: ../../../helm`) next to the base. Build one:

```bash
kustomize build --enable-helm deploy/gitops/overlays/staging | kubectl apply -n smo -f -     # or let Argo CD do it
```

**Secrets are never in these files.** Staging and production name `secrets.existingSecret: smo-secrets` and `databaseRoles.existingSecret: smo-role-secrets`; create them out of band (External Secrets, Sealed Secrets, the platform's vault) before the first sync, with the keys `deploy/helm/smo/README.md` lists. Every host, registry and Postgres address in the overlays is a placeholder (`*.example.com`); replace them.

**Argo CD.** `application.yaml` points at the chart and passes `../../gitops/overlays/lab/values.yaml` as a value file (a path relative to the chart), so Argo CD renders with Helm itself and needs no build option. Change `repoURL` to your fork or mirror, `targetRevision` to a tag (`smo-v0.5.0`) for staging and production, and the overlay name. With `automated.prune` and `selfHeal` a change in Git is applied and a change made by hand in the cluster is reverted. The chart's migrate Job (`helm.sh/hook: pre-upgrade`) runs before the Deployments change and every pod waits for the schema in an init container, so no sync-wave ordering is needed. `application-kustomize.yaml` is the same through Kustomize, for a cluster whose Argo CD already uses it; it requires `kustomize.buildOptions: --enable-helm` in `argocd-cm`.

**Tested.** `tests_integration/test_gitops.py` (CI): every YAML parses, each file's paths exist (the chart, the base, the value files, the Argo CD `path` and `valueFiles`), every key an overlay sets exists in the chart's `values.yaml` (a typo is otherwise accepted by Helm and does nothing), no overlay has a key named like a credential; with `kustomize` and `helm` on the path each overlay is also built and its output checked. The CI job `helm` builds all three overlays. **Not tested:** a sync by Argo CD on a cluster (`OPEN_ITEMS.md`, `OPS-6.2`).
