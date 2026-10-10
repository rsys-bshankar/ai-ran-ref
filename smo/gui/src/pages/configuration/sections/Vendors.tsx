/** Configuration · vendor capabilities (`configuration.vendors`): what each RAN vendor (or digital twin) supports, paged by the server
 * (`GET /vendor-capabilities`): its MnS services, data-model conformance (SPEC, OWN or COMBINED), O1 modes and the schema descriptors its CM
 * writes are checked against. Declaring or removing a vendor is an admin's call; this box only reads (vendor onboarding stays on its API). */
import { Card, type Column } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { ServerTable } from "../../../kit/ServerTable";
import { formatTime } from "../../../lib/domain";
import { VENDORS_PATH } from "../data/queries";
import type { VendorCapability } from "../data/types";

/** The tone of a conformance mode. */
const CONFORMANCE_TONE = { SPEC: "ok", COMBINED: "info", OWN: "warn" } as const;

/** "name@revision", or "—". */
const ref = (r: { schemaName: string; revision: string } | null) => (r ? `${r.schemaName}${r.revision ? `@${r.revision}` : ""}` : null);

/** The table's columns. */
const COLUMNS: Column<VendorCapability>[] = [
  { header: "Vendor", render: (v) => <strong>{v.vendorName}</strong> },
  { header: "Services", render: (v) => v.supportedServices.join(", ") },
  { header: "Conformance", render: (v) => <Badge tone={CONFORMANCE_TONE[v.conformanceMode] ?? "mute"}>{v.conformanceMode}</Badge> },
  { header: "Modes", render: (v) => v.supportedVendorModes.join(", ") },
  { header: "Schema", render: (v) => <span className="mono small">{[ref(v.schemaRef), ref(v.specSchemaRef)].filter(Boolean).join(" + ") || "—"}</span> },
  { header: "Updated", render: (v) => <span className="small muted">{formatTime(v.updatedAt)}</span> },
];

/** The card. */
export function Vendors() {
  return (
    <Card section="configuration.vendors" title="Vendor capabilities" sub="what each RAN vendor (or digital twin) supports · onboarding is data, not code">
      <ServerTable<VendorCapability> path={VENDORS_PATH} columns={COLUMNS} rowKey={(v) => v.vendorName} empty="No vendor declared: CM writes are not checked against a data model." />
    </Card>
  );
}
