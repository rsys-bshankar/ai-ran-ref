/** View types of the Configuration page, mapped from RAN NF OAM (smo/ran-nf-oam/app/main.py `list_write_config_jobs`, the dry-run answer of
 * `POST /config-jobs`, `list_o1_adaptor_endpoints`; vendors.py `_capability_view`, `_schema_view`). The job detail reuses `ConfigJob` from
 * `api/types.ts` (the shape `ConfigJobDrawer` already reads); the onboarding row and host keys come from `pages/element/data/types.ts`. */
import type { ConfigJob } from "../../../api/types";

/** A config job's state (statemachine.py `JobState`). */
export const JOB_STATES = ["HALTED", "PENDING", "PROCESSING", "COMPLETED", "PARTIAL_SUCCESS", "FAILED"] as const;

/** One row of `GET /config-jobs`. */
export interface JobRow { jobId: string; requestedBy: string; accessScope: string; scope: string; status: string; msacRole: string | null }

/** One sub-change of a job. */
export type SubChange = ConfigJob["subChanges"][number];

/** The 200 answer of `POST /config-jobs` with `dryRun: true`: every check passed, each change's verdict, the waves; nothing written. */
export interface JobDryRun {
  dryRun: true; status: "VALIDATED" | "WOULD_REJECT_SOME"; waves: string[][];
  changes: { managedElementRef: string; managedFunctionRef: string | null; operation: string; verdict: "PASS" | "WOULD_REJECT"; reason: string | null }[];
}

/** One row of `GET /vendor-capabilities`. */
export interface VendorCapability {
  vendorName: string; supportedServices: string[]; conformanceMode: "SPEC" | "OWN" | "COMBINED"; supportedVendorModes: string[];
  schemaRef: { schemaName: string; revision: string } | null; specSchemaRef: { schemaName: string; revision: string } | null;
  discoveryUri: string | null; updatedAt: string;
}

/** One row of `GET /cm-schemas` (built-in descriptors first). */
export interface CmSchema { schemaName: string; revision: string; type: "YANG" | "OPENAPI_NRM" | "DESCRIPTOR"; location: string; builtin: boolean; classCount: number }

/** One row of `GET /o1-adaptor-endpoints`, with its transport (host keys apply to `ssh` only). */
export interface Endpoint {
  endpointId: string; managedElementRef: string; adaptorUri: string; transport: string; healthStatus: string; lastHeartbeatAt: string | null;
  region?: string | null; tenant?: string | null;
}

/** The state of one wave of a job, from its sub-changes. */
export type WaveState = "applied" | "rejected" | "reverted" | "skipped" | "running" | "waiting";

/** The state of wave `wave` of `job`: what its sub-changes became, or whether it is running or still to come. */
export function jobWaveState(job: Pick<ConfigJob, "status" | "currentWave" | "subChanges">, wave: number): WaveState {
  const rows = job.subChanges.filter((s) => (s.wave ?? 1) === wave);
  if (rows.length > 0 && rows.every((s) => s.status === "REJECTED" && s.rejectionReason === "WAVE_NOT_RUN")) return "skipped";
  if (rows.some((s) => s.status === "REVERTED")) return "reverted";
  if (rows.some((s) => s.status === "REJECTED")) return "rejected";
  if (rows.length > 0 && rows.every((s) => s.status === "APPLIED")) return "applied";
  if (job.status === "PROCESSING" && wave === (job.currentWave ?? 0) + 1) return "running";
  return "waiting";
}

/** Sub-changes by status (`APPLIED`, `REJECTED`, `PENDING`, `REVERTED`) of one job: its own detail, so bounded. */
export function subChangeCounts(subChanges: SubChange[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const s of subChanges) out[s.status] = (out[s.status] ?? 0) + 1;
  return out;
}
