/**
 * The Account security page (route /security, every signed-in role): the one-time code set-up of the user's own account, through `TotpEnrolment`. It is the one page open to a local admin
 * who must enrol a code before anything else (the BFF's GUI_ADMIN_MFA_REQUIRED; `RequireAuth` in main.tsx redirects there and the menu shows only this entry).
 */

import { useAuth } from "../auth/AuthContext";
import { TotpEnrolment } from "../components/TotpEnrolment";
import { PageHeader } from "../components/ui";

/** Account security (PR-SEC-7): the one-time code of the signed-in local account. Also where an admin is sent when GUI_ADMIN_MFA_REQUIRED is on. */
export function Security() {
  const { me } = useAuth();
  return (
    <>
      <PageHeader title="Account security" subtitle="Your sign-in: the one-time code that follows your password, and the recovery codes behind it" />
      <TotpEnrolment required={Boolean(me?.mfaEnrolmentRequired)} />
    </>
  );
}
