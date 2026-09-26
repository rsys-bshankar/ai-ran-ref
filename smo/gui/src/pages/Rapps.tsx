import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { useSmo, useSmoAction } from "../api/hooks";
import type { FaultReport, Instance, InstanceSummary, Package, PackageArtifact, PackageUsage, PerfReport } from "../api/types";
import { Sparkline } from "../components/charts";
import {
  ActionButton, Can, Card, DataTable, Drawer, ErrorBox, Field, Id, Json, KeyValue, Modal, PageHeader, SeverityChip,
  StateBadge, Tabs, useHashTab,
} from "../components/ui";
import { formatTime, metricSeries, numericMetricKeys, packageActions, parseJsonObject } from "../lib/domain";

const TABS = ["packages", "instances"] as const;

export function Rapps() {
  const [tab, setTab] = useHashTab(TABS, "packages");
  return (
    <>
      <PageHeader title="rApps" subtitle={<>Onboarding → rApp Management → NFO, per <code>docs/call-flows/01-rapp-onboarding-to-deployment.md</code></>} />
      <Tabs tabs={[{ id: "packages", label: "Packages" }, { id: "instances", label: "Instances" }]} value={tab} onChange={setTab} />
      {tab === "packages" ? <Packages /> : <Instances />}
    </>
  );
}

// ---------------------------------------------------------------- packages

function Packages() {
  const [state, setState] = useState("");
  const packages = useSmo<Package[]>("/onboarding/packages", { state });
  const [deployFrom, setDeployFrom] = useState<Package | null>(null);
  const [detail, setDetail] = useState<string | null>(null);
  return (
    <>
      <Can method="POST" path="/onboarding/packages"><OnboardForm /></Can>
      <Card title="Application packages" actions={
        <select value={state} onChange={(e) => setState(e.target.value)} aria-label="Filter by state">
          <option value="">All states</option>
          {["ONBOARDING", "AVAILABLE", "PRIMED", "DEPRECATED", "DELETING", "FAILED"].map((s) => <option key={s}>{s}</option>)}
        </select>}>
        <DataTable rows={packages.data} loading={packages.isLoading} error={packages.error} rowKey={(p) => p.packageId}
          empty="No packages onboarded yet." onRowClick={(p) => setDetail(p.packageId)} selectedKey={detail}
          columns={[
            { header: "Package", render: (p) => <><strong>{p.name}</strong> <span className="muted">{p.version}</span><div className="muted small">{p.vendor ?? ""} {p.applicationType}</div></> },
            { header: "ID", render: (p) => <Id value={p.packageId} /> },
            { header: "State", render: (p) => <StateBadge state={p.state} /> },
            { header: "Signature", render: (p) => (p.signatureVerified ? "verified" : <span className="muted">unverified</span>) },
            { header: "NF descriptor", render: (p) => <Id value={p.nfDeploymentDescriptorId} /> },
            {
              header: "", className: "actions", render: (p) => (
                <div className="row gap end">
                  {p.state === "AVAILABLE" && <Can method="POST" path="/rapp-mgmt/instances"><button className="btn primary" onClick={(e) => { e.stopPropagation(); setDeployFrom(p); }}>Deploy</button></Can>}
                  {packageActions(p.state).map((a) => (
                    <ActionButton key={a.action} label={a.label} tone={a.action === "delete" ? "danger" : "default"}
                      confirm={a.action === "delete" ? `Delete package ${p.name} ${p.version}?` : undefined}
                      action={a.action === "delete"
                        ? { method: "DELETE", path: `/onboarding/packages/${p.packageId}`, success: "Package delete requested" }
                        : { method: "POST", path: `/onboarding/packages/${p.packageId}/${a.action}`, success: `${a.label}: done` }} />
                  ))}
                </div>
              ),
            },
          ]} />
      </Card>
      {deployFrom && <CreateInstance pkg={deployFrom} onClose={() => setDeployFrom(null)} />}
      {detail && packages.data?.find((p) => p.packageId === detail) && <PackageDrawer pkg={packages.data.find((p) => p.packageId === detail)!} onClose={() => setDetail(null)} />}
    </>
  );
}

function PackageDrawer({ pkg, onClose }: { pkg: Package; onClose: () => void }) {
  const base = `/onboarding/packages/${pkg.packageId}`;
  const artifacts = useSmo<PackageArtifact[]>(`${base}/artifacts`);
  const usage = useSmo<PackageUsage[]>(`${base}/usage`);
  const instances = useSmo<InstanceSummary[]>("/rapp-mgmt/instances");
  const active = (usage.data ?? []).filter((u) => u.active);
  return (
    <Drawer title={`${pkg.name} ${pkg.version}`} onClose={onClose}>
      <div className="row between"><StateBadge state={pkg.state} /><Link className="btn small" to="/flows#06">Track in flow 06 →</Link></div>
      <KeyValue items={[
        ["Package ID", <code>{pkg.packageId}</code>], ["Vendor / type", `${pkg.vendor ?? "—"} / ${pkg.applicationType}`],
        ["TOSCA entry definitions", pkg.toscaEntryDefinitions], ["Signature", pkg.signatureVerified ? "verified (dev cert)" : "unverified"],
        ["NF deployment descriptor", pkg.nfDeploymentDescriptorId && <code>{pkg.nfDeploymentDescriptorId}</code>],
        ["Instances", String((instances.data ?? []).filter((i) => i.packageId === pkg.packageId).length)],
      ]} />
      <h3>Artifacts</h3>
      <DataTable rows={artifacts.data} error={artifacts.error} rowKey={(a) => a.artifactId} empty="No artifacts registered." columns={[
        { header: "Path", render: (a) => <code className="small">{a.path}</code> }, { header: "Access URL", render: (a) => <code className="small clip">{a.accessUrl}</code> },
      ]} />
      <h3>Priming</h3>
      <p className="muted small">
        {pkg.state === "PRIMED"
          ? active.length
            ? `PRIMED. Deprime is refused while ${active.length} usage registration(s) are active — terminate the instances using this package (or stop their usage) first.`
            : "PRIMED. No active usage, so deprime will succeed (PRIMED → DEPRIMING → AVAILABLE)."
          : pkg.state === "AVAILABLE"
            ? "AVAILABLE (commissioned). Prime pre-provisions the package: AVAILABLE → PRIMING → PRIMED."
            : `Priming applies to AVAILABLE packages; this one is ${pkg.state}.`}
      </p>
      {(pkg.state === "AVAILABLE" || pkg.state === "PRIMED") && (
        <div className="row gap">
          {pkg.state === "AVAILABLE"
            ? <ActionButton label="Prime" action={{ method: "POST", path: `${base}/prime`, success: "Package primed" }} />
            : <ActionButton label="Deprime" disabled={active.length > 0} title={active.length ? "Blocked by active usage" : undefined} action={{ method: "POST", path: `${base}/deprime`, success: "Package deprimed" }} />}
        </div>
      )}
      <h3>Usage registrations (cascade-delete guard)</h3>
      <p className="muted small">{active.length ? `${active.length} active registration(s): deprime and delete are blocked until they stop.` : "No active usage — delete and deprime are not blocked by usage."}</p>
      <DataTable rows={usage.data} error={usage.error} rowKey={(u) => u.registrationId} empty="No usage registrations." columns={[
        { header: "Consumer", render: (u) => <Id value={u.consumerId} /> },
        { header: "State", render: (u) => u.active ? <StateBadge state="ACTIVE" /> : <>stopped {formatTime(u.stoppedAt)}</> },
        { header: "", className: "actions", render: (u) => u.active && <ActionButton label="Stop" title="Simulates the consumer releasing the package"
          action={{ method: "POST", path: `${base}/usage/${u.registrationId}/stop`, success: "Usage stopped" }} /> },
      ]} />
      <Can method="POST" path={`${base}/usage/start`}>
        <div className="row gap"><ActionButton label="Register test usage" title="Simulates an instance holding this package, to exercise the guard"
          action={{ method: "POST", path: `${base}/usage/start`, query: { consumer_id: "smo-gui-test" }, success: "Usage registered" }} /></div>
      </Can>
    </Drawer>
  );
}

function OnboardForm() {
  const [location, setLocation] = useState("");
  const [applicationType, setApplicationType] = useState("rApp");
  const action = useSmoAction();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    action.mutate({ method: "POST", path: "/onboarding/packages", json: { location, applicationType }, success: "Onboarding accepted — watch the package state" },
      { onSuccess: () => setLocation("") });
  };
  return (
    <Card title="Onboard a package">
      <form className="form inline" onSubmit={submit}>
        <Field label="CSAR location (URL reachable from the Onboarding service)" hint="Validation runs asynchronously: the result shows up as the package state (AVAILABLE or FAILED).">
          <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="http://r1-termination:8899/hello-world-rapp.csar" required pattern="https?://.+" />
        </Field>
        <Field label="Application type"><select value={applicationType} onChange={(e) => setApplicationType(e.target.value)}><option>rApp</option><option>xApp</option></select></Field>
        <button className="btn primary" disabled={action.isPending}>Onboard</button>
      </form>
    </Card>
  );
}

function CreateInstance({ pkg, onClose }: { pkg: Package; onClose: () => void }) {
  const [config, setConfig] = useState("{}");
  const action = useSmoAction();
  const parsed = parseJsonObject(config);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!parsed.ok) return;
    action.mutate({ method: "POST", path: "/rapp-mgmt/instances", json: { packageId: pkg.packageId, config: parsed.value }, success: "Instance created (DEPLOYING)" },
      { onSuccess: onClose });
  };
  return (
    <Modal title={`Deploy ${pkg.name} ${pkg.version}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <p className="muted small">rApp Management checks the package is AVAILABLE, instantiates it on NFO from its NF deployment descriptor, and registers package usage.</p>
        <Field label="Instance configuration (JSON)" hint={parsed.ok ? "Optional. requiredResourceTypeId is passed to NFO for placement." : <span className="text-bad">{parsed.error}</span>}>
          <textarea rows={6} value={config} onChange={(e) => setConfig(e.target.value)} spellCheck={false} />
        </Field>
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!parsed.ok || action.isPending}>Deploy</button></div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- instances

function Instances() {
  const [state, setState] = useState("");
  const instances = useSmo<InstanceSummary[]>("/rapp-mgmt/instances", { state });
  const packages = useSmo<Package[]>("/onboarding/packages");
  const [selected, setSelected] = useState<string | null>(null);
  const pkgName = (id: string) => {
    const p = packages.data?.find((x) => x.packageId === id);
    return p ? `${p.name} ${p.version}` : null;
  };
  return (
    <>
      <Card title="rApp instances" actions={
        <select value={state} onChange={(e) => setState(e.target.value)} aria-label="Filter by state">
          <option value="">All states</option>
          {["DEPLOYING", "RUNNING", "UPGRADING", "FAULTED", "UNDEPLOYED"].map((s) => <option key={s}>{s}</option>)}
        </select>}>
        <DataTable rows={instances.data} loading={instances.isLoading} error={instances.error} rowKey={(i) => i.instanceId}
          empty="No instances. Deploy one from an AVAILABLE package." onRowClick={(i) => setSelected(i.instanceId)} selectedKey={selected}
          columns={[
            { header: "Instance", render: (i) => <Id value={i.instanceId} /> },
            { header: "Package", render: (i) => <>{pkgName(i.packageId) ?? <Id value={i.packageId} />}</> },
            { header: "State", render: (i) => <StateBadge state={i.state} /> },
            { header: "", className: "actions", render: (i) => <InstanceActions inst={i} /> },
          ]} />
      </Card>
      {selected && <InstanceDrawer id={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function InstanceActions({ inst, withUpgrade }: { inst: InstanceSummary; withUpgrade?: () => void }) {
  const base = `/rapp-mgmt/instances/${inst.instanceId}`;
  return (
    <div className="row gap end">
      {inst.state === "DEPLOYING" && <ActionButton label="Mark bootstrapped" title="Simulates the rApp container finishing R1 bootstrap + SME/DME registration (DEMO_RUNBOOK step)" action={{ method: "POST", path: `${base}/bootstrap-complete`, success: "Instance RUNNING" }} />}
      {inst.state === "RUNNING" && withUpgrade && <Can method="POST" path={`${base}/upgrade`}><button className="btn" onClick={(e) => { e.stopPropagation(); withUpgrade(); }}>Upgrade…</button></Can>}
      {inst.state === "UPGRADING" && <>
        <ActionButton label="Upgrade succeeded" action={{ method: "POST", path: `${base}/upgrade/resolve`, query: { succeeded: true }, success: "Upgrade committed" }} />
        <ActionButton label="Upgrade failed" action={{ method: "POST", path: `${base}/upgrade/resolve`, query: { succeeded: false }, success: "Upgrade rolled back" }} />
      </>}
      {inst.state === "FAULTED" && <ActionButton label="Recover" action={{ method: "POST", path: `${base}/recover`, success: "Recovering — instance re-enters DEPLOYING" }} />}
      {inst.state === "RUNNING" && <ActionButton label="Terminate" tone="danger" confirm="Terminate this rApp instance? Its workload is torn down and credentials revoked." action={{ method: "POST", path: `${base}/terminate`, success: "Instance UNDEPLOYED" }} />}
      {inst.state === "UNDEPLOYED" && <ActionButton label="Delete" tone="danger" confirm="Delete this instance record permanently?" action={{ method: "DELETE", path: base, success: "Instance deleted" }} />}
    </div>
  );
}

function InstanceDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const base = `/rapp-mgmt/instances/${id}`;
  const inst = useSmo<Instance>(base);
  const perf = useSmo<PerfReport[]>(`${base}/performance`, { limit: 50 });
  const faults = useSmo<FaultReport[]>(`${base}/faults`, { limit: 50 });
  const [upgrading, setUpgrading] = useState(false);
  const keys = numericMetricKeys(perf.data ?? []);
  return (
    <Drawer title={<>rApp instance <Id value={id} /></>} onClose={onClose}>
      <ErrorBox error={inst.error} />
      {inst.data && <>
        <div className="row between"><StateBadge state={inst.data.state} /><InstanceActions inst={inst.data} withUpgrade={() => setUpgrading(true)} /></div>
        <KeyValue items={[
          ["Instance ID", <code>{inst.data.instanceId}</code>], ["Package", <code>{inst.data.packageId}</code>],
          ["NFO deployment (workloadRef)", inst.data.workloadRef && <code>{inst.data.workloadRef}</code>],
          ["Pending upgrade to", inst.data.pendingUpgradeInstanceId && <code>{inst.data.pendingUpgradeInstanceId}</code>],
        ]} />
        <ConfigEditor id={id} config={inst.data.configuration ?? {}} />
      </>}
      <h3>Performance</h3>
      {keys.length === 0 ? <p className="muted">No performance reports.</p> : <div className="spark-list">{keys.map((k) => <Sparkline key={k} points={metricSeries(perf.data!, k)} label={k} width={300} />)}</div>}
      <h3>Faults</h3>
      <DataTable rows={faults.data} rowKey={(f) => f.faultId} empty="No faults reported." columns={[
        { header: "Severity", render: (f) => <SeverityChip severity={f.severity} /> },
        { header: "Description", render: (f) => f.description ?? "—" },
        { header: "Reported", render: (f) => formatTime(f.reportedAt) },
      ]} />
      <Can method="POST" path={`${base}/performance`}><InjectReports id={id} /></Can>
      {upgrading && inst.data && <UpgradeModal inst={inst.data} onClose={() => setUpgrading(false)} />}
    </Drawer>
  );
}

function ConfigEditor({ id, config }: { id: string; config: Record<string, unknown> }) {
  const [text, setText] = useState<string | null>(null);
  const action = useSmoAction();
  const path = `/rapp-mgmt/instances/${id}/config`;
  if (text === null) {
    return (
      <>
        <div className="row between"><h3>Configuration</h3><Can method="PUT" path={path}><button className="btn small" onClick={() => setText(JSON.stringify(config, null, 2))}>Edit</button></Can></div>
        <Json value={config} />
      </>
    );
  }
  const parsed = parseJsonObject(text);
  return (
    <>
      <h3>Configuration</h3>
      <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      {!parsed.ok && <p className="text-bad small">{parsed.error}</p>}
      <div className="row gap end">
        <button className="btn" onClick={() => setText(null)}>Cancel</button>
        <button className="btn primary" disabled={!parsed.ok || action.isPending}
          onClick={() => parsed.ok && action.mutate({ method: "PUT", path, json: parsed.value, success: "Configuration updated" }, { onSuccess: () => setText(null) })}>Save</button>
      </div>
    </>
  );
}

function UpgradeModal({ inst, onClose }: { inst: Instance; onClose: () => void }) {
  const packages = useSmo<Package[]>("/onboarding/packages", { state: "AVAILABLE" });
  const [target, setTarget] = useState("");
  const action = useSmoAction();
  const candidates = (packages.data ?? []).filter((p) => p.packageId !== inst.packageId);
  return (
    <Modal title="Upgrade instance" onClose={onClose}>
      <form className="form" onSubmit={(e) => {
        e.preventDefault();
        action.mutate({ method: "POST", path: `/rapp-mgmt/instances/${inst.instanceId}/upgrade`, json: { newPackageId: target }, success: "Upgrade started — resolve it once the new instance bootstraps" }, { onSuccess: onClose });
      }}>
        <p className="muted small">Starts the two-row upgrade: the instance goes UPGRADING while a replacement deploys; resolving success commits it, failure rolls back (auto-rollback, Annex A.1.2.2.1).</p>
        <Field label="New package (AVAILABLE)">
          <select value={target} onChange={(e) => setTarget(e.target.value)} required>
            <option value="">Choose…</option>
            {candidates.map((p) => <option key={p.packageId} value={p.packageId}>{p.name} {p.version}</option>)}
          </select>
        </Field>
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!target || action.isPending}>Start upgrade</button></div>
      </form>
    </Modal>
  );
}

function InjectReports({ id }: { id: string }) {
  const [metrics, setMetrics] = useState('{"throughputMbps": 120, "latencyMs": 8}');
  const [severity, setSeverity] = useState("minor");
  const [description, setDescription] = useState("");
  const parsed = parseJsonObject(metrics);
  const base = `/rapp-mgmt/instances/${id}`;
  return (
    <details className="admin-tools">
      <summary>Admin: inject test reports</summary>
      <p className="muted small">What the rApp itself would report. A <strong>critical</strong> fault crashes the instance to FAULTED.</p>
      <Field label="Performance metrics (JSON)"><textarea rows={3} value={metrics} onChange={(e) => setMetrics(e.target.value)} spellCheck={false} /></Field>
      <ActionButton label="Report performance" disabled={!parsed.ok} action={{ method: "POST", path: `${base}/performance`, json: parsed.ok ? parsed.value : {}, success: "Performance recorded" }} />
      <div className="form inline">
        <Field label="Fault severity"><select value={severity} onChange={(e) => setSeverity(e.target.value)}>{["warning", "minor", "major", "critical"].map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Description"><input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <ActionButton label="Report fault" tone={severity === "critical" ? "danger" : "default"} action={{ method: "POST", path: `${base}/fault`, query: { severity, description }, success: "Fault recorded" }} />
      </div>
    </details>
  );
}
