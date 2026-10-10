/** Flow 10 board data (SO SMOS multi-step order: INFRA → TRAINING → DEPLOY): the subject is a service order; its steps come from the order
 * itself, so the order list is the only call. */
import { useSmo } from "../../../api/hooks";
import type { ServiceOrder } from "../../../api/types";
import { ActionButton, Id } from "../../../components/ui";
import { flow10 } from "../../../lib/flows";
import { go } from "./go";
import { choose, SUBJECT_LIMIT } from "./subjects";
import type { FlowBoardData } from "./types";

/** The board of flow 10 for order `subjectId`. */
export function useFlow10(subjectId: string | null): FlowBoardData {
  const orders = useSmo<ServiceOrder[]>("/so-smos/orders", { limit: SUBJECT_LIMIT });
  const order = choose(orders.data, subjectId, (o) => o.orderId);
  const pending = order?.steps.some((s) => s.status === "PENDING");
  const label = (o: ServiceOrder) => `${o.scope} (${o.orderId.slice(0, 8)})`;
  return {
    subjects: orders.data?.map((o) => ({ id: o.orderId, label: label(o) })),
    subjectsError: orders.error, retry: () => void orders.refetch(),
    selected: order && { id: order.orderId, label: label(order) },
    steps: flow10(order),
    empty: <>No orders. {go("/infrastructure#orders", "Submit one")}</>,
    actions: { submit: go("/infrastructure#orders", "Submit an order") },
    extra: order && <div className="row gap wrap between">
      <p className="muted small">Order <Id value={order.orderId} /> · RMIH {order.rmihRegistration}. Completed steps are never rolled back; cancelling only touches PENDING steps.</p>
      {pending && <ActionButton label="Cancel pending steps" action={{ method: "POST", path: `/so-smos/orders/${order.orderId}/cancel`, success: "Pending steps cancelled" }} />}
    </div>,
  };
}
