/** View types of the Dashboard that `api/types.ts` does not hold: a managed element as `GET /ran-nf-oam/managed-entities` answers it
 * (ran-nf-oam/app/vendors.py `_me_view`), the fleet health and worst-element aggregates (ran-nf-oam/app/fleet.py) and one hourly alarm bucket
 * (`GET /alarms/counts?group_by=hour`). */

/** A managed element (or one of its functions). `region`, `siteCluster` and `tenant` are null when not set (PR-SEC-10.2, PR-GUI-9.8). */
export interface ManagedEntity {
  managedElementRef: string; managedFunctionRef: string | null; entityType: string | null; vendorName: string | null; o1Protocol: string | null;
  o1AdaptorEndpointId: string | null; supportedServices: string[]; conformanceMode: string | null; region: string | null; tenant: string | null;
  siteCluster?: string | null;
}

/** A graded alarm severity, or null when a group has no open graded alarm. */
export type WorstSeverity = "critical" | "major" | "minor" | "warning" | null;

/** One group of the health map: a region or a site cluster (`key` null: elements without one). */
export interface HealthGroup { key: string | null; elements: number; unhealthy: number; worstSeverity: WorstSeverity }

/** `GET /managed-entities/health`: the groups and the score over all of them (null when there are no elements). */
export interface FleetHealth { groupBy: "region" | "site_cluster"; groups: HealthGroup[]; healthScore: number | null }

/** One row of `GET /managed-entities/worst`. */
export interface WorstElement { managedElementRef: string; region: string | null; siteCluster: string | null; critical: number; major: number; openAlarms: number }

/** One hour of `GET /alarms/counts?group_by=hour`: its start (UTC), the alarms raised in it, and their graded severities. */
export interface AlarmHour { key: string; count: number; bySeverity: { critical: number; major: number; minor: number; warning: number } }
