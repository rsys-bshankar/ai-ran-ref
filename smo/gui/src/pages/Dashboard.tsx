/**
 * The landing page (route /, every signed-in role): module health (BFF /api/modules/status, polled every 10 s), open alarms by severity, SA SMOS escalations, model and rApp KPI sparklines, and fleet counts per state with
 * links to the pages that own them. Read-only. The counts come from the first page of each list (the modules' default page size is 100, `smo_shared/pagination.py`), so a fleet larger than that is under-counted here; the pages
 * themselves page through everything.
 */

import type { ReactNode } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, smo } from "../api/client";
import { POLL, unwrapPage, useSmo } from "../api/hooks";
import type {
  Alarm, AnalyticsReport, InstanceSummary, Intent, MlmfReport, Model, ModelLifecycle, ModulesStatus, NfDeployment,
  O1Endpoint, OCloudAlarm, Package, PerfReport, RemedialAction,
} from "../api/types";
import { CountBar, Sparkline } from "../components/charts";
import { Card, DataTable, ErrorBox, Id, PageHeader, StateBadge } from "../components/ui";
import { countBySeverity, metricSeries, moduleRows, numericMetricKeys, SEVERITIES } from "../lib/domain";

/**
 * The dashboard. Each tile or card reads its own list through `useSmo` (cached and shared with the other pages) and tolerates a failed or missing read by showing a dash or an empty text, so one module being down
 * leaves the rest of the page working. "Modules healthy" counts `healthy` over the modules the BFF reported (14 until the first answer arrives).
 */
export function Dashboard() {
  const status = useQuery<ModulesStatus>({ queryKey: ["bff", "modules-status"], queryFn: () => api("/modules/status"), refetchInterval: POLL.status });
  const alarms = useSmo<Alarm[]>("/ran-nf-oam/alarms", undefined, { refetchInterval: POLL.alarms });
  const ocloudAlarms = useSmo<OCloudAlarm[]>("/focom/alarms", undefined, { refetchInterval: POLL.alarms });
  const mlmf = useSmo<MlmfReport[]>("/aimgf/mlmf/reports", { limit: 40 });
  const escalations = useSmo<RemedialAction[]>("/sa-smos/remedial-actions", { outcome: "ESCALATED" });
  const instances = useSmo<InstanceSummary[]>("/rapp-mgmt/instances");
  const packages = useSmo<Package[]>("/onboarding/packages");
  const models = useSmo<Model[]>("/mlmr/models");
  const modelLifecycles = useSmo<ModelLifecycle[]>("/aimgf/model-lifecycles");
  const deployments = useSmo<NfDeployment[]>("/nfo/deployments");
  const endpoints = useSmo<O1Endpoint[]>("/ran-nf-oam/o1-adaptor-endpoints");
  const intents = useSmo<Intent[]>("/intent-service/intents");
  const analytics = useSmo<AnalyticsReport[]>("/mdaf/reports");

  const open = (alarms.data ?? []).filter((a) => a.severity !== "cleared");
  const sev = countBySeverity(open);
  const unacked = open.filter((a) => a.ackState === "UNACKNOWLEDGED").length;
  const healthy = status.data?.modules.filter((m) => m.healthy).length ?? 0;
  const total = status.data?.modules.length ?? 14;

  return (
    <>
      <PageHeader title="Dashboard" subtitle={status.data ? `Health checked ${new Date(status.data.checkedAt).toLocaleTimeString()} · refreshes every 10 s` : "Checking module health…"} />

      <div className="grid cols-4">
        <Stat label="Modules healthy" value={`${healthy}/${total}`} tone={healthy === total ? "ok" : "bad"} to="#health" />
        <Stat label="Open alarms" value={open.length} sub={`${unacked} unacknowledged`} tone={sev.critical ? "bad" : open.length ? "warn" : "ok"} to="/alarms" />
        <Stat label="SA SMOS escalations" value={escalations.data?.length ?? "—"} tone={escalations.data?.length ? "bad" : "ok"} to="/kpis#assurance" />
        <Stat label="MLMF floor breaches" value={(mlmf.data ?? []).filter((r) => r.breachedFloor).length} sub="in the last 40 reports" tone={(mlmf.data ?? []).some((r) => r.breachedFloor) ? "warn" : "ok"} to="/kpis#mlmf" />
      </div>

      <Card title="Module health" className="anchor" actions={<span className="muted small">GET /&lt;module&gt;/health, /ready and /version via R1 Termination</span>}>
        <div id="health" />
        <ErrorBox error={status.error} />
        <div className="health-grid">
          {(status.data?.modules ?? []).map((m) => (
            <div key={m.module} className={`health-tile ${m.healthy ? "up" : "down"}`} title={m.error ?? `HTTP ${m.statusCode}`}>
              <span className="dot" />
              <div><strong>{m.module}</strong><span className="muted small">{m.healthy ? `${m.latencyMs} ms` : m.error ?? `HTTP ${m.statusCode}`}</span></div>
            </div>
          ))}
        </div>
        <DataTable
          rows={status.data ? moduleRows(status.data.modules) : undefined}
          loading={status.isLoading}
          rowKey={(r) => r.module}
          columns={[
            { header: "Module", render: (r) => <strong>{r.module}</strong> },
            { header: "Readiness", render: (r) => <span className={`badge tone-${r.readiness === "READY" ? "ok" : r.readiness === "UNKNOWN" ? "muted" : "bad"}`}>{r.readiness}</span> },
            { header: "Version", render: (r) => r.version },
            { header: "Build", render: (r) => <><code>{r.buildSha}</code>{r.skewed && <span className="badge tone-warn" title="A different commit than most modules">differs</span>}</> },
            { header: "Built at", render: (r) => r.builtAt },
          ]}
        />
      </Card>

      <div className="grid cols-2">
        <Card title="Open alarms by severity" actions={<Link to="/alarms" className="small">Alarm console →</Link>}>
          <ErrorBox error={alarms.error} />
          <div className="sev-counts">
            {SEVERITIES.map((s) => <div key={s} className={`sev-count sev-${s}`}><span>{sev[s]}</span>{s}</div>)}
          </div>
          <CountBar parts={SEVERITIES.map((s) => ({ key: s, value: sev[s], className: `bar-${s}` }))} />
          <p className="muted small">RAN NF (O1 FaultMnS). O-Cloud (FOCOM) alarms: {ocloudAlarms.data?.length ?? "—"}</p>
        </Card>

        <Card title="SA SMOS escalations" actions={<Link to="/kpis#assurance" className="small">Assurance →</Link>}>
          {(escalations.data ?? []).length === 0 ? <p className="muted">No escalated remedial actions.</p> : (
            <ul className="plain-list">
              {escalations.data!.slice(-6).reverse().map((a) => (
                <li key={a.actionId}><StateBadge state={a.outcome} /> {a.actionType} on monitor <Id value={a.monitorId} /></li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid cols-2">
        <Card title="Model KPIs (MLMF)" actions={<Link to="/kpis#mlmf" className="small">All reports →</Link>}>
          <MlmfSparklines reports={mlmf.data ?? []} />
        </Card>
        <Card title="rApp performance" actions={<Link to="/kpis#rapp" className="small">Per instance →</Link>}>
          <RappSparklines instances={(instances.data ?? []).filter((i) => i.state === "RUNNING").slice(0, 3)} />
        </Card>
      </div>

      <Card title="Fleet">
        <div className="grid cols-4 tight">
          <Summary label="rApp packages" to="/rapps#packages" counts={byState(packages.data)} />
          <Summary label="rApp instances" to="/rapps#instances" counts={byState(instances.data)} />
          <Summary label="AI/ML models" to="/aiml#models" counts={byKey(models.data, (m) => modelLifecycles.data?.find((l) => l.modelId === m.modelId)?.modelLifecycleState ?? "REGISTERED")} />
          <Summary label="NF deployments (NFO)" to="/infrastructure#nfo" counts={byState(deployments.data)} />
          <Summary label="O1 adaptor endpoints" to="/infrastructure#o1" counts={byKey(endpoints.data, (e) => e.healthStatus)} />
          <Summary label="Intents" to="/policy#intents" counts={byKey(intents.data, (i) => i.intentAdminState)} />
          <Summary label="RAN analytics reports" to="/kpis#analytics" counts={byKey(analytics.data, (r) => r.analyticsType)} />
        </div>
      </Card>
    </>
  );
}

function byState<T extends { state: string }>(rows: T[] | undefined) {
  return byKey(rows, (r) => r.state);
}

/**
 * Counts rows per key (for the fleet summaries); undefined while the rows have not arrived, so the card shows a dash instead of zero.
 */
function byKey<T>(rows: T[] | undefined, key: (r: T) => string): Record<string, number> | undefined {
  if (!rows) return undefined;
  const out: Record<string, number> = {};
  for (const r of rows) out[key(r)] = (out[key(r)] ?? 0) + 1;
  return out;
}

function Stat({ label, value, sub, tone, to }: { label: string; value: ReactNode; sub?: string; tone: "ok" | "warn" | "bad"; to: string }) {
  const body = (<><span className="stat-label">{label}</span><span className="stat-value">{value}</span>{sub && <span className="muted small">{sub}</span>}</>);
  return to.startsWith("#") ? <a className={`stat tone-${tone}`} href={to}>{body}</a> : <Link className={`stat tone-${tone}`} to={to}>{body}</Link>;
}

/**
 * One fleet card: the total, a badge per state with its count, linking to the page and tab that manage those objects.
 */
function Summary({ label, counts, to }: { label: string; counts: Record<string, number> | undefined; to: string }) {
  const total = counts ? Object.values(counts).reduce((a, b) => a + b, 0) : undefined;
  return (
    <Link to={to} className="summary">
      <div className="row between"><span>{label}</span><strong>{total ?? "—"}</strong></div>
      <div className="summary-badges">
        {counts && Object.entries(counts).map(([k, v]) => <span key={k}><StateBadge state={k} /> {v}</span>)}
      </div>
    </Link>
  );
}

/**
 * The model KPI card: for up to four subscriptions, the first two numeric metrics of its reports as sparklines (oldest first).
 */
function MlmfSparklines({ reports }: { reports: MlmfReport[] }) {
  if (reports.length === 0) return <p className="muted">No MLMF performance reports yet.</p>;
  const bySub = new Map<string, MlmfReport[]>();
  for (const r of reports) bySub.set(r.subscriptionId, [...(bySub.get(r.subscriptionId) ?? []), r]);
  return (
    <div className="spark-list">
      {[...bySub.entries()].slice(0, 4).map(([sub, rs]) => (
        numericMetricKeys(rs).slice(0, 2).map((k) => (
          <div key={`${sub}-${k}`} className="spark-row">
            <span className="muted small">sub <Id value={sub} /></span>
            <Sparkline points={metricSeries(rs, k)} label={k} />
          </div>
        ))
      ))}
    </div>
  );
}

/**
 * The rApp performance card: for each given RUNNING instance (the caller passes at most three) the latest 30 performance reports, drawn as a sparkline of the first numeric metric; one read per instance through `useQueries`.
 */
function RappSparklines({ instances }: { instances: InstanceSummary[] }) {
  const perf = useQueries({
    queries: instances.map((i) => ({
      queryKey: ["smo", `/rapp-mgmt/instances/${i.instanceId}/performance`, { limit: 30 }],
      queryFn: async () => unwrapPage<PerfReport[]>(await smo<unknown>(`/rapp-mgmt/instances/${i.instanceId}/performance`, { query: { limit: 30 } })),
      refetchInterval: POLL.lists,
    })),
  });
  if (instances.length === 0) return <p className="muted">No RUNNING rApp instances.</p>;
  return (
    <div className="spark-list">
      {instances.map((inst, idx) => {
        const reports = perf[idx]?.data ?? [];
        const keys = numericMetricKeys(reports);
        return (
          <div key={inst.instanceId} className="spark-row">
            <span className="muted small">instance <Id value={inst.instanceId} /></span>
            {perf[idx]?.isLoading ? <span className="muted small">loading…</span>
              : keys.length === 0 ? <span className="muted small">no performance reports</span>
              : <Sparkline points={metricSeries(reports, keys[0])} label={keys[0]} />}
          </div>
        );
      })}
    </div>
  );
}
