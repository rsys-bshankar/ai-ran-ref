/** The Packages tab's table (SCALE.md P1): application packages paged by Onboarding (`GET /onboarding/packages?state=`), with the package
 * pipeline counts from the summary on top (each one filters the table), Deploy on AVAILABLE packages and the lifecycle calls a state allows.
 * A row opens the package drawer. Section id `rapps.packages`. */
import { useCallback, useState } from "react";

import type { Package } from "../../../api/types";
import { ActionButton, Can, Card, Id, StateBadge } from "../../../components/ui";
import { formatCount } from "../../../kit/Kpi";
import { ServerTable } from "../../../kit/ServerTable";
import { count } from "../../../data/summary";
import { packageActions } from "../../../lib/domain";
import { INSTANCES_PATH, PACKAGE_STATES, PACKAGES_PATH, packageBase, useRappsSummary } from "../data/queries";
import { CreateInstance } from "./CreateInstance";
import { PackageDrawer } from "./PackageDrawer";

/** The pipeline stages the summary counts (gui-bff/app/summary.py PACKAGES). */
const PIPELINE = ["ONBOARDING", "AVAILABLE", "PRIMED", "DEPRECATED", "FAILED"] as const;

/** The table with its pipeline chips and state filter. */
export function PackagesTable() {
  const [state, setState] = useState("");
  const [deployFrom, setDeployFrom] = useState<Package | null>(null);
  const [detail, setDetail] = useState<Package | null>(null);
  const summary = useRappsSummary();
  // keep the open drawer on the row as the table refreshes it (its state changes after an action)
  const onRows = useCallback((rows: Package[]) => setDetail((d) => (d ? rows.find((r) => r.packageId === d.packageId) ?? d : d)), []);
  return (
    <Card section="rapps.packages" title="Application packages" sub="package pipeline · counts from the summary, click one to filter"
      actions={
        <select value={state} onChange={(e) => setState(e.target.value)} aria-label="Filter by state">
          <option value="">All states</option>
          {PACKAGE_STATES.map((s) => <option key={s}>{s}</option>)}
        </select>}>
      <div className="row gap wrap" role="group" aria-label="Package pipeline">
        {PIPELINE.map((s) => {
          const n = count(summary.data, `packages.${s}`);
          return (
            <button key={s} type="button" className={`chip${state === s ? " on" : ""}`} aria-pressed={state === s} onClick={() => setState(state === s ? "" : s)}>
              {s} <b className="num">{formatCount(n)}</b>
            </button>
          );
        })}
        <span className="muted small">{formatCount(count(summary.data, "packages.total"))} in all</span>
      </div>
      <ServerTable<Package> path={PACKAGES_PATH} query={{ state: state || undefined }} rowKey={(p) => p.packageId}
        empty={state ? `No package is ${state}.` : "No packages onboarded yet."} onRowClick={setDetail} selectedKey={detail?.packageId ?? null} onRows={onRows}
        columns={[
          { header: "Package", render: (p) => <><strong>{p.name}</strong> <span className="muted">{p.version}</span><div className="muted small">{p.vendor ?? ""} {p.applicationType}</div></> },
          { header: "ID", render: (p) => <Id value={p.packageId} /> },
          { header: "State", render: (p) => <StateBadge state={p.state} /> },
          { header: "Signature", render: (p) => (p.signatureVerified ? "verified" : <span className="muted">unverified</span>) },
          { header: "NF descriptor", render: (p) => <Id value={p.nfDeploymentDescriptorId} /> },
          {
            header: "", className: "actions", render: (p) => (
              <div className="row gap end">
                {p.state === "AVAILABLE" && <Can method="POST" path={INSTANCES_PATH}><button type="button" className="btn primary" onClick={(e) => { e.stopPropagation(); setDeployFrom(p); }}>Deploy</button></Can>}
                {packageActions(p.state).map((a) => (
                  <ActionButton key={a.action} label={a.label} tone={a.action === "delete" ? "danger" : "default"}
                    confirm={a.action === "delete" ? `Delete package ${p.name} ${p.version}?` : undefined}
                    action={a.action === "delete"
                      ? { method: "DELETE", path: packageBase(p.packageId), success: "Package delete requested" }
                      : { method: "POST", path: `${packageBase(p.packageId)}/${a.action}`, success: `${a.label}: done` }} />
                ))}
              </div>
            ),
          },
        ]} />
      {deployFrom && <CreateInstance pkg={deployFrom} onClose={() => setDeployFrom(null)} />}
      {detail && <PackageDrawer pkg={detail} onClose={() => setDetail(null)} />}
    </Card>
  );
}
