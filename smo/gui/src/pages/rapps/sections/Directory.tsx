/** The rApp directory (PR-GUI-8, GUI-8.4, 8.5): every rApp instance in one searchable list served by the BFF (`/api/rapps`: search, state, owner,
 * has-a-page and pinned filters, server paging). Each row opens the rApp's own page (/rapps/<instance>); the star pins it to the sidebar (at most
 * MAX_PINS per user, kept by the GUI backend). What a rApp's page shows is declared in its package, so a rApp onboarded at run time is in this
 * list and has its page without a GUI build. Section id `rapps.directory`; scripts/gui_rapp_pages_e2e.py reads its markup. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { MAX_PINS, usePinToggle, usePins, useRappDirectory, type RappSummary } from "../../../api/rapps";
import { Card, DataTable, Id, StateBadge } from "../../../components/ui";
import { Pager } from "../../../kit/Pager";

/** The default page size. */
const PAGE = 25;

/** The directory card. */
export function RappDirectory() {
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  const [state, setState] = useState("");
  const [owner, setOwner] = useState("");
  const [page, setPage] = useState<"" | "true" | "false">("");
  const [pinnedOnly, setPinnedOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => { const t = setTimeout(() => { setSearch(text.trim()); setOffset(0); }, 250); return () => clearTimeout(t); }, [text]);
  const directory = useRappDirectory({ search, state, owner, hasPage: page === "" ? "" : page === "true", pinned: pinnedOnly ? true : "", limit, offset });
  const pins = usePins();
  const toggle = usePinToggle();
  const full = (pins.data?.items.length ?? 0) >= MAX_PINS;
  const data = directory.data;
  const reset = <T,>(set: (v: T) => void) => (v: T) => { set(v); setOffset(0); };
  return (
    <Card section="rapps.directory" title="rApp directory" sub="every rApp and the page its package declares"
      actions={<span className="muted small">{data ? `${data.total} rApp${data.total === 1 ? "" : "s"}` : ""}</span>}>
      <div className="row gap wrap filters" role="search">
        <input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search name, owner, version or id" aria-label="Search rApps" />
        <select value={state} onChange={(e) => reset(setState)(e.target.value)} aria-label="Filter by state">
          <option value="">All states</option>{(data?.states ?? []).map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={owner} onChange={(e) => reset(setOwner)(e.target.value)} aria-label="Filter by owner">
          <option value="">All owners</option>{(data?.owners ?? []).map((o) => <option key={o}>{o}</option>)}
        </select>
        <select value={page} onChange={(e) => reset(setPage)(e.target.value as "" | "true" | "false")} aria-label="Filter by page">
          <option value="">With or without a page</option><option value="true">Declares a page</option><option value="false">Overview only</option>
        </select>
        <label className="row gap small"><input type="checkbox" checked={pinnedOnly} onChange={(e) => reset(setPinnedOnly)(e.target.checked)} /> Pinned only</label>
      </div>
      <DataTable<RappSummary> rows={data?.items} loading={directory.isLoading} error={directory.error} rowKey={(r) => r.instanceId}
        empty={search || state || owner || page || pinnedOnly ? "No rApp matches." : "No rApp instances yet. Onboard a package and deploy it under Packages."}
        columns={[
          { header: "rApp", render: (r) => <><Link to={`/rapps/${r.instanceId}`}><strong>{r.name ?? "(unknown package)"}</strong></Link> <span className="muted">{r.version ?? ""}</span></> },
          { header: "Owner", render: (r) => r.vendor ?? <span className="muted">—</span> },
          { header: "Instance", render: (r) => <Id value={r.instanceId} /> },
          { header: "State", render: (r) => <StateBadge state={r.state} /> },
          { header: "Autonomy", render: (r) => <StateBadge state={r.autonomyMode} /> },
          { header: "Page", render: (r) => (r.hasPage ? (r.operatorApiRegistered ? "declared" : <span className="muted">declared, API not registered</span>) : <span className="muted">overview only</span>) },
          { header: "", className: "actions", render: (r) => (
            <button type="button" className="btn ghost small" aria-pressed={r.pinned} disabled={toggle.isPending || (!r.pinned && full)}
              title={r.pinned ? "Unpin from the sidebar" : full ? `At most ${MAX_PINS} pinned: unpin one first` : "Pin to the sidebar"}
              aria-label={`${r.pinned ? "Unpin" : "Pin"} ${r.name ?? r.instanceId}`}
              onClick={() => toggle.mutate({ instanceId: r.instanceId, pin: !r.pinned })}>{r.pinned ? "★" : "☆"}</button>) },
        ]} />
      {data && <Pager offset={offset} limit={limit} shown={data.items.length} total={data.total} onOffset={setOffset} onLimit={(l) => { setLimit(l); setOffset(0); }} />}
      <p className="gap-note">Grouping by category is not possible yet: a package declares no category the backend serves.</p>
    </Card>
  );
}
