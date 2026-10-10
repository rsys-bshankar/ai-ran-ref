/** Section `approvals.diff`: the config change a request would write, as a diff (`kit/Diff`), one block per target with its new values, cut at
 * 50 lines with a count of the rest (SCALE.md, Approvals: page a large diff). The request carries no current values, so there are no "−" lines. */
import type { ApprovalDetail } from "../../../api/types";
import { Card } from "../../../components/ui";
import { Diff } from "../../../kit/Diff";
import { changeDiff } from "../data/queries";

/** The diff card. */
export function ChangeDiff({ approval: a }: { approval: ApprovalDetail }) {
  const lines = changeDiff(a.changes);
  return (
    <Card section="approvals.diff" title={`What it would write (${a.changes.length})`} sub={`O1 config job · ${a.managedElements.length} managed element${a.managedElements.length === 1 ? "" : "s"}`}>
      <Diff lines={lines} label="Changes" />
      {a.accessScope && <p className="muted small">Access scope: {a.accessScope}</p>}
      <p className="gap-note">The current values are not sent with the request, so only the new ones show.</p>
    </Card>
  );
}
