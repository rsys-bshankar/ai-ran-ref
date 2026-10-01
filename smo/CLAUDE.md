# Working in this repo

This is the Phase 1 SMO reference implementation — see `README.md` for
the stack/layout and `OPEN_ITEMS.md` for what's deliberately incomplete
and why. The rest of this file is working practice for anyone (human or
agent) changing code here, distilled from the backlog closed out in
`OPEN_ITEMS.md` section 6.

## Before pushing or opening a PR: run the full verification battery

Most of this is also enforced by CI (`.github/workflows/smo-tests.yml`,
which runs on every push/PR touching `smo/**`), but run it locally
first — it's faster to iterate on a failure here than to wait on a CI
run and read its logs, and `scripts/check_migration_matches_models.py`
needs a real Postgres CI doesn't give you locally unless you start one
yourself.

**1. Every module's own unit suite** (each module's `tests/` runs
standalone against SQLite, `PYTHONPATH` pointed at both itself and
`shared/`):

```bash
cd smo
for m in onboarding rapp-mgmt ran-nf-oam aimgf mlmr mllf so-smos \
         a1-related sme dme r1-termination nfo focom ran-analytics mdaf \
         intent-service sa-smos mock-near-rt-ric mock-o1-adaptor sdk; do
  (cd "$m" && PYTHONPATH=.:../shared python -m pytest tests/ -q) || break
done
(cd shared && PYTHONPATH=. python -m pytest tests/ -q)
(cd gui-bff && PYTHONPATH=.:../shared python -m pytest tests/ -q)
```

**2. Cross-service integration suite** (in-process service mesh,
`tests_integration/mesh.py` — includes the check that every committed
`docs/openapi/*.json` still matches each module's live FastAPI schema):

```bash
cd smo && PYTHONPATH=shared python -m pytest tests_integration/ -q
```

If you changed a route's request/response shape, regenerate the specs
before this check: `python scripts/generate_openapi_specs.py`.

**3. `docker-compose.yml` is structurally valid:**

```bash
cd smo && docker compose config --quiet
```

**4. Every ORM model's columns actually exist in the migration, with
matching nullability — against a real Postgres, not SQLite** (SQLite's
unit-test runs don't catch a `CHECK` constraint or a nullable/NOT NULL
mismatch the same way Postgres does — this caught a real regression
once, see `OPEN_ITEMS.md`'s §6.2 hotfix). Point `SMO_DATABASE_URL` at
any reachable Postgres 16 instance, or start one if you don't have one:

```bash
docker run --rm -d --name smo-verify-pg -e POSTGRES_USER=smo -e POSTGRES_PASSWORD=smo \
  -e POSTGRES_DB=smo -p 5432:5432 postgres:16-alpine
cd smo && SMO_DATABASE_URL=postgresql+psycopg://smo:smo@localhost:5432/smo \
  python scripts/check_migration_matches_models.py
```

**5. GUI — typecheck, unit tests, production build, and call-flow
diagram validation:**

```bash
cd smo/gui
npm run typecheck
npx vitest run
npx vite build
npm run validate:diagrams
```

`validate:diagrams` (`gui/scripts/validate-call-flow-diagrams.mjs`)
parses every mermaid block in `docs/call-flows/*.md` with mermaid's own
parser. It exists because GitHub's mermaid renderer is stricter than it
looks from the raw markdown — a bare `;` inside a `Note over`/message
string, for instance, silently breaks the sequence-diagram grammar and
only shows up as a render error on the PR, never in a text diff. Run it
(or let CI run it) after touching any file under `docs/call-flows/`.

## PR conventions

- Branch names: `claude/<kebab-case-description>`.
- PR body: a `## Summary` (what changed and why, referencing the
  `OPEN_ITEMS.md` section it closes if applicable) and a `## Test plan`
  checklist naming the specific battery steps above that were run.
- When a change closes (or partially closes) an `OPEN_ITEMS.md` item,
  update that section in the same PR — mark it closed with real
  implementation detail (not just "done"), and keep `README.md`'s status
  table in sync. A design choice deliberately *not* taken belongs in the
  same closure note, not left implicit.
- Don't merge your own PR on the assumption that's always wanted —
  that's a per-task call the person running the session makes explicitly
  each time, not a standing default this file grants.

## A couple of cross-cutting conventions worth knowing before you add code

- **Cross-module calls** always go through `smo_shared.r1_client.R1Client`
  (instantiated once per module as `_r1 = R1Client()`), proxied through
  R1 Termination's routing table — never a raw `httpx` call from one
  module straight to another's container.
- **Any caller-supplied notification/callback destination** (a
  `notificationDestination`/`callbackUri`/similar field accepted from a
  request body and POSTed/GETed/DELETEd back to later) goes through
  `smo_shared.webhook`'s `post_webhook`/`get_webhook`/`delete_webhook`,
  not a raw `httpx` call — see that module's docstring for why (CodeQL
  `py/full-ssrf`, and why a hostname allowlist isn't viable here). Adding
  a new callback-shaped field anywhere in this build should use it from
  the start rather than adding another ad-hoc `httpx.post` + swallow.
