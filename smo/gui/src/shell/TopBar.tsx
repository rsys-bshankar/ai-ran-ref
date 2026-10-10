/** The top bar (64 px, BRIEF §2, handoff `Topbar.dc.html`): breadcrumb (section / page), the scope picker (GUI-9.3, `ScopePicker.tsx`), the ⌘K jump search, a live-data chip, the
 * notifications button (critical alarms and waiting approvals from the "nav" summary, with a short list of where to go), the moon button to the
 * Preferences page, and help (keyboard shortcuts and where the runbook lives). The live chip says "Live · pushed" while the summary stream
 * (`shell/LiveEvents.tsx`, SCALE.md P7) is open, and "Live · polling" while it is not (connecting, refused, or a browser without EventSource). */
import { useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { useLive } from "../data/events";
import { count, useSummary } from "../data/summary";
import { Icon } from "../kit/icons";
import { formatCount } from "../kit/Kpi";
import { GlobalSearch } from "./GlobalSearch";
import { crumbOf } from "./nav";
import { ScopePicker } from "./ScopePicker";

/** The notifications button and its popover. */
function Notifications() {
  const nav = useSummary("nav", { refetchInterval: 30_000 });
  const [open, setOpen] = useState(false);
  const critical = count(nav.data, "alarms.critical");
  const approvals = count(nav.data, "approvals.PENDING");
  const halted = (count(nav.data, "configJobs.HALTED") ?? 0) + (count(nav.data, "campaigns.HALTED") ?? 0);
  const total = (critical ?? 0) + (approvals ?? 0) + halted;
  const rows = [
    { n: critical, text: "critical RAN alarms", to: "/alarms", tone: "d-bad" },
    { n: approvals, text: "approval requests waiting", to: "/approvals", tone: "d-warn" },
    { n: count(nav.data, "configJobs.HALTED"), text: "config jobs halted", to: "/configuration", tone: "d-warn" },
    { n: count(nav.data, "campaigns.HALTED"), text: "software campaigns halted", to: "/software", tone: "d-bad" },
  ].filter((r) => (r.n ?? 0) > 0);
  return (
    <div className="rel">
      <button type="button" className="btn icon" aria-label={`Notifications, ${total} need attention`} aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="bell" />
        {total > 0 && <span className="icon-count">{formatCount(total, 99)}</span>}
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Notifications">
          <strong>Needs attention</strong>
          {rows.length === 0 ? <span className="muted small">Nothing waiting on you.</span> : rows.map((r) => (
            <Link key={r.to} to={r.to} className="row" onClick={() => setOpen(false)}><span className={`dot ${r.tone}`} /><span className="grow">{formatCount(r.n)} {r.text}</span>→</Link>
          ))}
          {nav.data && nav.data.partial.length > 0 && <span className="xs muted">Not counted: {nav.data.partial.join(", ")} did not answer.</span>}
        </div>
      )}
    </div>
  );
}

/** The help button and its popover. */
function Help() {
  const [open, setOpen] = useState(false);
  return (
    <div className="rel">
      <button type="button" className="btn icon" aria-label="Help" aria-expanded={open} onClick={() => setOpen(!open)}><Icon name="help" /></button>
      {open && (
        <div className="popover" role="dialog" aria-label="Help">
          <strong>Shortcuts</strong>
          <span className="small"><kbd>⌘K</kbd> / <kbd>Ctrl K</kbd> jump to a page, flow, element or id</span>
          <span className="small"><kbd>Esc</kbd> close a panel or dialog</span>
          <strong>Guides</strong>
          <span className="small muted">The live walkthrough is <code>smo/DEMO_RUNBOOK.md</code>; every lifecycle flow has a board under <Link to="/flows" onClick={() => setOpen(false)}>Lifecycle flows</Link>.</span>
        </div>
      )}
    </div>
  );
}

/** The top bar. */
export function TopBar() {
  const { pathname } = useLocation();
  const [section, page] = crumbOf(pathname);
  const { connected } = useLive();
  return (
    <header className="topbar">
      <nav className="crumb" aria-label="Breadcrumb"><span>{section}</span><span aria-hidden>/</span><strong aria-current="page">{page}</strong></nav>
      <ScopePicker />
      <GlobalSearch />
      <div className="topbar-tools">
        {connected
          ? <span className="chip" title="Counts are pushed by the server as they change"><span className="dot d-ok" />Live · pushed</span>
          : <span className="chip" title="Pages refresh on an interval while visible; a hidden tab does not poll"><span className="dot d-warn" />Live · polling</span>}
        <Notifications />
        <Link className="btn icon" to="/preferences" aria-label="Appearance and preferences" title="Theme, text size, accent colour"><Icon name="moon" /></Link>
        <Help />
      </div>
    </header>
  );
}
