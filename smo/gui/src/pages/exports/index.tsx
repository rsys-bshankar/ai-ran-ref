/** The Exports page (route /exports, GUI-9.5b, Account group): the asynchronous CSV exports the user started from the Decisions page and Admin →
 * Audit log (an admin sees every user's), with their state, rows and size, followed every 2 s while one is running, the download of a finished
 * file (kept 24 h) and delete. Data and rules: `data/exports.ts`. Sections and known limits: README.md. */
import { Link } from "react-router-dom";

import { PageHeader } from "../../components/ui";
import { SectionBoundary } from "../../kit/SectionBoundary";
import { ExportList } from "./sections/ExportList";

/** The page. */
export function Exports() {
  return (
    <>
      <PageHeader eyebrow="Account · files" title="Exports"
        subtitle={<>CSV files written in the background from <Link to="/decisions">Decisions</Link> and the Admin audit log; each is kept 24 hours after it finishes</>} />
      <SectionBoundary id="exports.list"><ExportList /></SectionBoundary>
    </>
  );
}
