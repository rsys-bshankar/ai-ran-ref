/** The Account security page (route /security, PR-SEC-7, handoff `Security.dc.html`): the one-time code of the signed-in local account and the
 * recovery codes behind it. Also where an admin is sent when GUI_ADMIN_MFA_REQUIRED is on. Sections and known limits: README.md. */
import { PageHeader } from "../../components/ui";
import { SectionBoundary } from "../../kit/SectionBoundary";
import { Authenticator } from "./sections/Authenticator";
import { RecoveryCodes } from "./sections/RecoveryCodes";
import { SignIns } from "./sections/SignIns";
import { StatusTiles } from "./sections/StatusTiles";

/** The page. */
export function Security() {
  return (
    <>
      <PageHeader eyebrow="Your sign-in" title="Account security" subtitle="Your sign-in: the one-time code that follows your password, and the recovery codes behind it" />
      <div className="stack">
        <SectionBoundary id="security.tiles"><StatusTiles /></SectionBoundary>
        <SectionBoundary id="security.authenticator"><Authenticator /></SectionBoundary>
        <div className="grid g2">
          <SectionBoundary id="security.recovery"><RecoveryCodes /></SectionBoundary>
          <SectionBoundary id="security.signins"><SignIns /></SectionBoundary>
        </div>
      </div>
    </>
  );
}
