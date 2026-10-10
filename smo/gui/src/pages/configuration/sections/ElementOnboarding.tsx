/** Configuration · element onboarding (`configuration.onboarding`): new elements on their way from discovered to configured, paged by the
 * server (`GET /element-onboarding?status=&software_check=`): which template was selected, the software check against its baseline, and the
 * config job that applied it. "Select template" (`POST /{me}/select`, match again) and "Apply" (`POST /{me}/apply`, write the template as a
 * config job) are operator actions, role-gated `ActionButton`s (the BFF sets `requestedBy`). */
import { useState } from "react";
import { Link } from "react-router-dom";

import { ActionButton, Card, Id, StateBadge, type Column } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { ServerTable } from "../../../kit/ServerTable";
import { MiniSteps, type StepState } from "../../../kit/Timeline";
import { elementHref, type Onboarding } from "../../element/data/types";
import { ONBOARDING_PATH, onboardingPath } from "../data/queries";

const STATES = ["DISCOVERED", "NO_TEMPLATE", "TEMPLATE_SELECTED", "APPLYING", "ONBOARDED", "FAILED"];

/** discovered → selected → applying → onboarded, as four steps. */
function steps(status: string): StepState[] {
  const order = ["DISCOVERED", "TEMPLATE_SELECTED", "APPLYING", "ONBOARDED"];
  if (status === "FAILED") return ["done", "done", "fail", "todo"];
  if (status === "NO_TEMPLATE") return ["done", "warn", "todo", "todo"];
  const at = order.indexOf(status);
  return order.map((_, i) => (i < at || status === "ONBOARDED" ? "done" : i === at ? "now" : "todo"));
}

/** The tone of a software check. */
const CHECK_TONE: Record<string, "ok" | "bad" | "mute"> = { MATCH: "ok", MISMATCH: "bad", NOT_CHECKED: "mute" };

/** The table's columns. */
const COLUMNS: Column<Onboarding>[] = [
  { header: "Element", render: (o) => <Link to={elementHref(o.managedElementRef)}>{o.managedElementRef}</Link> },
  { header: "Template", render: (o) => (o.templateName ? <span className="mono small">{o.templateName}</span> : <span className="muted">—</span>) },
  { header: "Software check", render: (o) => (
    <span className="row wrap"><Badge tone={CHECK_TONE[o.softwareCheck ?? ""] ?? "mute"}>{o.softwareCheck ?? "NOT_CHECKED"}</Badge>
      {o.softwareVersion && <span className="small mono">{o.softwareVersion}{o.softwareBaseline ? ` / baseline ${o.softwareBaseline}` : ""}</span>}</span>
  ) },
  { header: "Progress", render: (o) => <MiniSteps states={steps(o.status)} label={`discovered, selected, applying, onboarded: ${o.status}`} /> },
  { header: "Status", render: (o) => <span className="row wrap"><StateBadge state={o.status} />{o.detail && <span className="small muted" title={o.detail}>{o.detail.slice(0, 60)}</span>}</span> },
  { header: "Config job", render: (o) => (o.configJobId ? <Link to={`/configuration?job=${o.configJobId}#jobs`}><Id value={o.configJobId} /></Link> : <span className="muted">—</span>) },
  { header: "", className: "actions", render: (o) => (
    <span className="row">
      {o.status !== "APPLYING" && o.status !== "ONBOARDED" && <ActionButton label="Select template" action={{ method: "POST", path: onboardingPath(o.managedElementRef, "select"), json: {}, success: "Template matched again" }} />}
      {o.status === "TEMPLATE_SELECTED" && <ActionButton label="Apply" tone="primary" confirm={`Write template ${o.templateName ?? ""} to ${o.managedElementRef} as a config job?`}
        action={{ method: "POST", path: onboardingPath(o.managedElementRef, "apply"), json: {}, success: "Template applied" }} />}
    </span>
  ) },
];

/** The card. */
export function ElementOnboarding() {
  const [status, setStatus] = useState("");
  const [check, setCheck] = useState("");
  const query = { ...(status ? { status } : {}), ...(check ? { software_check: check } : {}) };
  return (
    <Card section="configuration.onboarding" title="Element onboarding" sub="new elements: discovered → template selected → applied"
      actions={<>
        <label className="row small">Status<select aria-label="Onboarding status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All</option>{STATES.map((s) => <option key={s}>{s}</option>)}</select></label>
        <label className="row small">Software<select aria-label="Software check" value={check} onChange={(e) => setCheck(e.target.value)}>
          <option value="">All</option>{["MATCH", "MISMATCH", "NOT_CHECKED"].map((s) => <option key={s}>{s}</option>)}</select></label>
      </>}>
      <ServerTable<Onboarding> path={ONBOARDING_PATH} query={query} columns={COLUMNS} rowKey={(o) => o.managedElementRef}
        empty="No element is being onboarded through a template." />
    </Card>
  );
}
