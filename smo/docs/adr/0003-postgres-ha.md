# ADR 0003 — Postgres high availability: an operator on Kubernetes, the connection string stays ours

Status: accepted (`PR-DB-7.1`). Date: 2026-10-04.

## Context

Since 0.3.0 the Helm chart runs a one-pod Postgres (`postgres.enabled`) or talks to one the operator runs (`postgres.external.*`). The
one-pod database is a single point of failure: a node loss or an upgrade stops every module until it comes back. v0.4.0 aims at
surviving the loss of the database primary with no committed work lost and a recovery time that is measured, not guessed
(`PR-DB-7`, `PR-HA-3`). Three routes were weighed: Patroni, a Postgres operator on Kubernetes, and a managed service.

## Decision

1. **The chart does not grow its own HA database.** `postgres.enabled=true` stays what it is: one pod, for a lab or a first look,
   documented as not highly available. High availability is reached through `postgres.external.*`.
2. **The supported, tested HA route on Kubernetes is an operator, CloudNativePG.** It runs a primary and streaming replicas as
   ordinary pods, promotes a replica itself, and publishes a read-write Service that follows the primary, so a service's connection
   string does not change on failover. The lab (`DB-7.2`) installs the operator at a pinned version and a three-instance cluster in the
   kind job, and the failover test (`DB-7.4`) runs against that.
3. **A managed service (RDS, Cloud SQL, Azure Database) is equally supported and untested by us.** Anything that gives one
   read-write endpoint and survives a primary loss behind it fits `postgres.external.*`; the chart needs nothing more. We state what
   we ran and do not claim the rest.
4. **The connection string is where we help.** The shared code accepts several hosts and `target_session_attrs=read-write`
   (libpq's own multi-host support, which psycopg 3 uses), so a deployment without a moving Service (a plain pair of VMs, say) still
   reconnects to the new primary. `DB-7.3` proves this with a switchover; where the stack's pool or driver does not recover, that is
   fixed there, not worked around in the chart.
5. **docker-compose stays single-node.** Compose is the quick-start and the demonstration, not the production shape; the README says so.
   It gets PgBouncer (`DB-5`) as an optional profile, not replication.

## Why not the others

- **Patroni.** It is mature, but it needs a distributed configuration store (etcd or Consul) that the chart would then have to ship
  and operate, a custom Postgres image, and its own health checks wired to probes. An operator packages the same idea for Kubernetes
  with less to maintain on our side. Patroni remains the right answer for VMs and is covered by decision 4: the connection string
  works with it unchanged.
- **Bundling the operator or its Cluster resource in our chart.** The operator is cluster-scoped (CRDs, a controller with wide
  permissions), and installing one from an application chart overreaches what a namespace-scoped install should do. The operator
  is an installation prerequisite for this route, like the ingress controller.
- **Doing nothing and relying on a managed service.** Fine for an operator who has one; the project would then never test a failover.

## Consequences

- The HA lab needs network access in CI to fetch the operator manifest at a pinned version; the digest of what ran is recorded in the
  job output.
- Recovery time is a number the failover test records (`DB-7.4`, `HA-3.1/3.2`), and the CHANGELOG states it with the test's own limits.
- A deployment on a managed database or Patroni gets the same code path; only the lab covers the operator.
- Backups are separate (`DB-6`, `HA-6`): replication is not a backup, and the docs say so next to this decision. The recovery targets, the off-site copy and the runbook are in `docs/DISASTER_RECOVERY.md`.
