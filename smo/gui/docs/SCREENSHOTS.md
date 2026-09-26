# Operator GUI — screenshots

Captured by a Playwright walk-through against the full local stack. All 13
modules, R1 Termination, the BFF and the real nginx config run against
Postgres 16, starting from an empty database. Every screen is live state
produced by the walk-through itself, signed in as `admin`, at 1440 px wide.

The walk-through drives each call flow in `smo/docs/call-flows` from the GUI
and then visits every page and tab. It finished with no console errors and no
5xx responses.

## Lifecycle flows (`/flows`)

| # | Flow | Result |
|---|------|--------|
| 01 | [rApp onboarding → running instance](screenshots/flows/f01.png) | 6/6, complete |
| 02 | [AI/ML model: register → train → certify → deploy → infer → monitor](screenshots/flows/f02.png) | 11/11, complete |
| 03 | [Configuration write, schema-checked, fleet-aware](screenshots/flows/f03.png) | `PARTIAL_SUCCESS`: one of the two MEs is deliberately unreachable |
| 04 | [Closed-loop assurance: monitor → decide → remediate → escalate](screenshots/flows/f04.png) | 5/5, `CONFIG_CHANGE` `RESOLVED` |
| 05 | [A1 EI registration → data consumption](screenshots/flows/f05.png) | 6/6, complete |
| 06 | [Package failure, deprecation and the cascade-delete guard](screenshots/flows/f06.png): [guard blocking delete](screenshots/flows/f06-blocked.png) | 5/5, complete |
| 07 | [rApp fault and performance reporting](screenshots/flows/f07.png) | 5/5, FAULTED → recovered → RUNNING |
| 08 | [RAN Analytics: producer → report → subscriber query](screenshots/flows/f08.png) | 5/5, complete |
| 09 | [Intent registration → fulfilment reporting → admin state](screenshots/flows/f09.png) | 5/5, complete |
| 10 | [SO SMOS multi-step order: INFRA → TRAINING → DEPLOY](screenshots/flows/f10.png) | 4/4, complete |

## Features added for PRs #88–#95

| Screen | What it shows |
|--------|---------------|
| [Package priming, deprime blocked](screenshots/features/rapps-priming-blocked.png) | PRIMED package with an active usage registration: Deprime is disabled, with the reason given |
| [Package priming, deprimed](screenshots/features/rapps-priming-done.png) | Usage stopped → deprime succeeds → back to AVAILABLE |
| [Coordination groups](screenshots/features/aiml-groups.png) | Create needs at least 2 members (AI/ML Workflow now returns 422 `COORDINATION_GROUP_TOO_SMALL`) |
| [Group-scoped remedial action](screenshots/features/kpis-assurance-group.png) | `SCALE` on a model-group monitor → `RESOLVED` via a group retrain; the new Scope column |
| [SME event subscriptions](screenshots/features/data-sme-events.png) | One unscoped subscription and one limited to a single service by `apiIds` |
| [A1 service supervision](screenshots/features/policy-services.png) | Keep-alive countdown for a supervised service |

## Every page and tab

| Page | Tabs |
|------|------|
| Dashboard | [overview](screenshots/pages/dashboard.png) |
| Lifecycle flows | [flow list](screenshots/pages/flows.png) |
| rApps | [packages](screenshots/pages/rapps-packages.png) · [instances](screenshots/pages/rapps-instances.png) |
| AI/ML | [models](screenshots/pages/aiml-models.png) · [training](screenshots/pages/aiml-training.png) · [inference](screenshots/pages/aiml-inference.png) · [coordination groups](screenshots/pages/aiml-groups.png) · [MLMF](screenshots/pages/aiml-mlmf.png) · [feature groups](screenshots/pages/aiml-features.png) |
| Alarms | [RAN](screenshots/pages/alarms-ran.png) · [O-Cloud](screenshots/pages/alarms-ocloud.png) |
| KPIs & Assurance | [rApp](screenshots/pages/kpis-rapp.png) · [PM](screenshots/pages/kpis-pm.png) · [MLMF](screenshots/pages/kpis-mlmf.png) · [RAN Analytics](screenshots/pages/kpis-analytics.png) · [assurance](screenshots/pages/kpis-assurance.png) · [O-Cloud](screenshots/pages/kpis-ocloud.png) |
| Policy & Intents | [A1 policies](screenshots/pages/policy-a1.png) · [status subscriptions](screenshots/pages/policy-status-subs.png) · [A1 services](screenshots/pages/policy-services.png) · [intents](screenshots/pages/policy-intents.png) · [handlers](screenshots/pages/policy-handlers.png) |
| Infrastructure | [NFO](screenshots/pages/infra-nfo.png) · [O-Cloud](screenshots/pages/infra-ocloud.png) · [topology (TEIV)](screenshots/pages/infra-topology.png) · [O1](screenshots/pages/infra-o1.png) · [service orders](screenshots/pages/infra-orders.png) |
| Data & Exposure | [DME](screenshots/pages/data-dme.png) · [A1 EI](screenshots/pages/data-a1-ei.png) · [SME](screenshots/pages/data-sme.png) |
| Admin | [users](screenshots/pages/admin-users.png) · [audit log](screenshots/pages/admin-audit.png) |
