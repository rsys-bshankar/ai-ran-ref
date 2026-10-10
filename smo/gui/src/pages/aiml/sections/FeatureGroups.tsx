/** Section `aiml.features` (Feature groups tab): AIMgF feature groups as a server-paged table with Delete, and the role-gated create form
 * (datalake source, optional DME sourcing). Feature groups carry datalake credentials, so reading them needs the operator role: a viewer sees a
 * note instead and no call is made. Credentials are never displayed. */
import { useState } from "react";

import type { FeatureGroup } from "../../../api/types";
import { useAuth } from "../../../auth/AuthContext";
import { ActionButton, Can, Card, Field, Id } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { FEATURE_GROUPS, useDmeTypes } from "../data/queries";

/** The tab. */
export function FeatureGroups() {
  const { can } = useAuth();
  const allowed = can("GET", FEATURE_GROUPS);
  return (
    <>
      <Can method="POST" path={FEATURE_GROUPS}><NewFeatureGroup /></Can>
      {!allowed && <Card section="aiml.features" title="Feature groups"><p className="muted">Feature groups carry datalake credentials, so they're visible to operators and admins only.</p></Card>}
      {allowed && <Card section="aiml.features" title="Feature groups" actions={<span className="muted small">Operator-only view: groups hold datalake credentials, which are never displayed here</span>}>
        <ServerTable<FeatureGroup> path={FEATURE_GROUPS} rowKey={(g) => g.featureGroupId} empty="No feature groups." columns={[
          { header: "Name", render: (g) => <strong>{g.featureGroupName}</strong> }, { header: "Features", render: (g) => <code className="small">{g.featureList}</code> },
          { header: "Source", render: (g) => `${g.datalakeSource} ${g.host}:${g.port}` }, { header: "Bucket / measurement", render: (g) => `${g.bucket} / ${g.measurement}` },
          { header: "DME data job", render: (g) => (g.dmeDataJobId ? <Id value={g.dmeDataJobId} /> : <span className="muted">none</span>) },
          { header: "", className: "actions", render: (g) => <ActionButton label="Delete" tone="danger" confirm={`Delete feature group ${g.featureGroupName}${g.dmeDataJobId ? " and its DME data job" : ""}?`} action={{ method: "DELETE", path: `${FEATURE_GROUPS}/${g.featureGroupName}`, success: "Feature group deleted" }} /> },
        ]} />
      </Card>}
    </>
  );
}

/** The create form; the name must be 3–63 word characters (AIMgF refuses anything else). */
function NewFeatureGroup() {
  const [f, setF] = useState({ featureGroupName: "", featureList: "", datalakeSource: "InfluxSource", host: "", port: "8086", bucket: "", token: "", dbOrg: "", measurement: "", sourceName: "" });
  const [enableDme, setEnableDme] = useState(false);
  const [dmeTypeId, setDmeTypeId] = useState("");
  const dmeTypes = useDmeTypes(enableDme);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const valid = /^\w{3,63}$/.test(f.featureGroupName);
  return (
    <Card section="aiml.newFeatureGroup" title="New feature group">
      <div className="form grid cols-3 tight">
        <Field label="Name" hint={f.featureGroupName && !valid ? <span className="text-bad">3-63 word characters</span> : "3-63 word characters, unique"}><input value={f.featureGroupName} onChange={set("featureGroupName")} /></Field>
        <Field label="Features" hint="Comma-separated"><input value={f.featureList} onChange={set("featureList")} placeholder="pdcpBytesDl,pdcpBytesUl" /></Field>
        <Field label="Datalake source"><input value={f.datalakeSource} onChange={set("datalakeSource")} /></Field>
        <Field label="Host"><input value={f.host} onChange={set("host")} /></Field>
        <Field label="Port"><input value={f.port} onChange={set("port")} /></Field>
        <Field label="Bucket"><input value={f.bucket} onChange={set("bucket")} /></Field>
        <Field label="Datalake token"><input type="password" autoComplete="off" value={f.token} onChange={set("token")} /></Field>
        <Field label="DB org"><input value={f.dbOrg} onChange={set("dbOrg")} /></Field>
        <Field label="Measurement"><input value={f.measurement} onChange={set("measurement")} /></Field>
        <label className="check"><input type="checkbox" checked={enableDme} onChange={(e) => setEnableDme(e.target.checked)} /> source via DME</label>
        {enableDme && <Field label="DME type" hint="AIMgF creates a DME data job of this type for the group">
          <select value={dmeTypeId} onChange={(e) => setDmeTypeId(e.target.value)}><option value="">Choose…</option>{dmeTypes.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</select>
        </Field>}
      </div>
      <ActionButton label="Create feature group" tone="primary" disabled={!valid || !f.featureList || !f.host || !f.bucket || !f.token || !f.dbOrg || !f.measurement || (enableDme && !dmeTypeId)}
        action={{ method: "POST", path: FEATURE_GROUPS, json: { ...f, sourceName: f.sourceName || null, enableDme, dmeTypeId: enableDme ? dmeTypeId : null }, success: "Feature group created" }} />
    </Card>
  );
}
