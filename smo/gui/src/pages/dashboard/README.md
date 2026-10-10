# Dashboard

Route: `/`    Design: handoff `Main.dc.html` (BRIEF §4, SCALE.md "Dashboard · at scale")

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| dashboard.tiles | sections/KpiTiles.tsx | network health score + unhealthy elements, open alarms + severity meter + unacknowledged, autonomous actions 24 h, awaiting approval, model guard breaches | `/api/summary/dashboard`; `/ran-nf-oam/managed-entities/health?group_by=region` (shared with the map) | 15 s / 60 s | 1 call + the map's |
| dashboard.map | sections/HealthMap.tsx | region tiles (elements, unhealthy, coloured by worst open severity) → the region's site clusters → their managed elements (server-paged, link to `/elements/<ref>`); crumb back up | `/ran-nf-oam/managed-entities/health?group_by=region`; drill `?group_by=site_cluster&region=`; elements `/ran-nf-oam/managed-entities?region&site_cluster` | 60 s | 1 call (+1 per drill level) |
| dashboard.worst | sections/WorstDus.tsx | top 10 elements by open critical, major, then all alarms; links to the element and its alarms | `/ran-nf-oam/managed-entities/worst?limit=10` | 60 s | 1 call |
| dashboard.attention | sections/NeedsAttention.tsx | top 3 each: critical alarms, pending approvals, MLMF breaches, escalations, each with its true total and "+N more"; a group whose module did not answer says so | BFF `/api/summary/attention?limit=3` (one fan-out on the server, GUI-9.8b); pushed as `event: attention` on the `summary:attention` topic | pushed / 60 s | 1 call |
| dashboard.alarms | sections/AlarmTrend.tsx | alarms raised per hour over 24 h, stacked by severity; open alarms by severity (counts + meter), unacknowledged, O-Cloud alarm total | `/ran-nf-oam/alarms/counts?group_by=hour`; summary | 60 s / 15 s | 1 call |
| dashboard.autonomy | sections/AutonomySummary.tsx | decisions in 24 h by disposition, latest 6 decisions | summary; `/ran-nf-oam/decision-records?limit=6&total=false` | 60 s | 1 call |
| dashboard.platform | sections/PlatformHealth.tsx | "Modules healthy n/m", module tiles, readiness / version / build table (`#health`) | `/api/modules/status` (same cache entry as the sidebar's environment chip) | 10 s | 0 extra (the shell's read) |
| dashboard.fleet | sections/FleetCounts.tsx | instances, packages, NF deployments, intents by state; models, training jobs, elements | summary (`byState`) | 15 s | 0 |
| dashboard.trends | sections/Trends.tsx | MLMF model KPI and rApp performance sparklines | `/aimgf/mlmf/reports?limit=40`, `/rapp-mgmt/instances?state=RUNNING&limit=3`, `/rapp-mgmt/instances/{id}/performance?limit=30` | 15 s | on demand only (≤ 5 calls) |

First load: **6 page calls** (summary, attention, decisions, fleet health, worst elements, hourly alarm counts), every one bounded or an
aggregate computed on the server; module status is the shell's read (the sidebar's environment chip asks it on every page; the Dashboard reads
the same cache entry). `__tests__/Dashboard.test.tsx` pins the budget at ≤ 6 and checks that the four attention lists are never asked one by
one. While the summary stream is open (top bar "Live · pushed") the counts and the attention groups are pushed (topics `summary:nav`,
`summary:dashboard`, `summary:attention`), and an alarm count change refetches the health map, worst DUs and hourly bars. SCALE.md's target of
3 would need the health map, worst elements, hourly counts and latest decisions folded into the summary as well.

**Scope** (GUI-9.3, the top bar's picker): every call above carries `region` / `site_cluster` (the summary and attention as query
parameters, the RAN NF OAM aggregates as their own filters, the latest decisions by their elements). What the scope cannot narrow says
"network-wide": the model breaches tile, the MLMF and escalation groups, the O-Cloud alarm total and the fleet rows of packages, NF deployments,
intents, models and training jobs (the BFF's `unscoped` keys).

## Known limits

- **Network health** is RAN NF OAM's definition (share of managed elements without an open critical or major alarm); it says nothing about
  KPIs.
- **Health map**: elements with no region form a "no region" tile that cannot be opened (no route filters on "region is empty"); a region's
  elements with no site cluster open as the region's whole element list. The drill-down page size is the user's "rows per page" preference.
- **Alarm trend** buckets are UTC hours (the server's), not the user's time zone.
- "Lapses in under 10 min", "retraining triggered", and the "by rApp" autonomy split need counts the summary does not serve; they are left out.
- The pre-redesign Fleet box's O1 endpoints by health and RAN analytics reports by type were counted from a first list page; no summary count
  exists for them, so they are left out.

## Troubleshooting

- Tiles read "—" with "Partial: …": that module did not answer the summary's count; check `/modules/status` and the module's logs.
- Counts look capped at 100: a box is counting a list; every count must come from `useDashboardSummary`.
- Health map shows only "no region": no managed element has a region; set one with `PUT /ran-nf-oam/managed-entities/{me}/scope`. Site clusters
  are set by an admin on the element page (`PUT /ran-nf-oam/managed-entities/{me}/site-cluster`).
