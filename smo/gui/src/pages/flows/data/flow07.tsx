/** Flow 07 board data (rApp instance lifecycle: report → fault → recover → upgrade → terminate): the subject is an instance. Loads the
 * instance list, the instance, and its newest 50 performance reports and faults. */
import { useSmo } from "../../../api/hooks";
import type { FaultReport, Instance, InstanceSummary, PerfReport } from "../../../api/types";
import { ActionButton } from "../../../components/ui";
import { flow07 } from "../../../lib/flows";
import { go } from "./go";
import { choose, SUBJECT_LIMIT } from "./subjects";
import type { FlowBoardData } from "./types";

/** The board of flow 07 for instance `subjectId`. */
export function useFlow07(subjectId: string | null): FlowBoardData {
  const instances = useSmo<InstanceSummary[]>("/rapp-mgmt/instances", { limit: SUBJECT_LIMIT });
  const chosen = choose(instances.data, subjectId, (i) => i.instanceId);
  const id = chosen?.instanceId ?? null;
  const instance = useSmo<Instance>(id ? `/rapp-mgmt/instances/${id}` : null);
  const perf = useSmo<PerfReport[]>(id ? `/rapp-mgmt/instances/${id}/performance` : null, { limit: 50 });
  const faults = useSmo<FaultReport[]>(id ? `/rapp-mgmt/instances/${id}/faults` : null, { limit: 50 });
  const base = `/rapp-mgmt/instances/${id}`;
  const label = (i: InstanceSummary) => `${i.instanceId.slice(0, 8)} (${i.state})`;
  return {
    subjects: instances.data?.map((i) => ({ id: i.instanceId, label: label(i) })),
    subjectsError: instances.error, retry: () => void instances.refetch(),
    selected: chosen && { id: chosen.instanceId, label: label(chosen) },
    steps: flow07(instance.data, perf.data ?? [], faults.data ?? []),
    empty: <>No instances. {go("/flows/01", "Run flow 01 first")}</>,
    actions: {
      perf: <ActionButton label="Report performance" title="Simulates the rApp's own report" action={{ method: "POST", path: `${base}/performance`, json: { throughputMbps: 120, latencyMs: 8 }, success: "Performance recorded" }} />,
      minor: <ActionButton label="Report minor fault" action={{ method: "POST", path: `${base}/fault`, query: { severity: "minor", description: "degraded throughput" }, success: "Fault recorded" }} />,
      crash: <ActionButton label="Report critical fault" tone="danger" action={{ method: "POST", path: `${base}/fault`, query: { severity: "critical", description: "container crash" }, success: "Instance FAULTED" }} />,
      recover: instance.data?.state === "FAULTED"
        ? <ActionButton label="Recover" tone="primary" action={{ method: "POST", path: `${base}/recover`, success: "Re-entering DEPLOYING" }} />
        : <ActionButton label="Mark re-bootstrapped" tone="primary" action={{ method: "POST", path: `${base}/bootstrap-complete`, success: "Instance RUNNING" }} />,
    },
  };
}
