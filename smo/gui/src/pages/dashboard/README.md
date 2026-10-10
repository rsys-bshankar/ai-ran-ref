# Dashboard

Route: `/`    Design: handoff `Main.dc.html` (BRIEF §4, SCALE.md "Dashboard · at scale")

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| dashboard.tiles | sections/KpiTiles.tsx | network health (gap), open alarms + severity meter, autonomous actions 24 h, awaiting approval, model guard breaches | `/api/summary/dashboard` | 15 s | 1 call (shared by every box) |
| dashboard.map | sections/HealthMap.tsx | region tiles → one region's managed elements (server-paged, link to `/elements/<ref>`), Back | `/ran-nf-oam/managed-entities?limit=100&total=false`; drill `?region=` | 60 s | 1 call (+1 per drill page) |
| dashboard.worst | sections/WorstDus.tsx | gap note; critical / major alarm totals with links | summary | — | 0 |
| dashboard.attention | sections/NeedsAttention.tsx | top 3 each: critical alarms, pending approvals, MLMF breaches, escalations, "+N more" from the summary | `/ran-nf-oam/alarms?severity=critical`, `/ran-nf-oam/rapp-approvals?status=PENDING`, `/aimgf/mlmf/reports?breached_only=true`, `/sa-smos/remedial-actions?outcome=ESCALATED` (each `limit=3&total=false`) | 60 s | 4 calls |
| dashboard.alarms | sections/AlarmTrend.tsx | open alarms by severity (counts + meter), O-Cloud alarm total | summary | 15 s | 0 |
| dashboard.autonomy | sections/AutonomySummary.tsx | decisions in 24 h by disposition, latest 6 decisions | summary; `/ran-nf-oam/decision-records?limit=6&total=false` | 60 s | 1 call |
| dashboard.platform | sections/PlatformHealth.tsx | "Modules healthy n/m", module tiles, readiness / version / build table (`#health`) | `/api/modules/status` (same cache entry as the sidebar) | 10 s | 0 extra |
| dashboard.fleet | sections/FleetCounts.tsx | instances, packages, NF deployments, intents by state; models, training jobs, elements | summary (`byState`) | 15 s | 0 |
| dashboard.trends | sections/Trends.tsx | MLMF model KPI and rApp performance sparklines | `/aimgf/mlmf/reports?limit=40`, `/rapp-mgmt/instances?state=RUNNING&limit=3`, `/rapp-mgmt/instances/{id}/performance?limit=30` | 15 s | on demand only (≤ 5 calls) |

First load: 8 data calls (summary, module status, 4 attention lists, decisions, one element page), down from 13 unbounded lists; SCALE.md's target
is 3, which needs a BFF "attention" aggregate (one call for the four top-3 lists). `__tests__/Dashboard.test.tsx` pins the budget at ≤ 8.

## Known limits

- **Network health** score is not served (BRIEF §5): the tile reads "—".
- **Health map**: there is no region list route, so region names come from the first 100 managed elements; per-region counts show only when that
  page was the whole fleet. Elements carry no site cluster, so the region → cluster level is skipped; alarms carry no region, so tiles are not
  coloured by worst state. The drill-down page size is the user's "rows per page" preference.
- **Worst DUs**: no server ranking of elements by open alarms; the box shows a gap note and alarm totals.
- **Alarm trend**: no per-hour counts on the server (SCALE.md P12); the box shows the current distribution by severity instead.
- **Unacknowledged alarms**, "lapses in under 10 min", "retraining triggered", and the "by rApp" autonomy split need counts the summary does not
  serve; they are left out.
- The pre-redesign Fleet box's O1 endpoints by health and RAN analytics reports by type were counted from a first list page; no summary count
  exists for them, so they are left out.
- No push stream yet (SCALE.md P7): the boxes poll (summary 15 s, lists 60 s).

## Troubleshooting

- Tiles read "—" with "Partial: …": that module did not answer the summary's count; check `/modules/status` and the module's logs.
- Counts look capped at 100: a box is counting a list; every count must come from `useDashboardSummary`.
- Health map shows no regions: no managed element has a region; set one with `PUT /ran-nf-oam/managed-entities/{me}/scope`.
