/** The authenticator box of Account security (`security.authenticator`): set up, confirm or re-make the one-time code. It composes the shared
 * `components/TotpEnrolment` unchanged (setup key, confirmation, new recovery codes shown once), so its behaviour and tests stay where they are. */
import { useAuth } from "../../../auth/AuthContext";
import { TotpEnrolment } from "../../../components/TotpEnrolment";

/** The box. `required` is on for an admin the backend sends here before anything else (GUI_ADMIN_MFA_REQUIRED). */
export function Authenticator() {
  const { me } = useAuth();
  return (
    <div data-section="security.authenticator">
      <TotpEnrolment required={Boolean(me?.mfaEnrolmentRequired)} />
    </div>
  );
}
