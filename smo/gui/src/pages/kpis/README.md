# KPIs & Assurance

Route: `/kpis`    Design: handoff `Kpis.dc.html` (BRIEF §4 KPIs & Assurance, §4e feature 9; SCALE.md "KPIs & Assurance")

Network performance, the monitors that watch it, and what was done when a threshold broke. Tabs (URL hash): `overview` (default, new),
`pm`, `definitions`, `rapp`, `analytics`, `assurance`, `ocloud`, `mlmf`. The pre-redesign ids are unchanged; `mlmf` mounts
`pages/aiml` `Mlmf`.

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| kpis.tiles | sections/KpiTiles.tsx | 5 standard KPIs over 1 h / 24 h / 7 d | `/ran-nf-oam/kpis/{name}?from_time&group_by=all` ×5 | 60 s | 5 calls |
| kpis.worst | sections/WorstElements.tsx | 10 elements with the lowest DL UE throughput | `/ran-nf-oam/kpis/dl_ue_throughput?group_by=element` | 60 s | 1 call |
| kpis.monitors | sections/Monitors.tsx | assurance monitors, server-paged; click → panel | `/sa-smos/monitors` | 15 s | 1 call/page |
| kpis.monitor | sections/MonitorPanel.tsx | evaluate, remediate, escalate; actions taken | `/sa-smos/remedial-actions?monitor_id=&limit=1` | 15 s | 1 call |
| kpis.escalations | sections/Escalations.tsx | newest 5 escalated actions + true count | `/sa-smos/remedial-actions?outcome=ESCALATED&limit=5` | 15 s | 1 call |
| kpis.pm | sections/PmSubscriptions.tsx | PM subscriptions, server-paged; new subscription | `/ran-nf-oam/pm-subscriptions`, `/o1-adaptor-endpoints` | 15 s | 1–2 calls |
| kpis.definitions / schedules | sections/KpiDefinitions.tsx (+ KpiDialogs.tsx) | KPI catalogue and DME publishing schedules | `/ran-nf-oam/kpi-definitions?limit=200`, `/kpi-schedules` | 15 s | 2 calls |
| kpis.rapp | sections/RappPerformance.tsx | one instance's self-reported metrics | `/rapp-mgmt/instances`, `/…/{id}/performance?limit=100` | 15 s | 2 calls |
| kpis.mdaRequest | sections/MdaRequestForm.tsx | request an analysis (read-only today, see limits) | `/mdaf/mda-functions` (form only) | — | 0–1 call |
| kpis.mdaFunctions / mdaRequests / mdaReports | sections/MdaLists.tsx | TS 28.104 functions, requests, reports (kind filter, file download) | `/mdaf/mda-functions`, `/mda-requests`, `/mda-reports?report_kind=` | 15 s | 3 calls |
| kpis.analyticsReports / producers / analyticsSubs | sections/AnalyticsLegacy.tsx | legacy MDAF reports, producers, subscriptions | `/mdaf/reports`, `/ran-analytics/producers`, `/mdaf/subscriptions` | 15 s | 3–4 calls |
| kpis.producerTools | sections/ProducerTools.tsx | admin: register a producer, publish a report | `/dme/dme-types` | — | 1 call |
| kpis.newMonitor | sections/RegisterMonitor.tsx | register a monitor (operator+) | `/so-smos/orders`, `/mlmr/coordination-groups`, `/rapp-mgmt/instances` | 15 s | 3 calls |
| kpis.actions | sections/RemedialActions.tsx | remedial actions, `outcome` filter | `/sa-smos/remedial-actions`, `/sa-smos/monitors?limit=500` | 15–60 s | 2 calls |
| kpis.ocloud | sections/OCloudPerformance.tsx | FOCOM performance, `resource_ref` filter | `/focom/performance` | 15 s | 1 call/page |

First load of the Overview: 8 calls (5 tiles + worst list + monitors + escalations), over the 6-call target: RAN NF OAM has no batch KPI
route, so each tile is its own computation. The KPI reads poll every 60 s.

## Known limits

- **KPI sparklines and the throughput band chart** (p10–p90 over time): the KPI route computes one window per call; no bucketed series is
  served, so the tiles carry no sparkline and the band chart is replaced by the worst-10 list (with a note).
- **Per-cell heatmap / worst cells**: the worst list is per managed element (`group_by=element`); a per-cell ranking would be the same call with
  `group_by=cell` and is left out to keep the Overview's call count down.
- **Assurance monitors "breaching first"**: SA SMOS keeps no breach state on a monitor and the list has no such filter; the table is in server
  order and says so. "Now" values per monitor are not served (a monitor is evaluated on demand in its panel).
- **Request an analysis** (feature 9): `POST /mdaf/mda-requests` is not in the BFF's permission table (`gui-bff/app/rbac.py`), so the form is
  replaced by a read-only note; it appears by itself once the BFF allows the route. The report kind (ANALYTICS / PREDICTION / DRIFT) is not part
  of a request (MDAF types each report), so it is a filter on the reports table instead.
- **Scope picker** of the mockup: not built (no scope parameter on the KPI route beyond one element or cell).

## Troubleshooting

- A tile says "not defined (Definitions tab)": that standard KPI is not defined; "Add the standard set" in Definitions (admin).
- Tiles say "no data in window": no PM file with the KPI's counters arrived in the range; check PM subscriptions and the DME jobs.
- "The server capped the computation": RAN NF OAM read its file or group limit (`truncated`); narrow the range.
