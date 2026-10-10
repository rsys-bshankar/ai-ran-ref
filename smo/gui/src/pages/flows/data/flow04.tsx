/** Flow 04 board data (closed-loop assurance): the subject is an assurance monitor. Loads the monitor list, the monitor's service order and
 * remedial actions, and the number of MDAF and MLMF reports (one-row pages, their `total` only). */
import { useSmo, useSmoPage } from "../../../api/hooks";
import type { AnalyticsReport, MlmfReport, Monitor, RemedialAction, ServiceOrder } from "../../../api/types";
import { ActionButton } from "../../../components/ui";
import { flow04 } from "../../../lib/flows";
import { go } from "./go";
import { choose, SUBJECT_LIMIT } from "./subjects";
import type { FlowBoardData } from "./types";

/** What a monitor watches, for the picker. */
const target = (m: Monitor) => (m.targetOrderId ? "order" : m.targetCoordinationGroupId ? "model group" : m.targetRappInstanceId ? "rApp instance" : "unscoped");

/** The board of flow 04 for monitor `subjectId`. */
export function useFlow04(subjectId: string | null): FlowBoardData {
  const monitors = useSmo<Monitor[]>("/sa-smos/monitors", { limit: SUBJECT_LIMIT });
  const monitor = choose(monitors.data, subjectId, (m) => m.monitorId);
  const monitorId = monitor?.monitorId ?? null;
  const order = useSmo<ServiceOrder>(monitor?.targetOrderId ? `/so-smos/orders/${monitor.targetOrderId}` : null);
  const actions = useSmo<RemedialAction[]>(monitorId ? "/sa-smos/remedial-actions" : null, { monitor_id: monitorId ?? undefined });
  const analytics = useSmoPage<AnalyticsReport>("/mdaf/reports", { limit: 1 });
  const mlmf = useSmoPage<MlmfReport>("/aimgf/mlmf/reports", { limit: 1 });
  const total = (p: { total?: number; items: unknown[] } | undefined) => p?.total ?? p?.items.length ?? 0;
  const base = `/sa-smos/monitors/${monitorId}`;
  const label = (m: Monitor) => `${m.monitorId.slice(0, 8)} · ${target(m)}`;
  return {
    subjects: monitors.data?.map((m) => ({ id: m.monitorId, label: label(m) })),
    subjectsError: monitors.error, retry: () => void monitors.refetch(),
    selected: monitor && { id: monitor.monitorId, label: label(monitor) },
    steps: flow04(monitor, order.data, actions.data ?? [], total(analytics.data), total(mlmf.data)),
    empty: <>No monitors. {go("/infrastructure#orders", "Submit an order")} then {go("/kpis#assurance", "register a monitor")}</>,
    actions: {
      order: go("/infrastructure#orders", "Service orders"),
      input: go("/kpis#analytics", "Analytics"),
      remediate: <div className="row gap wrap">
        {["CONFIG_CHANGE", "RECONNECT", "SCALE"].map((t) => (
          <ActionButton key={t} label={t} action={{ method: "POST", path: `${base}/remedial-actions`, query: { action_type: t }, success: `${t} dispatched` }} />
        ))}
        {go("/kpis#assurance", "Evaluate thresholds")}
      </div>,
      escalate: <ActionButton label="Escalate to operator" tone="danger" action={{ method: "POST", path: `${base}/escalate`, query: { reason: "raised from the lifecycle view" }, success: "Escalated" }} />,
    },
  };
}
