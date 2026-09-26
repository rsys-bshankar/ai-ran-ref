import { useState } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import type {
  ConfigJob, ConfigJobSummary, DeploymentManager, InventorySubscription, LcmOperation, Model, NfDeployment, NfDescriptor, NfResource, O1Endpoint, OCloudResource, ResourcePool,
  ResourceType, ServiceOrder, SwmJob, Topology,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ActionButton, Can, Card, DataTable, Drawer, ErrorBox, Field, Id, Json, KeyValue, Modal, PageHeader, StateBadge, Tabs, useHashTab } from "../components/ui";
import { formatTime, parseJsonObject, splitList } from "../lib/domain";

const TABS = ["nfo", "ocloud", "topology", "o1", "orders"] as const;

export function Infrastructure() {
  const [tab, setTab] = useHashTab(TABS, "nfo");
  return (
    <>
      <PageHeader title="Infrastructure" subtitle="Workloads (NFO / O2dms), O-Cloud inventory (FOCOM / O2ims), O1 management and service orders" />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "nfo", label: "NF deployments" }, { id: "ocloud", label: "O-Cloud inventory" }, { id: "topology", label: "Topology" },
        { id: "o1", label: "O1 endpoints & jobs" }, { id: "orders", label: "Service orders" }
      ]} />
      {tab === "nfo" && <Deployments />}
      {tab === "ocloud" && <OCloud />}
      {tab === "topology" && <TopologyView />}
      {tab === "o1" && <O1 />}
      {tab === "orders" && <Orders />}
    </>
  );
}

// ---------------------------------------------------------------- NFO

function Deployments() {
  const [state, setState] = useState("");
  const deployments = useSmo<NfDeployment[]>("/nfo/deployments", { state });
  const [selected, setSelected] = useState<NfDeployment | null>(null);
  return (
    <>
      <Card title="NF deployments" actions={<select value={state} onChange={(e) => setState(e.target.value)} aria-label="State"><option value="">All states</option>{["INITIAL", "INSTANTIATING", "RUNNING", "UPDATING", "TERMINATING", "ABNORMAL", "DELETING"].map((s) => <option key={s}>{s}</option>)}</select>}>
        <p className="muted small">Deployments are created by rApp Management / SO SMOS; the GUI heals, scales or terminates them.</p>
        <DataTable rows={deployments.data} loading={deployments.isLoading} error={deployments.error} rowKey={(d) => d.nfDeploymentId} empty="No NF deployments."
          onRowClick={setSelected} selectedKey={selected?.nfDeploymentId} columns={[
            { header: "Name", render: (d) => <strong>{d.name}</strong> },
            { header: "Deployment", render: (d) => <Id value={d.nfDeploymentId} /> },
            { header: "State", render: (d) => <StateBadge state={d.state} /> },
            { header: "Cluster", render: (d) => d.clusterId },
            { header: "Resource type", render: (d) => d.requiredResourceTypeId ?? <span className="muted">—</span> },
            { header: "", className: "actions", render: (d) => <DeploymentActions d={d} /> },
          ]} />
      </Card>
      {selected && <DeploymentDrawer d={selected} onClose={() => setSelected(null)} />}
      <Descriptors />
    </>
  );
}

function Descriptors() {
  const descriptors = useSmo<NfDescriptor[]>("/nfo/descriptors");
  return (
    <Card title="NF deployment descriptors" actions={<span className="muted small">Created by Onboarding's validation pipeline from each package's TOSCA definitions</span>}>
      <DataTable rows={descriptors.data} loading={descriptors.isLoading} error={descriptors.error} rowKey={(d) => d.nfDeploymentDescriptorId} empty="No descriptors." columns={[
        { header: "Descriptor", render: (d) => <Id value={d.nfDeploymentDescriptorId} /> }, { header: "Name", render: (d) => <code>{d.name}</code> },
        { header: "Package", render: (d) => <Id value={d.packageId} /> }, { header: "Resource type", render: (d) => d.requiredResourceTypeId ?? "—" },
      ]} />
    </Card>
  );
}

function DeploymentActions({ d }: { d: NfDeployment }) {
  const base = `/nfo/deployments/${d.nfDeploymentId}`;
  return (
    <div className="row gap end">
      {(d.state === "RUNNING" || d.state === "ABNORMAL") && <ActionButton label="Heal" action={{ method: "POST", path: `${base}/heal`, success: "Heal requested" }} />}
      {d.state === "RUNNING" && <ActionButton label="Scale" action={{ method: "POST", path: `${base}/scale`, success: "Scale requested" }} />}
      {d.state !== "DELETING" && <ActionButton label="Terminate" tone="danger" confirm={`Terminate deployment ${d.name}?`} action={{ method: "DELETE", path: base, success: "Terminate requested" }} />}
    </div>
  );
}

function DeploymentDrawer({ d, onClose }: { d: NfDeployment; onClose: () => void }) {
  const resources = useSmo<NfResource[]>(`/nfo/deployments/${d.nfDeploymentId}/resources`);
  const ops = useSmo<LcmOperation[]>(`/nfo/deployments/${d.nfDeploymentId}/operations`);
  return (
    <Drawer title={d.name} onClose={onClose}>
      <div className="row between"><StateBadge state={d.state} /><DeploymentActions d={d} /></div>
      <KeyValue items={[
        ["Deployment ID", <code>{d.nfDeploymentId}</code>], ["Descriptor", <code>{d.nfDeploymentDescriptorId}</code>],
        ["Cluster (placement)", d.clusterId], ["Workload ref", d.workloadRef], ["Required resource type", d.requiredResourceTypeId],
      ]} />
      <h3>LCM operations</h3>
      <DataTable rows={ops.data} error={ops.error} rowKey={(o) => o.operationId} empty="No operations recorded." columns={[
        { header: "Operation", render: (o) => <code>{o.operationType}</code> }, { header: "Status", render: (o) => <StateBadge state={o.status} /> },
        { header: "ID", render: (o) => <Id value={o.operationId} /> },
      ]} />
      <h3>O-Cloud resources</h3>
      <DataTable rows={resources.data} error={resources.error} rowKey={(r) => r.resourceLinkId} empty="No linked resources." columns={[
        { header: "Resource", render: (r) => <code>{r.resourceRef}</code> }, { header: "Type", render: (r) => r.vresourceType },
      ]} />
    </Drawer>
  );
}

// ---------------------------------------------------------------- FOCOM

function OCloud() {
  const pools = useSmo<ResourcePool[]>("/focom/resource-pools");
  const types = useSmo<ResourceType[]>("/focom/resource-types");
  const dms = useSmo<DeploymentManager[]>("/focom/deployment-managers");
  const [pool, setPool] = useState<string | null>(null);
  const activePool = pool ?? pools.data?.[0]?.resourcePoolId ?? null;
  const resources = useSmo<OCloudResource[]>(activePool ? `/focom/resource-pools/${activePool}/resources` : null);
  const [spec, setSpec] = useState('{"resourceTypeId": "", "description": "GPU node"}');
  const parsed = parseJsonObject(spec);
  return (
    <>
      <div className="grid cols-2">
        <Card title="Resource pools">
          <DataTable rows={pools.data} loading={pools.isLoading} error={pools.error} rowKey={(p) => p.resourcePoolId} onRowClick={(p) => setPool(p.resourcePoolId)} selectedKey={activePool} columns={[
            { header: "Pool", render: (p) => <><strong>{p.name}</strong><div className="muted small">{p.description}</div></> },
            { header: "O-Cloud", render: (p) => p.oCloudId },
          ]} />
        </Card>
        <Card title="Deployment managers">
          <DataTable rows={dms.data} loading={dms.isLoading} error={dms.error} rowKey={(d) => d.deploymentManagerId} columns={[
            { header: "Name", render: (d) => <strong>{d.name}</strong> }, { header: "Service URI", render: (d) => <code className="small">{d.serviceUri}</code> },
          ]} />
        </Card>
      </div>
      <Card title={<>Resources in pool {activePool && <code>{activePool}</code>}</>}>
        <DataTable rows={resources.data} loading={resources.isLoading} error={resources.error} rowKey={(r) => r.resourceId} empty="No resources in this pool." columns={[
          { header: "Resource", render: (r) => <Id value={r.resourceId} /> },
          { header: "Type", render: (r) => r.resourceTypeId }, { header: "Description", render: (r) => r.description ?? "—" },
          { header: "Parent", render: (r) => <Id value={r.parentId} /> },
          { header: "", className: "actions", render: (r) => <ActionButton label="Deprovision" tone="danger" confirm="Deprovision this resource?" action={{ method: "DELETE", path: `/focom/resources/${r.resourceId}`, success: "Resource deprovisioned" }} /> },
        ]} />
        <Can method="POST" path="/focom/resources/provision">
          <details className="admin-tools">
            <summary>Admin: provision a resource</summary>
            <Field label="Spec (JSON)" hint={parsed.ok ? "An unknown resourceTypeId is auto-registered." : <span className="text-bad">{parsed.error}</span>}><textarea rows={2} value={spec} onChange={(e) => setSpec(e.target.value)} spellCheck={false} /></Field>
            <ActionButton label="Provision" disabled={!parsed.ok} action={{ method: "POST", path: "/focom/resources/provision", json: parsed.ok ? parsed.value : {}, success: "Resource provisioned" }} />
          </details>
        </Can>
      </Card>
      <InventorySubscriptions />
      <Card title="Resource types">
        <DataTable rows={types.data} loading={types.isLoading} error={types.error} rowKey={(t) => t.resourceTypeId} columns={[
          { header: "Type", render: (t) => <><strong>{t.name}</strong> <code className="small">{t.resourceTypeId}</code></> },
          { header: "Vendor / model", render: (t) => [t.vendor, t.model, t.version].filter(Boolean).join(" ") || "—" },
          { header: "Kind / class", render: (t) => [t.resourceKind, t.resourceClass].filter(Boolean).join(" / ") || "—" },
        ]} />
      </Card>
    </>
  );
}

function InventorySubscriptions() {
  const subs = useSmo<InventorySubscription[]>("/focom/inventory/subscriptions");
  const [callback, setCallback] = useState("");
  const [typeId, setTypeId] = useState("");
  return (
    <Card title="Inventory-change subscriptions" actions={<span className="muted small">Notified on provision / deprovision (CREATE / DELETE)</span>}>
      <Can method="POST" path="/focom/inventory/subscriptions">
        <div className="form inline">
          <Field label="Callback"><input value={callback} onChange={(e) => setCallback(e.target.value)} placeholder="http://consumer:8000/inventory-events" /></Field>
          <Field label="Resource type filter"><input value={typeId} onChange={(e) => setTypeId(e.target.value)} placeholder="any" /></Field>
          <ActionButton label="Subscribe" disabled={!callback} action={{ method: "POST", path: "/focom/inventory/subscriptions", json: { callback, resourceTypeId: typeId || null, consumerSubscriptionId: "smo-gui" }, success: "Subscribed to inventory changes" }} />
        </div>
      </Can>
      <DataTable rows={subs.data} rowKey={(s) => s.subscriptionId} empty="No inventory subscriptions." columns={[
        { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> }, { header: "Callback", render: (s) => <code className="small">{s.callback}</code> },
        { header: "Resource type", render: (s) => s.resourceTypeId ?? "any" },
        { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `/focom/inventory/subscriptions/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
      ]} />
    </Card>
  );
}

function TopologyView() {
  const topo = useSmo<Topology>("/focom/topology");
  const entities = (topo.data?.entities ?? []).flatMap((group) => Object.entries(group).flatMap(([type, list]) => list.map((e) => ({ type: type.split(":").pop()!, ...e }))));
  const relationships = (topo.data?.relationships ?? []).flatMap((group) => Object.entries(group).flatMap(([type, list]) => list.map((r) => ({ type: type.split(":").pop()!, ...r }))));
  const tail = (urn: string) => urn.split(":").slice(-2).join(":");
  return (
    <>
      <ErrorBox error={topo.error} />
      <Card title="Entities" actions={<span className="muted small">TEIV export (pull), from FOCOM's own inventory rows</span>}>
        <DataTable rows={entities} loading={topo.isLoading} rowKey={(e) => e.id} empty="Empty topology." columns={[
          { header: "Type", render: (e) => <StateBadge state={e.type} /> },
          { header: "ID", render: (e) => <code className="small">{tail(e.id)}</code> },
          { header: "Attributes", render: (e) => <span className="small">{Object.entries(e.attributes).filter(([, v]) => v != null).map(([k, v]) => `${k}=${v}`).join(" · ")}</span> },
        ]} />
      </Card>
      <Card title="Relationships">
        <DataTable rows={relationships} loading={topo.isLoading} rowKey={(r) => r.id} empty="No relationships." columns={[
          { header: "Relationship", render: (r) => <code className="small">{r.type}</code> },
          { header: "A side", render: (r) => <code className="small">{tail(r.aSide)}</code> },
          { header: "B side", render: (r) => <code className="small">{tail(r.bSide)}</code> },
        ]} />
      </Card>
    </>
  );
}

// ---------------------------------------------------------------- O1

function O1() {
  const endpoints = useSmo<O1Endpoint[]>("/ran-nf-oam/o1-adaptor-endpoints");
  const jobs = useSmo<ConfigJobSummary[]>("/ran-nf-oam/config-jobs");
  const swm = useSmo<SwmJob[]>("/ran-nf-oam/software-management-jobs");
  const [registering, setRegistering] = useState(false);
  const [writing, setWriting] = useState(false);
  const [job, setJob] = useState<string | null>(null);
  const [swmMe, setSwmMe] = useState("");
  return (
    <>
      <Card title="O1 adaptor endpoints (managed elements)" actions={<>
        <ActionButton label="Run health discovery" action={{ method: "POST", path: "/ran-nf-oam/o1-adaptor-endpoints/discover", success: "Endpoint health re-aged" }} />
        <Can method="POST" path="/ran-nf-oam/o1-adaptor-endpoints"><button className="btn primary" onClick={() => setRegistering(true)}>Register endpoint</button></Can>
      </>}>
        <DataTable rows={endpoints.data} loading={endpoints.isLoading} error={endpoints.error} rowKey={(e) => e.endpointId} empty="No managed elements registered." columns={[
          { header: "Managed element", render: (e) => <strong>{e.managedElementRef}</strong> },
          { header: "Adaptor", render: (e) => <code className="small">{e.adaptorUri}</code> },
          { header: "Protocols", render: (e) => e.protocolSupport.join(", ") },
          { header: "Health", render: (e) => <StateBadge state={e.healthStatus} /> },
          { header: "Last heartbeat", render: (e) => formatTime(e.lastHeartbeatAt) },
          { header: "", className: "actions", render: (e) => <ActionButton label="Heartbeat" title="Simulates the ME's O1 adaptor heartbeat (DISCOVERED/DEGRADED → ACTIVE)"
            action={{ method: "POST", path: `/ran-nf-oam/o1-adaptor-endpoints/${e.endpointId}/heartbeat`, success: `${e.managedElementRef} heartbeat` }} /> },
        ]} />
      </Card>
      <Card title="CM write jobs" actions={<Can method="POST" path="/ran-nf-oam/config-jobs"><button className="btn primary" onClick={() => setWriting(true)}>New config write</button></Can>}>
        <p className="muted small">Schema-checked writes, dispatched per managed element as NETCONF &lt;edit-config&gt; (call-flow 03).</p>
        <DataTable rows={jobs.data} loading={jobs.isLoading} error={jobs.error} rowKey={(j) => j.jobId} empty="No config jobs." onRowClick={(j) => setJob(j.jobId)} columns={[
          { header: "Job", render: (j) => <Id value={j.jobId} /> }, { header: "Requested by", render: (j) => j.requestedBy },
          { header: "Scope", render: (j) => j.scope }, { header: "Status", render: (j) => <StateBadge state={j.status} /> },
        ]} />
      </Card>
      <Card title="Software management jobs" actions={<Can method="POST" path="/ran-nf-oam/software-management-jobs">
        <select value={swmMe} onChange={(e) => setSwmMe(e.target.value)} aria-label="Managed element"><option value="">Managed element…</option>{endpoints.data?.map((e) => <option key={e.endpointId}>{e.managedElementRef}</option>)}</select>
        <ActionButton label="Start software update" disabled={!swmMe} action={{ method: "POST", path: "/ran-nf-oam/software-management-jobs", query: { managed_element_ref: swmMe }, success: "Software job started (DOWNLOAD)" }} />
      </Can>}>
        <DataTable rows={swm.data} loading={swm.isLoading} error={swm.error} rowKey={(j) => j.jobId} empty="No software jobs." columns={[
          { header: "Job", render: (j) => <Id value={j.jobId} /> }, { header: "Managed element", render: (j) => j.managedElementRef },
          { header: "Phase", render: (j) => <code>{j.phase}</code> }, { header: "Status", render: (j) => <StateBadge state={j.status} /> },
          { header: "", className: "actions", render: (j) => !["COMPLETED", "FAILED"].includes(j.status) && (
            <div className="row gap end">
              <ActionButton label={`${j.phase} ok`} action={{ method: "POST", path: `/ran-nf-oam/software-management-jobs/${j.jobId}/advance`, query: { succeeded: true }, success: "Phase advanced" }} />
              <ActionButton label="Failed" action={{ method: "POST", path: `/ran-nf-oam/software-management-jobs/${j.jobId}/advance`, query: { succeeded: false }, success: "Job failed" }} />
            </div>
          ) },
        ]} />
      </Card>
      {registering && <RegisterEndpoint onClose={() => setRegistering(false)} />}
      {writing && <ConfigWrite endpoints={endpoints.data ?? []} onClose={() => setWriting(false)} />}
      {job && <ConfigJobDetail id={job} onClose={() => setJob(null)} />}
    </>
  );
}

function RegisterEndpoint({ onClose }: { onClose: () => void }) {
  const [f, setF] = useState({ managedElementRef: "", adaptorUri: "http://mock-o1-adaptor:8000/edit-config", protocolSupport: "NETCONF", o1Protocol: "NETCONF", entityType: "O-DU", vendorName: "", managedFunctionRef: "" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const action = useSmoAction();
  return (
    <Modal title="Register O1 adaptor endpoint" onClose={onClose}>
      <form className="form grid cols-2 tight" onSubmit={(e) => {
        e.preventDefault();
        action.mutate({ method: "POST", path: "/ran-nf-oam/o1-adaptor-endpoints", success: "Endpoint registered",
          json: { ...f, protocolSupport: splitList(f.protocolSupport), vendorName: f.vendorName || null, managedFunctionRef: f.managedFunctionRef || null } }, { onSuccess: onClose });
      }}>
        <Field label="Managed element ref"><input value={f.managedElementRef} onChange={set("managedElementRef")} required placeholder="ME-1" /></Field>
        <Field label="Entity type"><select value={f.entityType} onChange={set("entityType")}>{["O-DU", "O-CU-CP", "O-CU-UP", "O-RU", "Near-RT RIC"].map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Adaptor URI"><input value={f.adaptorUri} onChange={set("adaptorUri")} required /></Field>
        <Field label="O1 protocol" hint="RESTCONF MEs are registered but CM writes to them are rejected (not implemented)"><select value={f.o1Protocol} onChange={set("o1Protocol")}><option>NETCONF</option><option>RESTCONF</option></select></Field>
        <Field label="Protocols supported"><input value={f.protocolSupport} onChange={set("protocolSupport")} /></Field>
        <Field label="Vendor"><input value={f.vendorName} onChange={set("vendorName")} /></Field>
        <Field label="Managed function ref"><input value={f.managedFunctionRef} onChange={set("managedFunctionRef")} /></Field>
        <div className="row gap end span-2"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={action.isPending}>Register</button></div>
      </form>
    </Modal>
  );
}

function ConfigWrite({ endpoints, onClose }: { endpoints: O1Endpoint[]; onClose: () => void }) {
  const { role } = useAuth();
  const [scope, setScope] = useState("cell");
  const [mes, setMes] = useState<string[]>([]);
  const [operation, setOperation] = useState("merge");
  const [attrs, setAttrs] = useState('{"administrativeState": "UNLOCKED"}');
  const parsed = parseJsonObject(attrs);
  const action = useSmoAction();
  return (
    <Modal title="New CM write" onClose={onClose}>
      <form className="form" onSubmit={(e) => {
        e.preventDefault();
        if (!parsed.ok) return;
        // requestedBy and msacRole are set by the BFF from your GUI identity
        action.mutate({ method: "POST", path: "/ran-nf-oam/config-jobs", success: "Config job submitted",
          json: { scope, changes: mes.map((m) => ({ managedElementRef: m, attributeChanges: parsed.value, operation })) } }, { onSuccess: onClose });
      }}>
        <p className="muted small">One job, decomposed into one NETCONF &lt;edit-config&gt; per managed element; mixed results aggregate to PARTIAL_SUCCESS (call flow 03).</p>
        <div className="grid cols-3 tight">
          <Field label="Managed elements" hint="Ctrl/Cmd-click for several">
            <select multiple size={Math.min(5, Math.max(2, endpoints.length))} value={mes} onChange={(e) => setMes([...e.target.selectedOptions].map((o) => o.value))} required>
              {endpoints.map((e) => <option key={e.endpointId} value={e.managedElementRef}>{e.managedElementRef} ({e.healthStatus})</option>)}
            </select>
          </Field>
          <Field label="Scope" hint={scope === "entire-RAN" && role !== "admin" ? <span className="text-bad">entire-RAN needs an MSAC tier: admins only</span> : undefined}>
            <select value={scope} onChange={(e) => setScope(e.target.value)}><option>cell</option><option>site</option><option>cluster</option><option>entire-RAN</option></select>
          </Field>
          <Field label="Operation"><select value={operation} onChange={(e) => setOperation(e.target.value)}>{["merge", "replace", "create", "delete", "remove"].map((o) => <option key={o}>{o}</option>)}</select></Field>
        </div>
        <Field label="Attribute changes (JSON, applied to each)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><textarea rows={4} value={attrs} onChange={(e) => setAttrs(e.target.value)} spellCheck={false} /></Field>
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!parsed.ok || mes.length === 0 || action.isPending}>Submit</button></div>
      </form>
    </Modal>
  );
}

function ConfigJobDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const job = useSmo<ConfigJob>(`/ran-nf-oam/config-jobs/${id}`);
  return (
    <Drawer title={<>Config job <Id value={id} /></>} onClose={onClose}>
      <ErrorBox error={job.error} />
      {job.data && <>
        <StateBadge state={job.data.status} />
        <DataTable rows={job.data.subChanges} rowKey={(s) => `${s.managedElementRef}/${s.operation}`} empty="No sub-changes." columns={[
          { header: "Managed element", render: (s) => s.managedElementRef }, { header: "Operation", render: (s) => s.operation },
          { header: "Status", render: (s) => <StateBadge state={s.status} /> }, { header: "Rejection", render: (s) => s.rejectionReason ?? "—" },
        ]} />
      </>}
    </Drawer>
  );
}

// ---------------------------------------------------------------- SO SMOS

const STEP_TEMPLATES: Record<string, Record<string, unknown>> = {
  POLICY: { stepType: "POLICY", targetModule: "A1_RELATED", policyTypeId: "ORAN_QoSandTSP_6.0.1", nearRtRicId: "mock-near-rt-ric-001", policyObject: { scope: { cellId: "cell-1" } } },
  CONFIG: { stepType: "CONFIG", targetModule: "RAN_NF_OAM", scope: "cell", changes: [] },
  DEPLOY: { stepType: "DEPLOY", targetModule: "NFO", nfDeploymentDescriptorId: "<descriptor uuid>", name: "so-deploy-1" },
  INFRA: { stepType: "INFRA", targetModule: "FOCOM", spec: { description: "GPU node" } },
  TRAINING: { stepType: "TRAINING", targetModule: "AI_ML_WORKFLOW", modelId: "<model uuid>" },
};

function Orders() {
  const orders = useSmo<ServiceOrder[]>("/so-smos/orders");
  const [selected, setSelected] = useState<ServiceOrder | null>(null);
  return (
    <>
      <Can method="POST" path="/so-smos/orders"><SubmitOrder /></Can>
      <Card title="Service orders" actions={<span className="muted small">Sequential, fail-fast: steps after a FAILED one stay PENDING</span>}>
        <DataTable rows={orders.data} loading={orders.isLoading} error={orders.error} rowKey={(o) => o.orderId} empty="No service orders." onRowClick={setSelected} columns={[
          { header: "Order", render: (o) => <Id value={o.orderId} /> }, { header: "Scope", render: (o) => o.scope },
          { header: "Steps", render: (o) => <div className="row gap wrap">{o.steps.map((s, i) => <span key={i} className="small">{s.stepType} <StateBadge state={s.status} /></span>)}</div> },
          { header: "", className: "actions", render: (o) => o.steps.some((s) => s.status === "PENDING") && <ActionButton label="Cancel pending" action={{ method: "POST", path: `/so-smos/orders/${o.orderId}/cancel`, success: "Pending steps cancelled" }} /> },
        ]} />
      </Card>
      {selected && <Drawer title={<>Order <Id value={selected.orderId} /></>} onClose={() => setSelected(null)}><KeyValue items={[["Scope", selected.scope], ["RMIH", selected.rmihRegistration]]} /><h3>Steps</h3><Json value={selected.steps} /></Drawer>}
    </>
  );
}

function SubmitOrder() {
  const [scope, setScope] = useState("");
  const [steps, setSteps] = useState(JSON.stringify([STEP_TEMPLATES.POLICY], null, 2));
  const models = useSmo<Model[]>("/mlmr/models");
  const descriptors = useSmo<NfDescriptor[]>("/nfo/descriptors");
  const deployments = useSmo<NfDeployment[]>("/nfo/deployments");
  let parsed: unknown[] | null = null;
  try { const v = JSON.parse(steps); parsed = Array.isArray(v) ? v : null; } catch { parsed = null; }
  // TRAINING/DEPLOY need real ids: prefill the newest model, and the newest
  // descriptor NFO will still accept (one deployment per descriptor, ever;
  // deployment names are unique too), instead of placeholders the operator
  // has to go and look up.
  const fill = (k: string) => {
    const t = { ...STEP_TEMPLATES[k] };
    const model = models.data?.at(-1);
    const used = new Set((deployments.data ?? []).map((d) => d.nfDeploymentDescriptorId));
    const descriptor = descriptors.data?.filter((d) => !used.has(d.nfDeploymentDescriptorId)).at(-1);
    if (k === "TRAINING" && model) t.modelId = model.modelId;
    if (k === "DEPLOY") {
      if (descriptor) t.nfDeploymentDescriptorId = descriptor.nfDeploymentDescriptorId;
      t.name = `so-deploy-${Date.now().toString(36)}`;
    }
    return t;
  };
  const add = (k: string) => setSteps(JSON.stringify([...(parsed ?? []), fill(k)], null, 2));
  return (
    <Card title="Submit service order">
      <div className="row gap wrap"><span className="muted small">Add step:</span>{Object.keys(STEP_TEMPLATES).map((k) => <button key={k} className="btn small" onClick={() => add(k)}>{k}</button>)}</div>
      <div className="form">
        <Field label="Scope"><input value={scope} onChange={(e) => setScope(e.target.value)} placeholder="cell-cluster-7 rollout" /></Field>
        <Field label="Steps (JSON array)" hint={parsed ? "TRAINING / DEPLOY steps are prefilled with the newest model and a not-yet-deployed NF descriptor" : <span className="text-bad">must be a JSON array</span>}><textarea rows={8} value={steps} onChange={(e) => setSteps(e.target.value)} spellCheck={false} /></Field>
      </div>
      <ActionButton label="Submit order" tone="primary" disabled={!parsed || !scope} action={{ method: "POST", path: "/so-smos/orders", json: { scope, steps: parsed ?? [] }, success: "Order executed" }} />
    </Card>
  );
}
