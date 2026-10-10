/** The dialogs of one rApp row of `safeguards.limits`: stop it (with a reason), set its limits, hold its changes for approval. Each validates with
 * the shared form helpers of `lib/domain.ts` before it calls, and closes on success. */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { ApprovalPolicy, InstanceSafeguards } from "../../../api/types";
import { Field, Modal } from "../../../components/ui";
import { approvalPolicyForm, approvalPolicyPayload, limitsForm, limitsPayload } from "../../../lib/domain";
import { approvalPolicyPath, killPath, limitsPath } from "../data/queries";

/** Stop one rApp's writes, with a reason. */
export function StopDialog({ instanceId, onClose }: { instanceId: string; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const action = useSmoAction();
  return (
    <Modal title="Stop this rApp" onClose={onClose}>
      <p className="muted small">Its config jobs (dry runs too) are refused at once, and every other change it makes through the gateway is refused within a few seconds. It can still read, withdraw what it made (delete) and undo its own config jobs. The instance itself keeps running.</p>
      <Field label="Reason" hint="Shown to everyone who looks, and in the refusal events">
        <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} autoFocus />
      </Field>
      <div className="row gap">
        <button type="button" className="btn danger" disabled={action.isPending} onClick={() => action.mutate(
          { method: "PUT", path: killPath(instanceId), json: { reason: reason.trim() || null }, success: "rApp stopped", invalidates: ["ran-nf-oam"] }, { onSuccess: onClose })}>
          {action.isPending ? "…" : "Stop rApp"}
        </button>
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

/** Set (replace) the limits of one invoker. */
export function LimitsDialog({ invokerId, current, onClose }: { invokerId: string; current: InstanceSafeguards["limits"]; onClose: () => void }) {
  const [form, setForm] = useState(limitsForm(current));
  const [problem, setProblem] = useState<string | null>(null);
  const action = useSmoAction();
  const save = () => {
    const parsed = limitsPayload(form);
    if (!parsed.ok) return setProblem(parsed.error);
    setProblem(null);
    action.mutate({ method: "PUT", path: limitsPath(invokerId), json: parsed.body, success: "Limits saved", invalidates: ["rapp-mgmt"] }, { onSuccess: onClose });
  };
  return (
    <Modal title="Limits for this rApp" onClose={onClose}>
      <p className="muted small">Saving replaces the whole set: a blank field removes that limit. Rollbacks and reverts are never limited.</p>
      <Field label="Config jobs per hour" hint="1 to 100000"><input inputMode="numeric" value={form.jobsPerHour} onChange={(e) => setForm({ ...form, jobsPerHour: e.target.value })} /></Field>
      <Field label="Managed elements per job" hint="1 to 10000: how many elements one job may touch"><input inputMode="numeric" value={form.elementsPerJob} onChange={(e) => setForm({ ...form, elementsPerJob: e.target.value })} /></Field>
      <Field label="Change per write, %" hint="Greater than 0, up to 10000: how far a numeric value may move, in percent of its current value">
        <input inputMode="decimal" value={form.changePercent} onChange={(e) => setForm({ ...form, changePercent: e.target.value })} />
      </Field>
      {problem && <div className="error-box" role="alert">{problem}</div>}
      <div className="row gap">
        <button type="button" className="btn primary" disabled={action.isPending} onClick={save}>{action.isPending ? "…" : "Save limits"}</button>
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

/** Hold one invoker's changes for approval: how long a request may wait, what a lapsed one becomes, and whether one person or two different people must approve. */
export function ApprovalPolicyDialog({ invokerId, current, onClose }: { invokerId: string; current: ApprovalPolicy | null; onClose: () => void }) {
  const [form, setForm] = useState(approvalPolicyForm(current));
  const [problem, setProblem] = useState<string | null>(null);
  const action = useSmoAction();
  const save = () => {
    const parsed = approvalPolicyPayload(form);
    if (!parsed.ok) return setProblem(parsed.error);
    setProblem(null);
    action.mutate({ method: "PUT", path: approvalPolicyPath(invokerId), json: parsed.body, success: "Changes by this rApp now wait for approval", invalidates: ["rapp-mgmt"] }, { onSuccess: onClose });
  };
  return (
    <Modal title="Hold this rApp's changes for approval" onClose={onClose}>
      <p className="muted small">From now on every config job this rApp asks for waits in the Approvals inbox until a person approves it; nothing is written before. Dry runs, rollbacks and reverts are not held.</p>
      <Field label="A request may wait, minutes" hint="1 to 10080 (a week)"><input inputMode="numeric" value={form.minutes} onChange={(e) => setForm({ ...form, minutes: e.target.value })} /></Field>
      <Field label="A request nobody decided" hint="Neither writes anything; there is no option that approves by itself">
        <select value={form.onTimeout} onChange={(e) => setForm({ ...form, onTimeout: e.target.value as "EXPIRE" | "REJECT" })}>
          <option value="EXPIRE">expires</option>
          <option value="REJECT">is rejected by the platform</option>
        </select>
      </Field>
      {/* two-person approval: `requiredApprovals: 2` is sent only when two is chosen (approvalPolicyPayload), so a policy left at one sends no such key */}
      <Field label="Approvals needed" hint="Two: two different people must approve; the first keeps it waiting, the second writes it, one rejection ends it, and the requester's own never counts">
        <select value={form.twoApprovals ? "2" : "1"} onChange={(e) => { const { twoApprovals: _drop, ...rest } = form; setForm(e.target.value === "2" ? { ...rest, twoApprovals: true } : rest); }}>
          <option value="1">one person (the default)</option>
          <option value="2">two different people</option>
        </select>
      </Field>
      {problem && <div className="error-box" role="alert">{problem}</div>}
      <div className="row gap">
        <button type="button" className="btn primary" disabled={action.isPending} onClick={save}>{action.isPending ? "…" : "Hold for approval"}</button>
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}
