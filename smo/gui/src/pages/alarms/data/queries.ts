/** The Alarms page's API knowledge (STRUCTURE.md rule 4): the RAN NF OAM and FOCOM alarm routes, their query parameters (the OpenAPI `GET`
 * parameters only: `severity`, `managed_element_ref`, `managed_function_ref` for RAN alarms; `severity`, `resource_ref` for O-Cloud alarms),
 * polling, and the "same managed element within 60 s" heuristic of the root-cause hint. Sections call the hooks here, never `useSmo` with a path. */
import { keepPreviousData } from "@tanstack/react-query";

import type { Query } from "../../../api/client";
import { POLL, useSmo, useSmoPage } from "../../../api/hooks";
import type { Alarm, O1Endpoint } from "../../../api/types";
import { useSummary } from "../../../data/summary";

/** RAN NF alarms (O1 FaultMnS). */
export const RAN_ALARMS = "/ran-nf-oam/alarms";
/** O-Cloud infrastructure alarms (FOCOM, O2ims). */
export const OCLOUD_ALARMS = "/focom/alarms";
/** FM subscriptions: RAN NF OAM as a DME producer of RAN.FaultRecords. */
export const FM_SUBSCRIPTIONS = "/ran-nf-oam/fm-subscriptions";
/** The registered O1 endpoints (the managed elements an FM subscription can name). */
export const O1_ENDPOINTS = "/ran-nf-oam/o1-adaptor-endpoints";
/** The root-cause hint looks at alarms of the same managed element raised within this many seconds of the selected one. */
export const SAME_ELEMENT_WINDOW_S = 60;
/** How many alarms of the element the root-cause hint reads (one bounded call). */
export const SAME_ELEMENT_LIMIT = 20;

/** The server-side filters of the RAN alarm table; an empty string leaves the parameter out. */
export interface RanFilter { severity: string; managedElement: string; managedFunction: string }

/** The query of one page of RAN alarms. */
export function ranAlarmQuery(f: RanFilter, limit: number, offset: number): Query {
  return {
    severity: f.severity || undefined,
    managed_element_ref: f.managedElement.trim() || undefined,
    managed_function_ref: f.managedFunction.trim() || undefined,
    limit, offset,
  };
}

/** The true counts per severity, from the BFF summary (`alarms.critical` … `alarms.total`, `ocloudAlarms.total`). */
export function useAlarmSummary() {
  return useSummary("alarms");
}

/** One page of RAN alarms; the previous page stays on screen while the next loads. */
export function useRanAlarmPage(f: RanFilter, limit: number, offset: number) {
  return useSmoPage<Alarm>(RAN_ALARMS, ranAlarmQuery(f, limit, offset), { refetchInterval: POLL.alarms, placeholderData: keepPreviousData });
}

/** Up to {@link SAME_ELEMENT_LIMIT} alarms of one managed element, for the root-cause hint (null: nothing selected, no call). */
export function useSameElementAlarms(managedElement: string | null) {
  return useSmoPage<Alarm>(managedElement ? RAN_ALARMS : null, { managed_element_ref: managedElement ?? undefined, limit: SAME_ELEMENT_LIMIT, total: false },
    { refetchInterval: POLL.lists });
}

/** The O1 endpoints for the FM subscription form's element picker. */
export function useO1Endpoints() {
  return useSmo<O1Endpoint[]>(O1_ENDPOINTS);
}

/** The base path of one alarm's actions (`/ack`, `/clear`). */
export function alarmPath(alarmId: string): string {
  return `${RAN_ALARMS}/${alarmId}`;
}

/** The other alarms of `alarm`'s managed element raised within `windowS` seconds of it (either side), nearest first. A heuristic only: the
 * backend has no correlation yet (SCALE.md, Alarms: root-cause hint; `PR-MGT-9`). */
export function sameElementWithin(alarm: Alarm, others: Alarm[], windowS = SAME_ELEMENT_WINDOW_S): { alarm: Alarm; deltaS: number }[] {
  const at = alarm.raisedAt ? new Date(alarm.raisedAt).getTime() : NaN;
  if (Number.isNaN(at)) return [];
  return others
    .filter((o) => o.alarmId !== alarm.alarmId && o.managedElementRef === alarm.managedElementRef && o.raisedAt)
    .map((o) => ({ alarm: o, deltaS: Math.round((new Date(o.raisedAt!).getTime() - at) / 1000) }))
    .filter((x) => !Number.isNaN(x.deltaS) && Math.abs(x.deltaS) <= windowS)
    .sort((a, b) => Math.abs(a.deltaS) - Math.abs(b.deltaS));
}
