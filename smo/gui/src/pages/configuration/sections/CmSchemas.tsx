/** Configuration · CM schemas (`configuration.schemas`): the schema descriptors every config write is checked against (YANG, OPENAPI_NRM or
 * DESCRIPTOR), built-in ones first, paged by the server (`GET /cm-schemas`). Loading one is an admin's call through the API. */
import { Card, type Column } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { ServerTable } from "../../../kit/ServerTable";
import { SCHEMAS_PATH } from "../data/queries";
import type { CmSchema } from "../data/types";

/** The table's columns. */
const COLUMNS: Column<CmSchema>[] = [
  { header: "Schema", render: (s) => <strong className="mono small">{s.schemaName}</strong> },
  { header: "Type", render: (s) => <Badge tone="info">{s.type}</Badge> },
  { header: "Revision", render: (s) => <span className="mono small">{s.revision || "—"}</span> },
  { header: "Classes", render: (s) => s.classCount },
  { header: "Location", render: (s) => (s.builtin ? <span className="muted">bundled</span> : <span className="mono small">{s.location}</span>) },
];

/** The card. */
export function CmSchemas() {
  return (
    <Card section="configuration.schemas" title="CM schemas" sub="what every config write is checked against">
      <ServerTable<CmSchema> path={SCHEMAS_PATH} columns={COLUMNS} rowKey={(s) => `${s.schemaName}@${s.revision}`} empty="No CM schema loaded." />
    </Card>
  );
}
