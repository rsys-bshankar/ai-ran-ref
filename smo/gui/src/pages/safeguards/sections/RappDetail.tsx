/** Section `safeguards.detail`: the selected rApp instance — whether it may write (and who stopped it, why, when), its limits as meters, its
 * approval policy, and its refusals in the last 24 h (a server count: one call with `limit=1`). */
import { useMemo } from "react";

import { Card, Id, KeyValue, StateBadge } from "../../../components/ui";
import { formatCount } from "../../../kit/Kpi";
import { UsageMeter } from "../../../kit/Meter";
import { Empty, QueryState } from "../../../kit/states";
import { describeApprovalPolicy, describeLimits, formatTime } from "../../../lib/domain";
import { refusalSince, useInstanceSafeguards, useInvokerRefusalCount } from "../data/queries";

/** The detail card; `instanceId` null asks the operator to pick a row. */
export function RappDetail({ instanceId }: { instanceId: string | null }) {
  const sg = useInstanceSafeguards(instanceId);
  const since = useMemo(() => refusalSince("24h") ?? "", [instanceId]); // eslint-disable-line react-hooks/exhaustive-deps
  const invoker = sg.data?.invokerId ?? null;
  const refusals = useInvokerRefusalCount(since, invoker);
  if (!instanceId) return <Card section="safeguards.detail" title="rApp"><Empty title="No rApp selected.">Click a row to see its safeguards.</Empty></Card>;
  const v = sg.data;
  const l = v?.limits;
  return (
    <Card section="safeguards.detail" title={<>rApp <Id value={instanceId} /></>}>
      <QueryState q={sg} isEmpty={() => false}>
        {v && <>
          <div className="row gap wrap">
            {!v.invokerId ? <span className="muted">no credential (terminated)</span> : v.killed ? <><StateBadge state="DISABLED" /> stopped</> : <StateBadge state="ACTIVE" />}
            {v.invokerId && <Id value={v.invokerId} />}
          </div>
          {v.killed && v.kill && <p className="small">Stopped by {v.kill.killedBy} · {formatTime(v.kill.killedAt)} · {v.kill.reason ?? "no reason given"}</p>}
          {l?.maxConfigJobsPerHour != null && <div className="col"><span className="small">Config jobs this hour: {l.configJobsLastHour} of {l.maxConfigJobsPerHour}</span>
            <UsageMeter used={l.configJobsLastHour} limit={l.maxConfigJobsPerHour} /></div>}
          <KeyValue items={[
            ["Limits", v.invokerId ? describeLimits(l) : "—"],
            ["Approval", v.invokerId ? describeApprovalPolicy(v.approvalPolicy) : "—"],
            ["Refusals · 24 h", v.invokerId ? (refusals.data ? formatCount(refusals.data.total ?? null) : refusals.error ? "unknown" : "…") : "—"],
          ]} />
        </>}
      </QueryState>
    </Card>
  );
}
