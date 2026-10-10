/** Every API path, query parameter and polling choice of the Data & Exposure page (STRUCTURE.md rule 4): DME (types, producers, jobs, offers,
 * type subscriptions) and SME / CAPIF (providers, published services, invokers, trusted invokers, discovery, event subscriptions). Sections call
 * these hooks or pass these paths to `kit/ServerTable`. Each tab mounts only its own sections, so only the visible tab's queries run. */
import { useSmo, useSmoPage } from "../../../api/hooks";
import type { DataJob, DataOffer, DmeProducer, DmeType, SmeInvoker, SmeService, TrustedInvoker } from "../../../api/types";

/** How many jobs and offers the data-flow view reads at most (one bounded page each, the modules' MAX_LIMIT); the view says when it is cut. */
export const FLOW_LIMIT = 500;

/** The routes the page's tables and actions use. */
export const PATHS = {
  types: "/dme/dme-types",
  producers: "/dme/production-capabilities",
  dataJobs: "/dme/data-jobs",
  offers: "/dme/offers",
  typeSubscriptions: "/dme/type-subscriptions",
  providers: "/sme/provider-registrations",
  invokers: "/sme/invoker-registrations",
  trustedInvokers: "/sme/trusted-invokers",
  publishedServices: (apfId: string) => `/sme/published-apis/v1/${apfId}/service-apis`,
  discovery: "/sme/service-apis/v1/allServiceAPIs",
  capifSubscriptions: (subscriber: string) => `/sme/capif-events/v1/${subscriber}/subscriptions`,
} as const;

/** Every DME type (the route is not paged: a registry of types, small by nature). */
export function useDmeTypes() {
  return useSmo<DmeType[]>(PATHS.types);
}

/** Every registered producer (the route is not paged). */
export function useProducers() {
  return useSmo<DmeProducer[]>(PATHS.producers);
}

/** The jobs the data-flow view aggregates: one bounded page, with its envelope so a cut can be named. */
export function useFlowJobs() {
  return useSmoPage<DataJob>(PATHS.dataJobs, { limit: FLOW_LIMIT, offset: 0 });
}

/** How many data jobs are late (two declared intervals without a delivery): the `total` of a one-row page filtered `late=true`. */
export function useLateJobCount() {
  return useSmoPage<DataJob>(PATHS.dataJobs, { late: true, limit: 1 });
}

/** The offers the data-flow view counts per type: one bounded page. */
export function useFlowOffers() {
  return useSmoPage<DataOffer>(PATHS.offers, { limit: FLOW_LIMIT, offset: 0 });
}

/** The offers of one type (`?dme_type_id=`), for the methods a new job can use; nothing is read until a type is chosen. */
export function useOffersForType(typeId: string) {
  return useSmo<DataOffer[]>(typeId ? PATHS.offers : null, { dme_type_id: typeId, limit: 100 });
}

/** The service APIs one provider published (the route is not paged). */
export function usePublishedServices(apfId: string) {
  return useSmo<SmeService[]>(PATHS.publishedServices(apfId));
}

/** How many invokers have a security context: the total of a one-row page. */
export function useTrustedCount() {
  return useSmoPage<TrustedInvoker>(PATHS.trustedInvokers, { limit: 1, offset: 0 });
}

/** Invokers to pick from in the discovery box: one bounded page, read only once `enabled` (the picker was used). */
export function useInvokerChoices(enabled: boolean) {
  return useSmoPage<SmeInvoker>(PATHS.invokers, { limit: FLOW_LIMIT, offset: 0 }, { enabled });
}

/** The services an invoker can discover, filtered by API name (`?api_name=`); nothing is read until an invoker is chosen. */
export function useDiscovery(invoker: string, apiName: string) {
  return useSmo<SmeService[]>(invoker ? PATHS.discovery : null, { api_invoker_id: invoker, api_name: apiName });
}
