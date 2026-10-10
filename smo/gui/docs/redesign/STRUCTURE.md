# Code structure — one folder per page, one file per section

**Goal:** every box on every page can be maintained, enhanced, debugged and upgraded on its own, without touching the rest of the page. Today each page is one large file (for example `pages/Aiml.tsx` at 521 lines and `pages/Infrastructure.tsx` at 456), with its data calls spread through the JSX.

## 1. Layout

```
src/
  shell/                     Sidebar, TopBar, ScopePicker, GlobalSearch, Layout
  kit/                       shared primitives (one file each + test)
    ServerTable.tsx  Pager.tsx  FilterChips.tsx  BulkBar.tsx  NewRowsBar.tsx
    Kpi.tsx  Meter.tsx  Segmented.tsx  Badge.tsx  SeverityChip.tsx
    Timeline.tsx  Steps.tsx  Callout.tsx  Diff.tsx  TopN.tsx  DrillCrumb.tsx
    states/ (Skeleton, Empty, ErrorRetry, Partial, Stale)   SectionBoundary.tsx
    charts/ (Sparkline, StackedBars, BandChart, RingGauge, Heatmap)
  data/
    client.ts  hooks.ts      (useSmo, useSmoPage, useSummary, useEvents)
    keys.ts                  every query key, in one place
    events.ts                SSE subscription → cache patches
  pages/
    <page>/
      index.tsx              route + layout only: places the sections in a grid
      README.md              the page's maintenance sheet (template in §3)
      sections/<Section>.tsx one box each
      data/queries.ts        this page's API paths, keys, polling and push topics
      data/types.ts          this page's view-model types (mapping from api/types)
      __tests__/<Section>.test.tsx
```

## 2. Rules

1. **`index.tsx` holds no logic.** It imports sections and lays them out. Each section is wrapped in `<SectionBoundary id="alarms.table">`, so a crash in one box shows an error-with-retry in that box and the rest of the page keeps working.
2. **A section owns its data.** It calls a hook from its page's `data/queries.ts`, never `useSmo` with a raw path. It renders all five states (loading, empty, error, partial, stale) using `kit/states`.
3. **Each section has a stable id.** `data-section="alarms.table"` goes on the root element. The same id is used in logs, error reports, tests, feature flags (`flags.alarms.table.grouping`) and the README.
4. **API knowledge lives in one place.** Only `data/queries.ts` knows paths, `limit`/`offset`, `total=false`, polling intervals and push topics. An API change touches that file only.
5. **Invalidation is targeted.** Mutations list the keys they affect from `data/keys.ts`; there's no blanket `["smo"]` invalidation (SCALE.md §1.5).
6. **Budgets are tested.** Each page's `__tests__` asserts the first-load call count is ≤ its budget (SCALE.md §4), using the mock BFF in `src/testing/bff.tsx`.
7. **Sections are small.** Aim for ≤ 200 lines per section. Split one that grows past that into sub-sections.

## 3. Page README template (`pages/<page>/README.md`)

```
# <Page>
Route: /alarms    Owner: <team>    Design: canvas board "Alarms · at scale"

## Sections
| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| alarms.tiles | sections/SeverityTiles.tsx | counts by severity × ack | /bff/summary/alarms | push + 60 s | 1 call |
| alarms.table | sections/AlarmTable.tsx | grouped server table | /ran-nf-oam/alarms?group_by=… | push | 1 call/page |

## Known limits
- e.g. grouping by cluster needs the region on the alarm row (ADR 0005)

## Troubleshooting
- Box shows "Partial": a module didn't answer; check /modules/status, then that module's logs
- Counts look capped at 100/500: a call without summary/total — check data/queries.ts
- Stale > 60 s: SSE dropped; see the browser console for "events: reconnect"

## Upgrade notes
- vX.Y: <what changed in the API or the UI and what to migrate>
```

## 4. Section map for every page (matches the canvas boards)

| Page folder | Sections (files under `sections/`) |
| --- | --- |
| `dashboard/` | `KpiTiles`, `HealthMap` (region → cluster drill), `WorstDus`, `NeedsAttention`, `AlarmTrend`, `AutonomySummary`, `PlatformHealth`, `FleetCounts` |
| `flows/` | `FlowList`, `FleetFunnel`, `SubjectPicker`, `SequenceLanes`, `StepTimeline`, `FlowActions`. One route per flow (`/flows/01` … `/flows/10`) driven by the `FLOWS` catalogue; per-flow data in `flows/data/flowNN.ts` (sources and funnel aggregate); evaluators stay in `lib/flows.ts` |
| `rapps/` | `SummaryTiles`, `PinnedAttention`, `FacetPanel`, `InstanceTable` (+ `BulkBar`), `PackagesTable`, `Rollouts`, `Directory` |
| `rapp-detail/` | `Header` (autonomy, pin, stop), `KpiTiles`, `CellStateDistribution`, `CellsInScope`, **`LifecycleFlows`** (flows 01, 06, 07 and the rApp's model flow 02), `LifecycleHistory`, `RecentDecisions`, `Safeguards`, `InstanceState`, `KpisReported`, `Faults`, `DeclaredPages` |
| `approvals/` | `Queue` (grouped/flat, filters), `GroupActions`, `Detail`, `ImpactTiles`, `ChangeDiff` (summary + paged diff), `Rationale`, `DecisionBox`, `Decided` |
| `decisions/` | `FilterBar`, `SummaryTiles`, `DecisionTable`, `DecisionChain`, `Integrity`, `ExportJob` |
| `safeguards/` | `SummaryTiles`, `LimitsTable` (+ bulk hold/stop), `GlobalStop`, `Refusals`, `Watchers` |
| `aiml/` | `StageBoard` (counts + top-N per stage), `ModelSearch`, `ModelDetail`, `GuardKpiChart`, `TrainingJobs`, `Governance`, `InferenceJobs`, plus one section per tab (feature groups, coordination, MLMF) |
| `intents/` | `SummaryTiles`, `IntentTable`, `IntentCard`, `Conflicts`, `NewIntentForm`, `Handlers`, `Dispatches` |
| `alarms/` | `SeverityTiles`, `AlarmTable` (group-by, bulk, new-rows bar), `AlarmDetail`, `RootCauseHint`, `OCloudAlarms`, `FmSubscriptions` |
| `kpis/` | `KpiTiles`, `ThroughputBand`, `WorstCells`, `CellHeatmap`, `MonitorsTable`, `Escalations`, `AnalyticsReports`, plus tab sections (PM subscriptions, definitions, rApp performance, O-Cloud) |
| `infrastructure/` | `TopologyLevels` (sites → pools → nodes → workloads), `NodeInspector`, `NfDeployments`, `OCloudInventory`, `O1Endpoints`, `ServiceOrders` |
| `data/` | `DataFlow` (aggregated bands), `DataJobs`, `ExposedServices`, `Invokers` |
| `security/` | `StatusTiles`, `Authenticator`, `RecoveryCodes`, `SignIns` |
| `admin/` | `UsersTable`, `RoleMatrix`, `AuditLog` |
| `login/` | `BrandPanel`, `SignInForm` |
| `topology/` | `RelationTiles`, `NeighbourGraph` (focus one element, ≤ 200 nodes), `RelationCheck`, `ElementSummary`, `ProblemRelations` |
| `element/` | `Header`, `Overview`, `ConfigHistory`, `SnapshotDiff`, `MoTree`, `MoAttributes`, `CellGuards`, `GuardEditor` |
| `configuration/` | `JobList`, `StagedJob` (waves, controls), `KpiGuard`, `NewJobForm`, `Vendors`, `CmSchemas`, `HostKeys`, `ElementOnboarding` |
| `software/` | `CampaignTiles`, `CampaignList`, `CampaignDetail` (waves, gate, controls, events), `CampaignElements`, `ElementJobs`, `NewCampaignForm` |
| `preferences/` | `ThemePicker`, `AccentPicker`, `TextSize`, `DisplayDefaults`, `LivePreview`, `SaveBar`; data via `data/queries.ts` → `/api/me/preferences`; applied by `shell/ThemeProvider.tsx` |

## 5. Migrating from today's files

| Today | Moves to |
| --- | --- |
| `components/Layout.tsx` | `shell/Layout.tsx`, `shell/Sidebar.tsx`, `shell/TopBar.tsx` |
| `components/ui.tsx` | split into `kit/*` (one component per file); `ActionButton` and `Can` → `kit/rbac.tsx` |
| `components/charts.tsx` | `kit/charts/*` |
| `components/Toast.tsx` | `kit/Toast.tsx` |
| `api/hooks.ts`, `api/client.ts` | `data/hooks.ts`, `data/client.ts` (+ `useSummary`, `useEvents`) |
| `pages/<Page>.tsx` | `pages/<page>/index.tsx` + `sections/*` + `data/queries.ts` |
| `pages/<Page>.test.tsx` | `pages/<page>/__tests__/*` (keep every existing assertion) |
| `lib/flows.ts` | stays as it is (it's pure and tested); `flows/` and `rapp-detail/LifecycleFlows` both import it |

Do it one page at a time, behind the same route, keeping tests green after each page. Start with `alarms/` and `dashboard/`, because they also carry the scale fixes.
