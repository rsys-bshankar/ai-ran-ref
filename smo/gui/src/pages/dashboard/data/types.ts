/** View types of the Dashboard that `api/types.ts` does not hold: a managed element as `GET /ran-nf-oam/managed-entities` answers it
 * (ran-nf-oam/app/vendors.py `_me_view`), used by the network health map's region tiles and drill-down. */

/** A managed element (or one of its functions). `region` and `tenant` are null when not set (PR-SEC-10.2). There is no cluster or site field. */
export interface ManagedEntity {
  managedElementRef: string; managedFunctionRef: string | null; entityType: string | null; vendorName: string | null; o1Protocol: string | null;
  o1AdaptorEndpointId: string | null; supportedServices: string[]; conformanceMode: string | null; region: string | null; tenant: string | null;
}
