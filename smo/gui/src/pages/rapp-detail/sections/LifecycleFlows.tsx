/** "Lifecycle flows for this rApp" (BRIEF §4, SCALE.md rApp detail): flows 01 (onboarding → running), 06 (its package) and 07 (its instance)
 * as one-line steppers, each built from the same `lib/flows.ts` evaluator the flow board uses, so a step reads the same on both. Each row
 * opens its board (`/flows/<id>?subject=<id>`). One rApp, so it is bounded: the package status, its usage and the NFO deployment are one
 * call each; the instance, performance and faults are shared with the boxes beside it. Flow 02 (the rApp's model) is listed but not
 * evaluated: the backend links no model to an rApp. Section id `rapp.flows`. */
import { Link } from "react-router-dom";

import type { RappPage } from "../../../api/rapps";
import type { Package } from "../../../api/types";
import { Card } from "../../../components/ui";
import { MiniSteps } from "../../../kit/Timeline";
import { Skeleton } from "../../../kit/states";
import { flow01, flow06, flow07, progress, toStepState, type FlowStep } from "../../../lib/flows";
import { useDeployment, useInstance, usePackageStatus, usePackageUsage, usePerformance, useRecentFaults } from "../data/queries";

/** One stepper row: number, title, subject, the steps as segments, and where the flow stands. */
function FlowRow({ id, title, subject, subjectLabel, steps }: { id: string; title: string; subject: string; subjectLabel: string; steps: FlowStep[] | null }) {
  const p = steps ? progress(steps) : null;
  const at = steps?.find((s) => s.status === "current" || s.status === "failed") ?? steps?.find((s) => s.status === "warn");
  return (
    <li>
      <Link className="row gap wrap rd-flow" to={`/flows/${id}?subject=${subject}`}>
        <span className="flow-num">{id}</span>
        <span className="col rd-flow-t"><strong>{title} →</strong><span className="small muted">{subjectLabel}</span></span>
        {steps ? <MiniSteps states={steps.map((s) => toStepState(s.status))} label={`Flow ${id}: ${p!.done} of ${p!.total} steps`} /> : <Skeleton lines={1} />}
        {p && <span className="small">{p.done}/{p.total}{p.complete ? " · complete" : at ? ` · ${at.title}` : ""}</span>}
      </Link>
    </li>
  );
}

/** The box of rApp `rapp`. */
export function LifecycleFlows({ rapp }: { rapp: RappPage }) {
  const id = rapp.instanceId;
  const inst = useInstance(id);
  const perf = usePerformance(id);
  const faults = useRecentFaults(id);
  const status = usePackageStatus(rapp.packageId);
  const usage = usePackageUsage(rapp.packageId);
  const deployment = useDeployment(inst.data?.workloadRef);
  const pkg: Package | undefined = status.data
    ? ({ ...status.data, name: rapp.name ?? "package", version: rapp.version ?? "" } as Package)
    : undefined;
  const ready = !!inst.data && !!status.data;
  const pkgLabel = `package ${rapp.name ?? rapp.packageId.slice(0, 8)} ${rapp.version ?? ""}`.trim();
  return (
    <Card section="rapp.flows" title="Lifecycle flows for this rApp" sub="every journey this rApp, its package and its model take · each step proved by live state"
      actions={<Link className="btn small" to={`/flows/07?subject=${id}`}>Open in Lifecycle flows</Link>}>
      {(inst.error || status.error) && <p className="small text-bad">Some lifecycle state could not be read; those rows stay unevaluated.</p>}
      <ul className="list">
        <FlowRow id="01" title="Onboarding → running" subject={rapp.packageId} subjectLabel={pkgLabel}
          steps={ready ? flow01(pkg, inst.data, deployment.data) : null} />
        <FlowRow id="06" title="Package lifecycle" subject={rapp.packageId} subjectLabel={pkgLabel}
          steps={status.data && usage.data ? flow06(pkg, usage.data, usage.data.filter((u) => u.active).length) : null} />
        <FlowRow id="07" title="Instance lifecycle" subject={id} subjectLabel={`instance ${id.slice(0, 8)}`}
          steps={inst.data && perf.data && faults.data ? flow07(inst.data, perf.data, faults.data) : null} />
        <li>
          <Link className="row gap wrap rd-flow" to="/flows/02">
            <span className="flow-num">02</span>
            <span className="col rd-flow-t"><strong>Its model: train → serve → retire →</strong><span className="gap-note">the backend links no AI/ML model to an rApp; pick the model on the board</span></span>
          </Link>
        </li>
      </ul>
    </Card>
  );
}
