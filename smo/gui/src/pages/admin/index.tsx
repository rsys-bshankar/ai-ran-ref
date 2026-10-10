/** The Admin page (route /admin, handoff `Admin.dc.html`): GUI users and roles (held by the BFF, not the SMO), the audit log of every mutating call,
 * and RAN NF OAM's access control (MSAC). Tabs live in the URL hash (`#users`, `#audit`, `#msac`); only the visible tab's queries run. Sections and
 * known limits: README.md. */
import { PageHeader, Tabs, useHashTab } from "../../components/ui";
import { SectionBoundary } from "../../kit/SectionBoundary";
import { AuditLog } from "./sections/AuditLog";
import { MsacTab } from "./sections/MsacTab";
import { RoleMatrix } from "./sections/RoleMatrix";
import { UsersTable } from "./sections/UsersTable";

const TABS = ["users", "audit", "msac"] as const;

/** The page. */
export function Admin() {
  const [tab, setTab] = useHashTab(TABS, "users");
  return (
    <>
      <PageHeader eyebrow="GUI users · held by the BFF" title="Admin" subtitle="GUI users and roles (held by the BFF, not the SMO), and the audit log of every mutating call" />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "users", label: "Users & roles" }, { id: "audit", label: "Audit log" }, { id: "msac", label: "RAN access control (MSAC)" }]} />
      {tab === "users" && (
        <div className="grid g3">
          <div className="s2"><SectionBoundary id="admin.users"><UsersTable /></SectionBoundary></div>
          <SectionBoundary id="admin.roles"><RoleMatrix /></SectionBoundary>
        </div>
      )}
      {tab === "audit" && <SectionBoundary id="admin.audit"><AuditLog /></SectionBoundary>}
      {tab === "msac" && <SectionBoundary id="admin.msac"><MsacTab /></SectionBoundary>}
    </>
  );
}
