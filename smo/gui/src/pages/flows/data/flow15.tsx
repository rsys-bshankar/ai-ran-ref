/** Flow 15 board data (NFO workload: instantiate → scale → heal → terminate): the subject is an NF deployment. Loads the deployment list
 * and the chosen deployment's LCM operations (scale and heal counts). */
import { useSmo } from "../../../api/hooks";
import type { LcmOperation, NfDeployment } from "../../../api/types";
import { ActionButton } from "../../../components/ui";
import { flow15 } from "../../../lib/flows";
import { go } from "./go";
import { choose, SUBJECT_LIMIT } from "./subjects";
import type { FlowBoardData } from "./types";

/** The board of flow 15 for deployment `subjectId`. */
export function useFlow15(subjectId: string | null): FlowBoardData {
  const deployments = useSmo<NfDeployment[]>("/nfo/deployments", { limit: SUBJECT_LIMIT });
  const dep = choose(deployments.data, subjectId, (d) => d.nfDeploymentId);
  const ops = useSmo<LcmOperation[]>(dep ? `/nfo/deployments/${dep.nfDeploymentId}/operations` : null, { limit: 100 });
  const base = `/nfo/deployments/${dep?.nfDeploymentId}`;
  const st = dep?.state ?? "";
  const label = (d: NfDeployment) => `${d.name} · ${d.state}`;
  const terminate = ["RUNNING", "ABNORMAL"].includes(st) && <>
    <ActionButton label="Terminate" tone="danger" confirm="Terminate this deployment? It is deleted and its descriptor is free again." action={{ method: "DELETE", path: base, success: "Deployment terminated" }} />
    <ActionButton label="Terminate (async)" tone="danger" confirm="Terminate asynchronously? It rests in TERMINATING until the deployment manager reports." action={{ method: "DELETE", path: base, query: { async_uninstall: true }, success: "Deployment TERMINATING" }} />
  </>;
  return {
    subjects: deployments.data?.map((d) => ({ id: d.nfDeploymentId, label: label(d) })),
    subjectsError: deployments.error, retry: () => void deployments.refetch(),
    selected: dep && { id: dep.nfDeploymentId, label: label(dep) },
    steps: flow15(dep, ops.data ?? []),
    empty: <>No NF deployments. {go("/infrastructure#nfo", "NF deployments")}</>,
    alwaysActions: ["scale"],
    actions: {
      scale: st === "RUNNING" && <ActionButton label="Scale" action={{ method: "POST", path: `${base}/scale`, success: "Deployment scaled" }} />,
      heal: <ActionButton label="Heal" tone="primary" action={{ method: "POST", path: `${base}/heal`, success: "Deployment healed" }} />,
      terminate,
    },
  };
}
