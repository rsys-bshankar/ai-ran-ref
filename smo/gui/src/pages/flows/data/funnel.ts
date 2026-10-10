/** The fleet funnel of each flow, built ONLY from the BFF summary's true state counts (SCALE.md P2): a funnel row is a state count that
 * matches a step of the flow (flow 07's instance states, flow 06's package states …). A flow whose steps have no state count (02, 04, 08,
 * 10, 16, 19) has no entry: the box hides with a gap note (a per-step aggregate across all subjects needs a server route, SCALE.md §5). */
import type { SummaryPage } from "../../../data/summary";

/** One funnel row: what the subjects at this point are, the summary key that counts them, and a tone for a stuck or failed state. */
export interface FunnelRow { label: string; key: string; tone?: "ok" | "warn" | "bad" }
/** A flow's funnel: which summary page serves it, the key of the whole fleet, and its rows. */
export interface FunnelDef { page: SummaryPage; totalKey: string; noun: string; rows: FunnelRow[] }

/** The funnels, by flow id. */
export const FUNNELS: Record<string, FunnelDef> = {
  "01": { page: "rapps", totalKey: "packages.total", noun: "packages", rows: [
    { label: "Onboarding (validating)", key: "packages.ONBOARDING" }, { label: "Validation failed", key: "packages.FAILED", tone: "bad" },
    { label: "AVAILABLE (deployable)", key: "packages.AVAILABLE", tone: "ok" }, { label: "Instances bootstrapping", key: "instances.DEPLOYING", tone: "warn" },
    { label: "Instances running", key: "instances.RUNNING", tone: "ok" }] },
  "06": { page: "rapps", totalKey: "packages.total", noun: "packages", rows: [
    { label: "Onboarding", key: "packages.ONBOARDING" }, { label: "Available", key: "packages.AVAILABLE", tone: "ok" },
    { label: "Primed", key: "packages.PRIMED", tone: "ok" }, { label: "Deprecated", key: "packages.DEPRECATED" }, { label: "Failed", key: "packages.FAILED", tone: "bad" }] },
  "07": { page: "rapps", totalKey: "instances.total", noun: "instances", rows: [
    { label: "Deploying (bootstrap)", key: "instances.DEPLOYING", tone: "warn" }, { label: "Running", key: "instances.RUNNING", tone: "ok" },
    { label: "Faulted (recover)", key: "instances.FAULTED", tone: "bad" }, { label: "Upgrading", key: "instances.UPGRADING", tone: "warn" },
    { label: "Terminated", key: "instances.UNDEPLOYED" }] },
  "03": { page: "configuration", totalKey: "configJobs.total", noun: "config jobs", rows: [
    { label: "Pending", key: "configJobs.PENDING" }, { label: "Processing", key: "configJobs.PROCESSING" }, { label: "Halted", key: "configJobs.HALTED", tone: "warn" },
    { label: "Completed", key: "configJobs.COMPLETED", tone: "ok" }, { label: "Partial success", key: "configJobs.PARTIAL_SUCCESS", tone: "warn" },
    { label: "Failed", key: "configJobs.FAILED", tone: "bad" }] },
  "09": { page: "intents", totalKey: "intents.total", noun: "intents", rows: [
    { label: "Activated", key: "intents.ACTIVATED", tone: "ok" }, { label: "Deactivated (admin state changed)", key: "intents.DEACTIVATED" }] },
  "15": { page: "infrastructure", totalKey: "deployments.total", noun: "NF deployments", rows: [
    { label: "Instantiating", key: "deployments.INSTANTIATING" }, { label: "Running", key: "deployments.RUNNING", tone: "ok" },
    { label: "Updating (scale)", key: "deployments.UPDATING" }, { label: "Abnormal (heal)", key: "deployments.ABNORMAL", tone: "bad" },
    { label: "Terminating", key: "deployments.TERMINATING", tone: "warn" }] },
};
