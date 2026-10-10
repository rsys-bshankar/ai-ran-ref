/**
 * The page of one rApp (route /rapps/:instanceId, PR-GUI-8): what the rApp's package declares (drawn by `components/OperatorUi.tsx`, the same renderer for every rApp) followed by the platform overview every rApp has: lifecycle
 * with its actions, KPIs it reported, safeguards, faults and the version history. Every signed-in role may open it; the change buttons depend on the role (`canChange` from the BFF) and on the declaration being writable, and the lifecycle
 * buttons come from `Rapps.tsx` (`InstanceActions`) with the same permission checks. Pinning to the sidebar is limited to `MAX_PINS` per user. Covered by `RappPages.test.tsx`.
 */

import { Link, useParams } from "react-router-dom";

import { useSmo } from "../api/hooks";
import { MAX_PINS, usePinToggle, usePins, useRapp } from "../api/rapps";
import type { FaultReport, Instance, InstanceSafeguards, PerfReport } from "../api/types";
import { Sparkline } from "../components/charts";
import { DeclaredPage } from "../components/OperatorUi";
import { Card, DataTable, ErrorBox, Id, Json, KeyValue, PageHeader, SeverityChip, StateBadge } from "../components/ui";
import { describeLimits, describeScope, formatTime, metricSeries, numericMetricKeys } from "../lib/domain";
import { InstanceActions, VersionHistory } from "./Rapps";

/**
 * The page of one rApp, /rapps/<instance> (PR-GUI-8, GUI-8.4): the declared page first, then the platform overview. An unknown instance shows the BFF's error with a way back to the directory. The declaration is shown only when
 * the BFF reports it as "declared"; "unreadable" (stored by a newer build) and "none" each get a card saying so, and the overview is always shown.
 */
export function RappDetail() {
  const { instanceId = "" } = useParams();
  const rapp = useRapp(instanceId);
  const pins = usePins();
  const toggle = usePinToggle();
  const data = rapp.data;
  const pinnedCount = pins.data?.items.length ?? 0;
  if (rapp.error) {
    return (
      <>
        <PageHeader title="rApp" actions={<Link className="btn" to="/rapps">← Directory</Link>} />
        <ErrorBox error={rapp.error} />
      </>
    );
  }
  if (!data) return <div className="muted">Loading…</div>;
  return (
    <>
      <PageHeader title={`${data.name ?? "rApp"} ${data.version ?? ""}`.trim()}
        subtitle={<>{data.vendor ?? "unknown owner"} · <Id value={data.instanceId} /> · <StateBadge state={data.state} /> · <StateBadge state={data.autonomyMode} /></>}
        actions={<>
          <button className="btn" aria-pressed={data.pinned} disabled={toggle.isPending || (!data.pinned && pinnedCount >= MAX_PINS)}
            title={data.pinned ? "Remove from the sidebar" : pinnedCount >= MAX_PINS ? `At most ${MAX_PINS} pinned: unpin one first` : "Show in the sidebar"}
            onClick={() => toggle.mutate({ instanceId: data.instanceId, pin: !data.pinned })}>{data.pinned ? "★ Pinned" : "☆ Pin"}</button>
          <Link className="btn" to="/rapps">← Directory</Link>
        </>} />
      {data.declarationState === "declared" && data.declaration && (
        <DeclaredPage instanceId={data.instanceId} declaration={data.declaration} canChange={data.canChange} operatorApiRegistered={data.operatorApiRegistered} />
      )}
      {data.declarationState === "unreadable" && (
        <Card title="Operator page"><p className="muted">The page declared in this rApp's package could not be read by this console, so only the overview is shown.</p></Card>
      )}
      {data.declarationState === "none" && (
        <Card title="Operator page"><p className="muted">This rApp's package declares no operator page. The platform overview below is all there is for it.</p></Card>
      )}
      <Overview instanceId={data.instanceId} />
    </>
  );
}

/**
 * The platform overview of one rApp instance: lifecycle (state, package, workload, autonomy mode, region and access scope, with the instance actions), KPIs reported (a sparkline per numeric metric of the last 50 reports),
 * safeguards (stopped or limited, or that a terminated instance has no credential), the last 20 faults and the version history.
 */
function Overview({ instanceId }: { instanceId: string }) {
  const base = `/rapp-mgmt/instances/${instanceId}`;
  const inst = useSmo<Instance>(base);
  const perf = useSmo<PerfReport[]>(`${base}/performance`, { limit: 50 });
  const faults = useSmo<FaultReport[]>(`${base}/faults`, { limit: 20 });
  const safeguards = useSmo<InstanceSafeguards>(`${base}/safeguards`);
  const keys = numericMetricKeys(perf.data ?? []);
  return (
    <>
      <h2 className="section-title">Platform overview</h2>
      <Card title="Lifecycle" actions={inst.data ? <InstanceActions inst={inst.data} /> : undefined}>
        <ErrorBox error={inst.error} />
        {inst.data && <KeyValue items={[
          ["State", <StateBadge key="s" state={inst.data.state} />], ["Package", <code key="p">{inst.data.packageId}</code>],
          ["NFO deployment", inst.data.workloadRef && <code key="w">{inst.data.workloadRef}</code>],
          ["Autonomy mode", <StateBadge key="a" state={inst.data.autonomyMode} />],
          ["Region scope", inst.data.regionScope ? <Json value={inst.data.regionScope} /> : <span className="muted">—</span>],
          ["Access scope", <span key="z" title="Which managed elements, by region and tenant, this rApp may touch (set when the instance was created)">{describeScope(inst.data.authzScope)}</span>],
        ]} />}
      </Card>
      <Card title="KPIs reported">
        {keys.length === 0 ? <p className="muted">No performance reports.</p> : <div className="spark-list">{keys.map((k) => <Sparkline key={k} points={metricSeries(perf.data!, k)} label={k} width={300} />)}</div>}
      </Card>
      <Card title="Safeguards">
        {safeguards.error ? <p className="muted">The safeguards could not be read.</p> : !safeguards.data ? <p className="muted">…</p>
          : !safeguards.data.invokerId ? <p className="muted">A terminated instance has no credential, so nothing to stop or limit.</p>
          : <KeyValue items={[
            ["Status", safeguards.data.killed ? <span key="k"><StateBadge state="DISABLED" /> stopped by {safeguards.data.kill?.killedBy} ({safeguards.data.kill?.reason ?? "no reason given"}, {formatTime(safeguards.data.kill?.killedAt)})</span> : <StateBadge key="k" state="ACTIVE" />],
            ["Limits", describeLimits(safeguards.data.limits)],
          ]} />}
        <p className="small"><Link to="/safeguards">Stop or limit this rApp under Safeguards →</Link></p>
      </Card>
      <Card title="Faults">
        <DataTable rows={faults.data} rowKey={(f) => f.faultId} empty="No faults reported." columns={[
          { header: "Severity", render: (f) => <SeverityChip severity={f.severity} /> },
          { header: "Description", render: (f) => f.description ?? "—" },
          { header: "Reported", render: (f) => formatTime(f.reportedAt) },
        ]} />
      </Card>
      <Card>{inst.data ? <VersionHistory id={instanceId} state={inst.data.state} /> : <p className="muted">Version history…</p>}</Card>
    </>
  );
}
