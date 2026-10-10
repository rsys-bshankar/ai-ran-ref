# Scale & latency audit — every page, every box

**Short answer:** no, the first version of the designs was drawn at lab size (5 DUs, 6 rApps, 4 alarms). The redesigned artboards that cover the gaps found here are marked "· at scale" on the canvas. This file is the checklist Claude Code works through. Every box on every page has a row, a verdict and a fix.

## 0. Design targets

The targets come from the repo's own load work: `docs/SIZING.md`, `docs/PERFORMANCE.md` and `OPEN_ITEMS.md` (PR-GUI-8, "100 rApps"). Confirm them or change them.

| Object | Design target | Stretch (tested in CI) |
| --- | --- | --- |
| Managed elements (DUs/CUs/RUs) | 10,000 | 100,000 |
| Cells | 30,000 | 300,000 |
| Alarms, open / stored | 10,000 / 1,000,000 | 1,000,000 stored |
| rApp packages / instances | 200 / 500 | — |
| AI/ML models (all versions) | 500 | — |
| Intents / handlers | 1,000 / 50 | — |
| NF deployments / O-Cloud sites / nodes | 5,000 / 50 / 2,000 | — |
| Decisions and audit records | 100,000 per day | — |
| Operators signed in at once | 50 | — |

**"Lead time" — the latency budget for every page.** The p95 figures are measured on the target data size, through the gateway.

| Moment | Budget |
| --- | --- |
| Shell and skeletons painted (cached JS) | ≤ 300 ms |
| First data on screen | ≤ 1.0 s |
| Page complete | ≤ 2.0 s |
| Filter, sort or page change | ≤ 500 ms |
| Click feedback | ≤ 100 ms (optimistic, or a spinner on the control) |
| Live-data staleness | alarms and approvals ≤ 5 s, health ≤ 10 s, inventory ≤ 60 s |
| API calls on first load | ≤ 6 per page |
| Steady-state polling per open tab | ≤ 0.3 req/s |

## 1. What breaks today (found in the code at `12a1b90`)

1. **Lists are silently cut at 100 rows.** The backend pages every list (`smo_shared/pagination.py`: `DEFAULT_LIMIT = 100`, `MAX_LIMIT = 500`). `useSmo` calls without a `limit` and `unwrapPage` drops `total`, so most pages show the first 100 rows and nothing says so.
2. **Counts are computed in the browser from that first page.** For example, the Dashboard's open alarms, unacknowledged and severity counts come from `alarms.data.filter(...)` (`Dashboard.tsx` lines 30–32), and the fleet totals and escalations are `.length` of a first page. Past 100 rows every count on the Dashboard is wrong, and it caps at 100.
3. **There are too many calls per page.** The Dashboard makes 13 list calls, Flows 31, Infrastructure 18 and AI/ML 17.
4. **The polling would overload the gateway.** The Dashboard polls alarms every 5 s and the other lists every 15 s. That's about 1.1 requests/s per open tab. `docs/SIZING.md` measures the gateway at about 26 ms of CPU per request, saturating near 39 req/s per core. **About 35 operators on the Dashboard would saturate one gateway core with polling alone.**
5. **Every action refetches everything.** `useSmoAction` invalidates `["smo"]` and `["bff"]` on every mutation, so an alarm ack refetches every list in the tab.
6. **Nothing is virtualized.** No table or list virtualizes its rows, and the DOM and React cost grows with every row rendered.
7. **The total count isn't free.** `COUNT(*)` costs 55 ms per million alarms. Pages that only page forward should send `?total=false` and use `hasMore`.
8. **Nothing pushes updates.** There's no SSE or WebSocket, so the GUI polls everything.

## 2. Global patterns (build once, use on every page)

| # | Pattern | Rule |
| --- | --- | --- |
| P1 | **Server-side table** | Sorting, filtering, search and paging all happen on the server. Use the `limit`/`offset` envelope, or keyset `?after=` where the backend offers it (`PR-DB-4.4`). The footer always says "Showing 1–50 of 12,480" (or "50 shown · more" when `total=false`), with a page-size choice of 25/50/100. Never `.filter()` an unbounded list in the browser. |
| P2 | **Summary endpoints** | Every tile, count, meter and chart reads an aggregate (count by severity, by state, by region), not a list. One BFF call per page: `/bff/summary/<page>` returns all its tiles and is cached about 5 s in the BFF, shared by every operator. |
| P3 | **Scope first** | A global scope picker in the top bar (region → site cluster → DU), backed by ADR 0005's region/tenant scope. Every page, query and chart respects it, and the URL carries it (`?region=eu-west&cluster=metro-a`). |
| P4 | **Top-N + "see all"** | Dashboard boxes show the worst 5–10 items, ranked on the server, then link to the full filtered table. Never all items. |
| P5 | **Aggregate, then drill** | Maps, graphs and timelines show clusters (region, site, pool) with counts and worst state; clicking expands one level. There's a hard cap of about 200 nodes per view, and the rest is summarised as "+1,240 more". |
| P6 | **Virtualized rows** | Any list that can pass about 200 rows uses `@tanstack/react-virtual`: fixed row height, sticky header. |
| P7 | **Live updates by push** | The BFF exposes one SSE stream per tab (`/bff/events?topics=alarms,approvals,health`). The tab patches its cache from events, and polling drops to a 60 s safety refresh. Hidden tabs pause (`refetchIntervalInBackground: false`, which is already the TanStack default). |
| P8 | **Targeted invalidation** | A mutation invalidates only the keys it touches (an ack touches `alarms` and `summary/alarms`), not `["smo"]`. |
| P9 | **Saved views** | Filters, sort, columns and scope live in the URL. Users can save a view and pin it to the sidebar (this replaces "pin rApp" at scale). |
| P10 | **Explicit states** | Every box has loading skeleton, empty, error-with-retry, partial ("2 of 21 modules didn't answer") and stale ("updated 40 s ago") states. |
| P11 | **Bulk actions** | Tables with a checkbox column, select-all-matching ("all 1,204 matching"), and bulk ack/approve/stop with a confirm that shows the count. |
| P12 | **Time range bounds** | Charts default to 24 h, and longer ranges come back down-sampled from the server (≤ 300 points per series). Raw rows are never sent for charts. |

## 3. Page by page, box by box

Verdict: ✅ fine at scale as drawn · ⚠️ works but needs the pattern · ❌ breaks, redesigned.

### Shell

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| Sidebar nav and badges | ⚠️ | Badges come from the summary endpoint and show "999+" past 999. | P2 |
| Pinned rApps (max 5) | ⚠️ | Keep it, and add saved views. | P9 |
| Environment chip "21/21" | ⚠️ | Use the scope picker plus module health from `/modules/status` (fine: 21 modules, a fixed set). | P3 |
| Global search ⌘K | ❌ in the first version (client-side) | Server-side typeahead across DUs, cells, rApps, alarms and models: one `/bff/search?q=` call, debounced 200 ms, top 5 per type. | new BFF route |
| Notifications bell | ⚠️ | Counts by push; the panel lists the latest 20. | P7 |

### Dashboard (`Main` → redesigned "Dashboard · at scale")

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| KPI tiles (health, alarms, actions, approvals, breaches) | ❌ counts from the first 100 rows | One summary call. Tiles show the true totals and "per region" on hover. | P2 `/bff/summary/dashboard` |
| RAN topology (5 DU nodes) | ❌ unreadable past about 30 nodes | Replaced by a **network health map**: regions → site clusters as a grid of tiles, sized by cell count and coloured by worst severity, with a count of unhealthy DUs in each. Click to drill in (region → cluster → DU list). Plus a "Worst 10 DUs" ranked list. | P3, P5, aggregate by region/cluster |
| Needs your attention | ⚠️ | Top-N per type (critical alarms, approvals, breaches, escalations), max 3 each, then "+N more". | P4 |
| Alarm trend 24 h | ⚠️ | Server-bucketed counts per hour by severity (24 × 4 numbers). | P12 |
| Autonomy activity feed | ⚠️ | Latest 6 plus "view all"; counts by outcome from the summary. | P4 |
| Module health (21 tiles) | ✅ | 21 is a fixed set of SMO modules. At replicas > 1, show per-module "3/3 ready". | — |
| AI/ML pipeline counts | ⚠️ | Counts by state from the summary. | P2 |
| Fleet | ❌ client-side counts | Summary counts by state. | P2 |
| **Calls** | 13 lists + polling | **3**: summary, attention top-N, activity, then the SSE stream. | |

### Lifecycle flows

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| Flow list (9 flows) | ✅ | It's a fixed catalogue (`lib/flows.ts`). | — |
| Subject picker (a `<select>` of every package/model/…) | ❌ | A searchable combobox with server typeahead and the last 5 used. | P1 |
| Sequence + steps for one subject | ✅ | Per-subject by design: it shows **one** journey end to end (all stages of that flow). | — |
| **Missing:** the fleet view | ❌ | **New "Fleet funnel"**: for each flow, how many subjects sit at each step ("Flow 01: 412 packages: 380 running, 18 at bootstrap, 9 failed at instantiate, 5 at descriptor"). Click a step to see the subjects stuck there, paged. This answers "all levels/stages" across hundreds of subjects. | aggregate per step (BFF computes from states) |
| Data loading (31 calls) | ❌ | Load only the selected flow's sources, and only for the chosen subject. The funnel reads one aggregate. | P2 |

### rApps (redesigned "rApps · at scale")

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| 4 big instance cards | ❌ at 100+ | Cards become a **"Pinned & attention" strip** (pinned plus faulted/upgrading, max 6). The **catalog table** is the main view. | P4 |
| Lifecycle column (new) | ✅ | A 5-segment mini stepper for flow 07, plus the stage name. It comes from the instance state in the same row, so it needs no extra call. | — |
| Catalog / instances table | ❌ 100-row cut | Server table with facets: state, autonomy mode, owner, category (energy / mobility / coverage / steering / analytics …), vendor, health. Saved views, bulk actions (set autonomy, stop, upgrade), virtualized rows, "Showing 1–50 of 487". | P1, P6, P9, P11 |
| Package pipeline | ⚠️ | Counts by state from the summary. | P2 |
| Upgrade in progress (one) | ⚠️ | "Rollouts" list (paged) with a canary/wave progress for each. | P1 |
| Directory & pages | ⚠️ | Already has search and paging (`RappDirectory.tsx`), so keep that; group by category. | — |

### rApp detail (one rApp)

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| Header, KPI tiles | ✅ | One rApp. | — |
| Cell-state swimlane (6 cells) | ❌ at 1,000s of cells per rApp | Show the **distribution over time** (a stacked area of cell counts by state) plus a paged "cells in this rApp's scope" table with a search box; a swimlane opens for ≤ 20 selected cells. | P5, P12 |
| **Lifecycle flows for this rApp** (new) | ✅ | Flows 01 (onboarding → running), 06 (package), 07 (instance) and 02 (its model) as one-line steppers built from `lib/flows.ts`, plus a paged lifecycle history. It's one rApp, so it's bounded; it costs one call per flow subject. | P1 for the history |
| Recent decisions | ⚠️ | Latest 10, then paged in Decisions, filtered to this rApp. | P1 |
| Safeguards meters | ✅ | Per rApp. | — |
| KPIs chart | ⚠️ | Down-sampled. | P12 |
| Faults | ⚠️ | Paged. | P1 |

### Approvals

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| Queue (cards) | ⚠️ | Today it's `limit: 100`. Sort by lapse time; filter by rApp, region and risk; group "Mobility Opt. · 14 similar"; bulk approve or reject a group with one reason. | P1, P11 |
| Detail: diff | ⚠️ | The diff can be 1,000s of elements in one job. Show a summary ("CIO +2 dB on 340 relations in 3 clusters"), group the diff by parameter, and page it at 50 lines with "download full diff". | P1 |
| Expected impact, why, decision | ✅ | Per approval. | — |

### Decisions

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| Filters + table | ⚠️ | Server table; 100k/day needs a time range by default (last 24 h) and keyset paging. | P1 |
| KPI tiles | ❌ client-side | Summary. | P2 |
| Detail chain, integrity | ✅ | One record. | — |
| Export CSV | ⚠️ | Make it a server-side async export job, not a browser loop. | new route |

### Safeguards

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| One card per rApp | ❌ at 500 rApps | A table (rApp, mode, state, change limit, jobs/h used, refusals 24 h) with filters and bulk "hold for approval". Cards only for the selected rApp. | P1, P11 |
| Stop all | ⚠️ | A server-side global stop (one call), with a confirm that names the count. | new route |
| Refusals | ⚠️ | Paged and time-bounded. | P1 |
| Watchers | ✅ | Few. | — |

### AI/ML

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| Kanban by stage | ❌ at 500 models | Columns show counts and the top 5 by last change, with "+N" opening a paged table filtered to that stage. Group versions under one model ("coverage-model · 6 versions"). | P4, P1 |
| Model detail, guard KPI chart | ⚠️ | Down-sampled; the chart shows the last 40–200 reports. | P12 |
| Training / inference / governance cards | ⚠️ | Top-N plus paged tables in their tabs. | P4 |
| Data loading (17 calls) | ❌ | Load per tab. | — |

### Intents

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| Intent cards (4) | ❌ at 1,000 | A table by default (purpose, state, priority, fulfilment %, handler, last report), with cards as an optional view, and a "not fulfilled" filter first. | P1 |
| Conflict callout | ⚠️ | Conflicts are computed on the server; show a count and a list. | new |
| New intent form | ✅ | — | — |

### Alarms (redesigned "Alarms · at scale")

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| Severity tiles | ❌ client-side counts | Summary counts per severity × ack state, per scope. | P2 |
| Alarm table | ❌ 100-row cut, 5 s polling of the full list | Server table, virtualized, 50 per page; the default sort is severity then time. **Group by** probable cause, managed element or cluster ("LOS · 37 alarms · 12 DUs"). Bulk ack/clear on a group. Pushed updates add a "12 new — show" bar, so rows don't jump under the cursor. | P1, P6, P7, P11 |
| Trend sparkline | ⚠️ | Bucketed. | P12 |
| Detail panel | ✅ | One alarm. | — |
| Root-cause hint | ⚠️ | Server-side correlation (`PR-MGT-9`, `MGT-10` in `OPEN_ITEMS`). Until that exists, show "same element within 60 s". | — |
| O-Cloud tab | ⚠️ | Same table pattern. | P1 |

### KPIs & Assurance

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| KPI tiles | ⚠️ | Use the backend's `compute_kpi` `group_by=all`, which already exists in ran-nf-oam. | P2 |
| "Throughput by DU" (4 lines) | ❌ at 10k DUs | Show the **distribution** (p10/p50/p90 band) for the scope, plus "worst 10 cells" ranked, and a cell × hour **heatmap** for ≤ 200 cells after drilling in. `group_by` cell/element/sectorGroup exists. | P5, P12 |
| Assurance monitors table | ⚠️ | Server table, with a "breaching" filter first. | P1 |
| Escalations | ⚠️ | Top-N. | P4 |
| PM subscriptions, definitions | ⚠️ | Paged (definitions already use `limit`). | P1 |

### Infrastructure (redesigned "Infrastructure · at scale")

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| TEIV topology graph | ❌ at 2,000 nodes / 5,000 workloads | **Collapsed by level**: O-Cloud sites → pools (counts and worst health) → expand one pool to show its nodes (paged after 50) → expand a node to show its workloads. Search jumps to a node. A breadcrumb shows the path. The cap is about 200 visible nodes. | P5 |
| Inspector | ✅ | One node. | — |
| NF deployments, O1 endpoints, orders tabs | ⚠️ | Server tables. | P1 |
| Data loading (18 calls) | ❌ | Load per tab. | — |

### Data & Exposure

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| Data-flow diagram | ⚠️ | Aggregate by data type and consumer category; at most 8 bands per column with "+N other". | P5 |
| Data jobs table | ⚠️ | Server table with a "late" filter. | P1 |
| Exposed services | ⚠️ | Paged with search. | P1 |

### Account security / Admin

| Box | Verdict | Fix | Data |
| --- | --- | --- | --- |
| Security page | ✅ | One user. | — |
| Users table | ⚠️ | Paged with search (users can number in the 100s with SSO). | P1 |
| Role matrix | ✅ | Fixed. | — |
| Audit log | ❌ unbounded | Time-bounded, keyset paged, async export. | P1 |

## 4. Call budget, before → after

| Page | First-load calls today | After | Polling after |
| --- | --- | --- | --- |
| Dashboard | 13 | 3 | SSE + 60 s refresh |
| Flows | 31 | 2–6 (per flow) | 15 s, selected subject only |
| AI/ML | 17 | 2 per tab | 15 s, visible tab only |
| Infrastructure | 18 | 2 per tab | 60 s |
| Alarms | 5 | 2 (summary, page) | SSE |
| rApps | 11 | 2 | 15 s |

With these budgets, 50 operators come to about 50 × 0.3 ≈ 15 req/s, plus SSE. That's under half of one gateway core, against more than one full core at today's design.

## 5. Back-end asks this creates (confirm before building)

1. BFF `/bff/summary/<page>`: aggregates, cached about 5 s and shared across users.
2. BFF `/bff/events`: SSE fan-out for alarms, approvals, health and decisions.
3. BFF `/bff/search?q=`: cross-object typeahead.
4. Group-by / count endpoints on alarms (by severity, ack, cause, element, region), instances (by state, mode) and models (by stage), or the BFF computes them with `total=false` counts.
5. Keyset pagination (`PR-DB-4.4`) on alarms, decisions and audit.
6. Server-side async CSV export.
7. Global safeguard stop.

The GUI work should start with P1, P2, P8 and P10. These fix today's silent 100-row cut and wrong counts even before the redesign lands.

### New pages (features 1–11)

| Page / box | Verdict | Rule |
| --- | --- | --- |
| Topology graph | ✅ as drawn | Focus one element (its cells + first-ring neighbours, ≤ 200 nodes). A cluster view uses the health map pattern (P5). Problem relations are a server table (P1). |
| Software campaigns | ✅ | Campaign list paged; elements per campaign paged per wave; counts from campaign `summary` (no client counting). |
| Config jobs | ✅ | Job list paged (halted first); sub-changes summarised, paged in detail. |
| Element detail | ✅ | Per element. Config history keyset-paged; MO tree loads children on expand (`/children?limit=`). |
| MSAC, vendors, schemas, host keys, registry, MDA, formulas | ✅ | Server tables (P1). |
| O-Cloud levels | ✅ | One level at a time, paged; counts per level from a summary call. |
