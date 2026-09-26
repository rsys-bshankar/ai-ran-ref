import { useState, type ReactNode } from "react";
import { useQueries } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { smo } from "../api/client";
import { POLL, useSmo } from "../api/hooks";
import type {
  AnalyticsProducer, AnalyticsReport, AnalyticsSubscription, ConfigJob, ConfigJobSummary, DataJob, DataOffer, DmeType, EiType,
  FaultReport, InferenceJob, Instance, InstanceSummary, Intent, IntentReport, MlmfReport, MlmfSubscription, Model, Monitor,
  NfDeployment, O1Endpoint, Package, PackageUsage, PerfReport, RemedialAction, Rmih, ServiceOrder, SmeService, TrainingJob,
} from "../api/types";
import { ActionButton, Card, Id, PageHeader, StateBadge, useHashTab } from "../components/ui";
import {
  FLOWS, flow01, flow02, flow03, flow04, flow05, flow06, flow07, flow08, flow09, flow10, progress, type FlowStep,
} from "../lib/flows";
import { modelActions } from "../lib/domain";

const IDS = FLOWS.map((f) => f.id) as readonly string[];

export function Flows() {
  const [flowId, setFlowId] = useHashTab(IDS, "01");
  const flow = FLOWS.find((f) => f.id === flowId)!;
  return (
    <>
      <PageHeader title="Lifecycle flows" subtitle={<>Every end-to-end journey in <code>smo/docs/call-flows</code>, tracked live against real SMO state. Pick a flow, then the entity to follow.</>} />
      <div className="flows-layout">
        <nav className="flow-list" aria-label="Call flows">
          {FLOWS.map((f) => (
            <button key={f.id} className={`flow-item ${f.id === flowId ? "active" : ""}`} onClick={() => setFlowId(f.id)}>
              <span className="flow-num">{f.number}</span>
              <span><strong>{f.title}</strong><span className="muted small">{f.modules.join(" · ")}</span></span>
            </button>
          ))}
        </nav>
        <div className="flow-body">
          <Card title={<>{flow.number} · {flow.title}</>} actions={<code className="small">docs/call-flows/{flow.doc}</code>}>
            {flowId === "01" && <Flow01 />}
            {flowId === "02" && <Flow02 />}
            {flowId === "03" && <Flow03 />}
            {flowId === "04" && <Flow04 />}
            {flowId === "05" && <Flow05 />}
            {flowId === "06" && <Flow06 />}
            {flowId === "07" && <Flow07 />}
            {flowId === "08" && <Flow08 />}
            {flowId === "09" && <Flow09 />}
            {flowId === "10" && <Flow10 />}
          </Card>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- shared bits

const ICON: Record<FlowStep["status"], string> = { done: "✓", current: "●", todo: "○", failed: "✕", blocked: "–", warn: "!" };

function Timeline({ steps, actions = {} }: { steps: FlowStep[]; actions?: Record<string, ReactNode> }) {
  const p = progress(steps);
  return (
    <>
      <div className="flow-progress">
        <div className="bar"><span style={{ width: `${(p.done / p.total) * 100}%` }} className={p.failed ? "bad" : p.complete ? "ok" : ""} /></div>
        <span className="muted small">{p.done}/{p.total} steps{p.complete ? " — flow complete" : p.failed ? " — stopped at a failure" : ""}</span>
      </div>
      <ol className="timeline">
        {steps.map((s, i) => (
          <li key={s.id} className={`tl-step tl-${s.status}`}>
            <span className="tl-icon" aria-label={s.status}>{ICON[s.status]}</span>
            <div className="tl-main">
              <div className="row between wrap">
                <strong>{i + 1}. {s.title}</strong>
                <span className="muted small">{s.actor}</span>
              </div>
              {s.detail && <div className="small tl-detail">{s.detail}</div>}
              {(s.status === "current" || s.status === "warn" || s.status === "failed") && actions[s.id] && <div className="tl-action">{actions[s.id]}</div>}
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}

function Pick<T>({ label, items, value, onChange, render, id, empty, extra }: {
  label: string; items: T[] | undefined; value: string; onChange: (v: string) => void; render: (t: T) => string; id: (t: T) => string;
  empty: ReactNode; extra?: ReactNode;
}) {
  if (items && items.length === 0) return <div className="flow-subject muted">{empty}</div>;
  return (
    <div className="flow-subject row gap wrap">
      <label className="field inline-field"><span className="field-label">{label}</span>
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          {(items ?? []).map((t) => <option key={id(t)} value={id(t)}>{render(t)}</option>)}
        </select>
      </label>
      {extra}
    </div>
  );
}

/** The selected id, or the first item's (newest last in every list, so take the last). */
function useSelection<T>(items: T[] | undefined, id: (t: T) => string): [string, (v: string) => void, T | undefined] {
  const [value, set] = useState("");
  const chosen = items?.find((t) => id(t) === value) ?? items?.[items.length - 1];
  return [chosen ? id(chosen) : "", set, chosen];
}

const go = (to: string, label = "Open") => <Link className="btn small" to={to}>{label} →</Link>;

// ---------------------------------------------------------------- 01 rApp onboarding → running

function Flow01() {
  const packages = useSmo<Package[]>("/onboarding/packages");
  const [pkgId, setPkg, pkg] = useSelection(packages.data, (p) => p.packageId);
  const instances = useSmo<InstanceSummary[]>("/rapp-mgmt/instances");
  const summary = instances.data?.filter((i) => i.packageId === pkgId).at(-1);
  const instance = useSmo<Instance>(summary ? `/rapp-mgmt/instances/${summary.instanceId}` : null);
  const deployments = useSmo<NfDeployment[]>("/nfo/deployments");
  const deployment = deployments.data?.find((d) => d.nfDeploymentId === instance.data?.workloadRef || d.name === `rapp-instance-${summary?.instanceId}`);
  const steps = flow01(pkg, instance.data, deployment);
  return (
    <>
      <Pick label="Package" items={packages.data} value={pkgId} onChange={setPkg} id={(p) => p.packageId}
        render={(p) => `${p.name} ${p.version} (${p.state})`} empty={<>No packages yet. {go("/rapps#packages", "Onboard one")}</>} />
      <Timeline steps={steps} actions={{
        onboard: go("/rapps#packages", "Onboard a package"),
        validate: go("/rapps#packages", "Packages"),
        create: go("/rapps#packages", "Deploy from the package"),
        bootstrap: summary && <ActionButton label="Mark bootstrapped" tone="primary" action={{ method: "POST", path: `/rapp-mgmt/instances/${summary.instanceId}/bootstrap-complete`, success: "Instance RUNNING" }} />,
      }} />
    </>
  );
}

// ---------------------------------------------------------------- 02 AI/ML

function Flow02() {
  const models = useSmo<Model[]>("/mlmr/models");
  const [modelId, setModel, model] = useSelection(models.data, (m) => m.modelId);
  const q = modelId ? { model_id: modelId } : undefined;
  const jobs = useSmo<TrainingJob[]>(modelId ? "/aimgf/training-jobs" : null, q);
  const inference = useSmo<InferenceJob[]>(modelId ? "/aimgf/inference-jobs" : null, q);
  const subs = useSmo<MlmfSubscription[]>(modelId ? "/aimgf/mlmf/subscriptions" : null, q);
  const reportLists = useQueries({
    queries: (subs.data ?? []).map((s) => ({
      queryKey: ["smo", `/aimgf/mlmf/subscriptions/${s.subscriptionId}/reports`, {}],
      queryFn: () => smo<MlmfReport[]>(`/aimgf/mlmf/subscriptions/${s.subscriptionId}/reports`),
      refetchInterval: POLL.lists,
    })),
  });
  const reports = reportLists.flatMap((r) => r.data ?? []);
  const steps = flow02(model, jobs.data ?? [], inference.data ?? [], subs.data ?? [], reports);
  const running = (inference.data ?? []).find((j) => j.status === "RUNNING");
  const next = model ? modelActions(model.state) : [];
  const nextAction = next[0] && (next[0].kind === "train"
    ? <ActionButton label={next[0].label} tone="primary" action={{ method: "POST", path: "/aimgf/training-jobs", json: { modelId, producerId: "smo-gui" }, success: "Training job started" }} />
    : <ActionButton label={next[0].label} tone="primary" action={{ method: "POST", path: `/aimgf/models/${modelId}/advance`, query: { event: next[0].event }, success: `${next[0].event} done` }} />);
  return (
    <>
      <Pick label="Model" items={models.data} value={modelId} onChange={setModel} id={(m) => m.modelId}
        render={(m) => `${m.modelType} v${m.version} (${m.state})`} empty={<>No models registered. {go("/aiml#models", "Register one")}</>} />
      <Timeline steps={steps} actions={{
        register: go("/aiml#models", "Register a model"),
        train: nextAction, tested: nextAction, emulated: nextAction, certified: nextAction, loaded: nextAction, active: nextAction,
        deploy: go("/aiml#models", "Choose node groups"),
        infer: running
          ? <ActionButton label="Mark inference completed" tone="primary" title="Simulates MLEF finishing the job (result delivered via DME)"
              action={{ method: "POST", path: `/aimgf/inference-jobs/${running.inferenceJobId}/resolve`, query: { succeeded: true }, success: "Inference COMPLETED" }} />
          : model?.state === "ACTIVE" && <ActionButton label="Request inference" tone="primary" action={{ method: "POST", path: `/aimgf/models/${modelId}/inference-jobs`, success: "Inference job RUNNING" }} />,
        monitor: go("/aiml#mlmf", "Subscribe"),
        report: go("/aiml#mlmf", "MLMF reports"),
      }} />
    </>
  );
}

// ---------------------------------------------------------------- 03 config write

function Flow03() {
  const endpoints = useSmo<O1Endpoint[]>("/ran-nf-oam/o1-adaptor-endpoints");
  const jobs = useSmo<ConfigJobSummary[]>("/ran-nf-oam/config-jobs");
  const [jobId, setJob] = useSelection(jobs.data, (j) => j.jobId);
  const job = useSmo<ConfigJob>(jobId ? `/ran-nf-oam/config-jobs/${jobId}` : null);
  const steps = flow03(endpoints.data ?? [], job.data);
  const stale = (endpoints.data ?? []).filter((e) => e.healthStatus !== "ACTIVE");
  return (
    <>
      <Pick label="Config job" items={jobs.data} value={jobId} onChange={setJob} id={(j) => j.jobId}
        render={(j) => `${j.jobId.slice(0, 8)} · ${j.scope} · ${j.status}`} empty={<>No config jobs yet. {go("/infrastructure#o1", "Write configuration")}</>} />
      <Timeline steps={steps} actions={{
        registry: go("/infrastructure#o1", "Register an O1 endpoint"),
        health: <div className="row gap wrap">{stale.map((e) => (
          <ActionButton key={e.endpointId} label={`Heartbeat ${e.managedElementRef}`} title="Simulates the ME's O1 adaptor heartbeat"
            action={{ method: "POST", path: `/ran-nf-oam/o1-adaptor-endpoints/${e.endpointId}/heartbeat`, success: `${e.managedElementRef} heartbeat` }} />
        ))}</div>,
        write: go("/infrastructure#o1", "New config write"),
      }} />
      {job.data && job.data.subChanges.length > 0 && (
        <table className="table compact"><thead><tr><th>Managed element</th><th>Operation</th><th>Status</th><th>Rejection</th></tr></thead>
          <tbody>{job.data.subChanges.map((c, i) => <tr key={i}><td>{c.managedElementRef}</td><td>{c.operation}</td><td><StateBadge state={c.status} /></td><td>{c.rejectionReason ?? "—"}</td></tr>)}</tbody>
        </table>
      )}
    </>
  );
}

// ---------------------------------------------------------------- 04 closed-loop assurance

function Flow04() {
  const monitors = useSmo<Monitor[]>("/sa-smos/monitors");
  const [monitorId, setMonitor, monitor] = useSelection(monitors.data, (m) => m.monitorId);
  const order = useSmo<ServiceOrder>(monitor?.targetOrderId ? `/so-smos/orders/${monitor.targetOrderId}` : null);
  const actions = useSmo<RemedialAction[]>(monitorId ? "/sa-smos/remedial-actions" : null, { monitor_id: monitorId });
  const analytics = useSmo<AnalyticsReport[]>("/ran-analytics/reports");
  const mlmf = useSmo<MlmfReport[]>("/aimgf/mlmf/reports", { limit: 50 });
  const steps = flow04(monitor, order.data, actions.data ?? [], analytics.data?.length ?? 0, mlmf.data?.length ?? 0);
  const base = `/sa-smos/monitors/${monitorId}`;
  return (
    <>
      <Pick label="Assurance monitor" items={monitors.data} value={monitorId} onChange={setMonitor} id={(m) => m.monitorId}
        render={(m) => `${m.monitorId.slice(0, 8)} · ${m.targetOrderId ? "order" : m.targetCoordinationGroupId ? "model group" : "unscoped"}`}
        empty={<>No monitors. {go("/infrastructure#orders", "Submit an order")} then {go("/kpis#assurance", "register a monitor")}</>} />
      <Timeline steps={steps} actions={{
        order: go("/infrastructure#orders", "Service orders"),
        input: go("/kpis#analytics", "Analytics"),
        remediate: <div className="row gap wrap">
          {["CONFIG_CHANGE", "RECONNECT", "SCALE"].map((t) => (
            <ActionButton key={t} label={t} action={{ method: "POST", path: `${base}/remedial-actions`, query: { action_type: t }, success: `${t} dispatched` }} />
          ))}
          {go("/kpis#assurance", "Evaluate thresholds")}
        </div>,
        escalate: <ActionButton label="Escalate to operator" tone="danger" action={{ method: "POST", path: `${base}/escalate`, query: { reason: "raised from the lifecycle view" }, success: "Escalated" }} />,
      }} />
    </>
  );
}

// ---------------------------------------------------------------- 05 A1 EI → consumption

function Flow05() {
  const eiTypes = useSmo<EiType[]>("/a1-related/ei-types");
  const [eiId, setEi, ei] = useSelection(eiTypes.data, (t) => t.eiTypeId);
  const types = useSmo<DmeType[]>("/dme/dme-types");
  const dmeTypeId = ei?.eiSourceDmeTypeId;
  const offers = useSmo<DataOffer[]>(dmeTypeId ? "/dme/offers" : null, { dme_type_id: dmeTypeId });
  const jobs = useSmo<DataJob[]>(dmeTypeId ? "/dme/data-jobs" : null, { dme_type_id: dmeTypeId });
  const dmeType = types.data?.find((t) => t.dmeTypeId === dmeTypeId);
  const steps = flow05(ei, dmeType, offers.data ?? [], jobs.data ?? []);
  // DME only accepts a job whose delivery method an offer has committed to
  const committed = offers.data?.find((o) => o.committedMethod)?.committedMethod ?? null;
  return (
    <>
      <Pick label="EI type" items={eiTypes.data} value={eiId} onChange={setEi} id={(t) => t.eiTypeId}
        render={(t) => `${t.eiTypeId} (by ${t.registeredBy})`} empty={<>No EI types registered. {go("/data#a1-ei", "Register one")}</>} />
      <Timeline steps={steps} actions={{
        register: go("/data#a1-ei", "Register an EI type"),
        offer: dmeTypeId && <ActionButton label="Create offer (PUSH_HTTP, PULL_HTTP)" title="Simulates the producer's DataOffer" action={{
          method: "POST", path: "/dme/offers", success: "Offer created",
          json: { dmeTypeId, dataDeliveryMode: "CONTINUOUS", dataDeliveryMethods: ["PUSH_HTTP", "PULL_HTTP"], dataOfferTerminationNotificationUri: "http://producer.invalid/terminate" },
        }} />,
        job: dmeTypeId && (committed
          ? <ActionButton label={`Create consumer data job (${committed})`} tone="primary" action={{
              method: "POST", path: "/dme/data-jobs", success: "Data job created",
              json: { dmeTypeId, dataDeliveryMode: "CONTINUOUS", dataDeliveryMethod: committed, consumerId: "smo-gui" },
            }} />
          : <span className="muted small">DME needs an offer with a committed delivery method first.</span>),
        consume: go("/data#dme", "DME data jobs"),
      }} />
    </>
  );
}

// ---------------------------------------------------------------- 06 failure / deprecation / delete guard

function Flow06() {
  const packages = useSmo<Package[]>("/onboarding/packages");
  const [pkgId, setPkg, pkg] = useSelection(packages.data, (p) => p.packageId);
  const usage = useSmo<PackageUsage[]>(pkgId ? `/onboarding/packages/${pkgId}/usage` : null);
  const instances = useSmo<InstanceSummary[]>("/rapp-mgmt/instances");
  const steps = flow06(pkg, usage.data ?? [], instances.data?.filter((i) => i.packageId === pkgId).length ?? 0);
  const base = `/onboarding/packages/${pkgId}`;
  const active = (usage.data ?? []).filter((u) => u.active);
  return (
    <>
      <Pick label="Package" items={packages.data} value={pkgId} onChange={setPkg} id={(p) => p.packageId}
        render={(p) => `${p.name} ${p.version} (${p.state})`} empty={<>No packages yet. {go("/rapps#packages", "Onboard one")}</>} />
      <Timeline steps={steps} actions={{
        deprecate: <ActionButton label="Deprecate" action={{ method: "POST", path: `${base}/deprecate`, success: "Package DEPRECATED" }} />,
        guard: <div className="row gap wrap">
          {active.map((u) => <ActionButton key={u.registrationId} label={`Stop usage by ${u.consumerId.slice(0, 8)}`} title="Simulates the consumer releasing the package"
            action={{ method: "POST", path: `${base}/usage/${u.registrationId}/stop`, success: "Usage stopped" }} />)}
          {active.length === 0 && <ActionButton label="Register test usage" title="Simulates an instance holding the package, to exercise the guard"
            action={{ method: "POST", path: `${base}/usage/start`, query: { consumer_id: "smo-gui-test" }, success: "Usage registered — delete is now blocked" }} />}
        </div>,
        delete: <ActionButton label="Delete package" tone="danger" confirm="Delete this package?" action={{ method: "DELETE", path: base, success: "Delete requested" }} />,
      }} />
    </>
  );
}

// ---------------------------------------------------------------- 07 rApp fault & performance

function Flow07() {
  const instances = useSmo<InstanceSummary[]>("/rapp-mgmt/instances");
  const [id, setId] = useSelection(instances.data, (i) => i.instanceId);
  const instance = useSmo<Instance>(id ? `/rapp-mgmt/instances/${id}` : null);
  const perf = useSmo<PerfReport[]>(id ? `/rapp-mgmt/instances/${id}/performance` : null);
  const faults = useSmo<FaultReport[]>(id ? `/rapp-mgmt/instances/${id}/faults` : null);
  const steps = flow07(instance.data, perf.data ?? [], faults.data ?? []);
  const base = `/rapp-mgmt/instances/${id}`;
  return (
    <>
      <Pick label="rApp instance" items={instances.data} value={id} onChange={setId} id={(i) => i.instanceId}
        render={(i) => `${i.instanceId.slice(0, 8)} (${i.state})`} empty={<>No instances. {go("/flows#01", "Run flow 01 first")}</>} />
      <Timeline steps={steps} actions={{
        perf: <ActionButton label="Report performance" title="Simulates the rApp's own report" action={{ method: "POST", path: `${base}/performance`, json: { throughputMbps: 120, latencyMs: 8 }, success: "Performance recorded" }} />,
        minor: <ActionButton label="Report minor fault" action={{ method: "POST", path: `${base}/fault`, query: { severity: "minor", description: "degraded throughput" }, success: "Fault recorded" }} />,
        crash: <ActionButton label="Report critical fault" tone="danger" action={{ method: "POST", path: `${base}/fault`, query: { severity: "critical", description: "container crash" }, success: "Instance FAULTED" }} />,
        recover: instance.data?.state === "FAULTED"
          ? <ActionButton label="Recover" tone="primary" action={{ method: "POST", path: `${base}/recover`, success: "Re-entering DEPLOYING" }} />
          : <ActionButton label="Mark re-bootstrapped" tone="primary" action={{ method: "POST", path: `${base}/bootstrap-complete`, success: "Instance RUNNING" }} />,
      }} />
    </>
  );
}

// ---------------------------------------------------------------- 08 RAN Analytics

function Flow08() {
  const producers = useSmo<AnalyticsProducer[]>("/ran-analytics/producers");
  const subs = useSmo<AnalyticsSubscription[]>("/ran-analytics/subscriptions");
  const reports = useSmo<AnalyticsReport[]>("/ran-analytics/reports");
  const types = [...new Set([...(producers.data ?? []), ...(subs.data ?? []), ...(reports.data ?? [])].map((x) => x.analyticsType))].map((t) => ({ t }));
  const [type, setType] = useSelection(types, (x) => x.t);
  const producerIds = [...new Set((producers.data ?? []).filter((p) => p.analyticsType === type).map((p) => p.producerId))];
  const services = useQueries({
    queries: producerIds.map((pid) => ({
      queryKey: ["smo", `/sme/published-apis/v1/${pid}/service-apis`, {}],
      queryFn: () => smo<SmeService[]>(`/sme/published-apis/v1/${pid}/service-apis`),
    })),
  });
  const names = services.flatMap((s) => s.data ?? []).map((s) => s.serviceName);
  const steps = flow08(type, producers.data ?? [], subs.data ?? [], reports.data ?? [], names);
  return (
    <>
      <Pick label="Analytics type" items={types} value={type} onChange={setType} id={(x) => x.t} render={(x) => x.t}
        empty={<>No analytics producers, subscriptions or reports yet. {go("/kpis#analytics", "Register a producer")}</>} />
      <Timeline steps={steps} actions={{
        producer: go("/kpis#analytics", "Register a producer"),
        sme: go("/data#sme", "SME registry"),
        subscribe: <ActionButton label="Subscribe (poll)" action={{ method: "POST", path: "/ran-analytics/subscriptions", query: { analytics_type: type, requested_by: "smo-gui" }, success: "Subscribed" }} />,
        publish: go("/kpis#analytics", "Publish a report"),
      }} />
    </>
  );
}

// ---------------------------------------------------------------- 09 intents

function Flow09() {
  const intents = useSmo<Intent[]>("/intent-service/intents");
  const [intentId, setIntent, intent] = useSelection(intents.data, (i) => i.intentId);
  const handlers = useSmo<Rmih[]>("/intent-service/intent-handling-functions");
  const reports = useSmo<IntentReport[]>(intentId ? "/intent-service/intent-reports" : null, { intent_id: intentId });
  const steps = flow09(handlers.data ?? [], intent, reports.data ?? []);
  return (
    <>
      <Pick label="Intent" items={intents.data} value={intentId} onChange={setIntent} id={(i) => i.intentId}
        render={(i) => `${i.intentId.slice(0, 8)} · ${i.intentAdminState} · ${i.rmioId || "no RMIO"}`}
        empty={<>No intents. {go("/policy#intents", "Create one")}</>} />
      <Timeline steps={steps} actions={{
        rmih: go("/policy#handlers", "Register a handler"),
        create: go("/policy#intents", "Create an intent"),
        dispatch: go("/policy#handlers", "Register a handler"),
        report: intentId && <ActionButton label="Publish fulfilment report (as so-smos)" title="Simulates the RMIH's report"
          action={{ method: "POST", path: "/intent-service/intent-reports", json: { intentId, fulfilmentReport: { fulfilmentStatus: "FULFILLED", reportedBy: "so-smos" } }, success: "Report published" }} />,
        admin: intent?.rmioId === "smo-gui"
          ? <ActionButton label="Deactivate" action={{ method: "PATCH", path: `/intent-service/intents/${intentId}/admin-state`, json: { newState: "DEACTIVATED" }, success: "Intent DEACTIVATED" }} />
          : <span className="muted small">Only the creating RMIO ({intent?.rmioId || "—"}) may change the admin state.</span>,
      }} />
    </>
  );
}

// ---------------------------------------------------------------- 10 SO multi-step

function Flow10() {
  const orders = useSmo<ServiceOrder[]>("/so-smos/orders");
  const [orderId, setOrder, order] = useSelection(orders.data, (o) => o.orderId);
  const steps = flow10(order);
  const pending = order?.steps.some((s) => s.status === "PENDING");
  return (
    <>
      <Pick label="Service order" items={orders.data} value={orderId} onChange={setOrder} id={(o) => o.orderId}
        render={(o) => `${o.scope} (${o.orderId.slice(0, 8)})`} empty={<>No orders. {go("/infrastructure#orders", "Submit one")}</>}
        extra={pending && <ActionButton label="Cancel pending steps" action={{ method: "POST", path: `/so-smos/orders/${orderId}/cancel`, success: "Pending steps cancelled" }} />} />
      <Timeline steps={steps} actions={{ submit: go("/infrastructure#orders", "Submit an order") }} />
      {order && <p className="muted small">Order <Id value={order.orderId} /> · RMIH {order.rmihRegistration}. Completed steps are never rolled back; cancelling only touches PENDING steps.</p>}
    </>
  );
}
