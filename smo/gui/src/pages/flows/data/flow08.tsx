/** Flow 08 board data (RAN Analytics: producer → report → subscriber query): the subject is an analytics type, gathered from the producers,
 * subscriptions and reports; the type's producers' SME service APIs prove the SME step. */
import { useQueries } from "@tanstack/react-query";

import { smo } from "../../../api/client";
import { unwrapPage, useSmo } from "../../../api/hooks";
import type { AnalyticsProducer, AnalyticsReport, AnalyticsSubscription, SmeService } from "../../../api/types";
import { ActionButton } from "../../../components/ui";
import { flow08 } from "../../../lib/flows";
import { go } from "./go";
import { choose, SUBJECT_LIMIT } from "./subjects";
import type { FlowBoardData } from "./types";

/** The board of flow 08 for analytics type `subjectId`. */
export function useFlow08(subjectId: string | null): FlowBoardData {
  const producers = useSmo<AnalyticsProducer[]>("/ran-analytics/producers", { limit: SUBJECT_LIMIT });
  const subs = useSmo<AnalyticsSubscription[]>("/mdaf/subscriptions", { limit: SUBJECT_LIMIT });
  const reports = useSmo<AnalyticsReport[]>("/mdaf/reports", { limit: SUBJECT_LIMIT });
  const loaded = producers.data && subs.data && reports.data;
  const types = loaded ? [...new Set([...producers.data!, ...subs.data!, ...reports.data!].map((x) => x.analyticsType))] : undefined;
  const type = choose(types, subjectId, (t) => t) ?? "";
  const producerIds = [...new Set((producers.data ?? []).filter((p) => p.analyticsType === type).map((p) => p.producerId))];
  const services = useQueries({
    queries: producerIds.map((pid) => ({
      queryKey: ["smo", `/sme/published-apis/v1/${pid}/service-apis`, {}],
      queryFn: async () => unwrapPage<SmeService[]>(await smo<unknown>(`/sme/published-apis/v1/${pid}/service-apis`)),
    })),
  });
  const names = services.flatMap((s) => s.data ?? []).map((s) => s.serviceName);
  const error = producers.error ?? subs.error ?? reports.error;
  return {
    subjects: types?.map((t) => ({ id: t, label: t })),
    subjectsError: error, retry: () => { void producers.refetch(); void subs.refetch(); void reports.refetch(); },
    selected: type ? { id: type, label: type } : undefined,
    steps: flow08(type, producers.data ?? [], subs.data ?? [], reports.data ?? [], names),
    empty: <>No analytics producers, subscriptions or reports yet. {go("/kpis#analytics", "Register a producer")}</>,
    actions: {
      producer: go("/kpis#analytics", "Register a producer"),
      sme: go("/data#sme", "SME registry"),
      subscribe: <ActionButton label="Subscribe (poll)" action={{ method: "POST", path: "/mdaf/subscriptions", query: { analytics_type: type, requested_by: "smo-gui" }, success: "Subscribed" }} />,
      publish: go("/kpis#analytics", "Publish a report"),
    },
  };
}
