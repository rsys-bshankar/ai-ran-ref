/** Flow 19 board data (RAN software job: download → install → activate, with a failed phase): the subject is a software management job;
 * its phase and status are on the job row, so the job list is the only call. */
import { useSmo } from "../../../api/hooks";
import type { SwmJob } from "../../../api/types";
import { ActionButton } from "../../../components/ui";
import { flow19 } from "../../../lib/flows";
import { go } from "./go";
import { choose, SUBJECT_LIMIT } from "./subjects";
import type { FlowBoardData } from "./types";

/** The board of flow 19 for job `subjectId`. */
export function useFlow19(subjectId: string | null): FlowBoardData {
  const jobs = useSmo<SwmJob[]>("/ran-nf-oam/software-management-jobs", { limit: SUBJECT_LIMIT });
  const job = choose(jobs.data, subjectId, (j) => j.jobId);
  const label = (j: SwmJob) => `${j.managedElementRef} · ${j.jobId.slice(0, 8)} · ${j.status}${j.status === "IN_PROGRESS" ? ` (${j.phase})` : ""}`;
  const advance = job && job.status === "IN_PROGRESS" && <>
    <ActionButton label={`${job.phase} ok`} tone="primary" action={{ method: "POST", path: `/ran-nf-oam/software-management-jobs/${job.jobId}/advance`, query: { succeeded: true }, success: "Phase advanced" }} />
    <ActionButton label={`${job.phase} failed`} tone="danger" action={{ method: "POST", path: `/ran-nf-oam/software-management-jobs/${job.jobId}/advance`, query: { succeeded: false }, success: "Job FAILED" }} />
  </>;
  const retry = job && job.status === "FAILED" && <ActionButton label="Retry from DOWNLOAD (new job)"
    action={{ method: "POST", path: "/ran-nf-oam/software-management-jobs", query: { managed_element_ref: job.managedElementRef, ...(job.ruInstanceId ? { ru_instance_id: job.ruInstanceId } : {}) }, success: "New software job started (DOWNLOAD)" }} />;
  return {
    subjects: jobs.data?.map((j) => ({ id: j.jobId, label: label(j) })),
    subjectsError: jobs.error, retry: () => void jobs.refetch(),
    selected: job && { id: job.jobId, label: label(job) },
    steps: flow19(job),
    empty: <>No software jobs. {go("/software", "Software campaigns")} or {go("/infrastructure#o1", "start one on an element")}</>,
    actions: { create: go("/infrastructure#o1", "New software job"), download: advance || retry, install: advance || retry, activate: advance || retry },
  };
}
