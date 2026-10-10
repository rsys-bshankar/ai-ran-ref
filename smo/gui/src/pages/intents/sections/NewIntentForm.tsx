/** Section `intents.new`: the "New intent" form (role-gated by the caller with `Can`): a strict TS 28.312 Intent addressed to one handler the
 * operator chooses, with a live "handler supports it" check against that handler's declared capabilities (object type and every target name),
 * from the handler list. Intent Service re-checks at creation and rejects what the handler does not cover; a feasibility-check purpose is
 * accepted and reports what isn't feasible. An overlap check against other intents is a ⚠ gap (it would read every intent). */
import { useState } from "react";

import { ActionButton, Card, Field } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { DEFAULT_TARGETS, OBJECT_TYPES, PURPOSES, expectationOf, parseTargets } from "../data/forms";
import { handlerSupport, targetNames } from "../data/intent";
import { INTENTS, useHandlers } from "../data/queries";

/** The form. */
export function NewIntentForm() {
  const handlers = useHandlers();
  const [rmihId, setRmihId] = useState("");
  const [userLabel, setUserLabel] = useState("");
  const [objectType, setObjectType] = useState("RAN_SUBNETWORK");
  const [targets, setTargets] = useState(DEFAULT_TARGETS.RAN_SUBNETWORK);
  const [priority, setPriority] = useState("1");
  const [reportTo, setReportTo] = useState("");
  const [purpose, setPurpose] = useState(PURPOSES[0]);
  const [scope, setScope] = useState("");
  const parsedTargets = parseTargets(targets);
  const support = rmihId ? handlerSupport(handlers.data?.find((h) => h.rmihId === rmihId), objectType, targetNames(parsedTargets)) : null;
  return (
    <Card section="intents.new" title="New intent" sub="TS 28.312 expectation · checked against the handler before it is sent">
      <p className="muted small">A strict TS 28.312 Intent, addressed to one handler you choose below (consumer-side selection). Rejected at creation if that handler's declared capabilities, targets or scope don't cover it; a feasibility-check purpose is accepted and reports what isn't feasible.</p>
      <div className="form grid cols-3 tight">
        <Field label="Handler (RMIH)"><select value={rmihId} onChange={(e) => setRmihId(e.target.value)}><option value="">select…</option>{(handlers.data ?? []).map((h) => <option key={h.rmihId} value={h.rmihId}>{h.rmihId}</option>)}</select></Field>
        <Field label="User label"><input value={userLabel} onChange={(e) => setUserLabel(e.target.value)} placeholder="e.g. night-time energy saving" /></Field>
        <Field label="Expectation object type"><select value={objectType} onChange={(e) => { setObjectType(e.target.value); setTargets(DEFAULT_TARGETS[e.target.value] ?? "[]"); }}>{OBJECT_TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Report recipient" hint="Optional — receives this Intent's reports (intentReportControl)."><input value={reportTo} onChange={(e) => setReportTo(e.target.value)} placeholder="http://rapp:8000/intent-reports" /></Field>
        <Field label="Priority"><input type="number" min={1} value={priority} onChange={(e) => setPriority(e.target.value)} /></Field>
        <Field label="Handling scope"><select value={scope} onChange={(e) => setScope(e.target.value)}><option value="">any</option><option>RAN</option><option>CN</option></select></Field>
        <Field label="Purpose"><select value={purpose} onChange={(e) => setPurpose(e.target.value)}>{PURPOSES.map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Expectation targets (JSON array)" hint={parsedTargets ? undefined : <span className="text-bad">must be a JSON array</span>}><textarea rows={2} value={targets} onChange={(e) => setTargets(e.target.value)} spellCheck={false} /></Field>
      </div>
      <div className="row between wrap">
        <div className="row wrap" aria-live="polite">
          {support && (support.ok ? <Badge tone="ok">Handler supports target</Badge> : <Badge tone="warn">{support.problem}</Badge>)}
          <span className="gap-note">Overlap with other intents is not checked here.</span>
        </div>
        <ActionButton label="Create intent" tone="primary" disabled={!parsedTargets || parsedTargets.length === 0 || !rmihId || !userLabel} action={{
          method: "POST", path: INTENTS, success: "Intent created",
          json: {
            rmihId, userLabel,
            intentExpectations: [expectationOf(objectType, parsedTargets ?? [])],
            intentReportControl: [reportTo ? { observationPeriod: 60, reportRecipientAddress: reportTo } : { observationPeriod: 60 }],
            intentPriority: Number(priority) || 1, intentMgmtPurpose: purpose, intentHandlingScope: scope || null,
          },
        }} />
      </div>
    </Card>
  );
}
