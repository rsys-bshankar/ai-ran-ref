/** The three status tiles of Account security (`security.tiles`, handoff `Security.dc.html`): whether the one-time code is on, how many recovery
 * codes are left, and how this user signs in (local account or the identity provider). Read from `/api/me` (the session) and `/api/me/totp`. */
import { useAuth } from "../../../auth/AuthContext";
import { Kpi } from "../../../kit/Kpi";
import { useTotpStatus } from "../data/queries";

/** The tiles. A value not known yet (or a status that failed to load: the recovery box shows that error with a retry) reads "—". */
export function StatusTiles() {
  const { me } = useAuth();
  const status = useTotpStatus();
  const s = status.data;
  const sso = me?.local === false || s?.reason === "identity provider";
  const twoStep = sso ? "By your provider" : !s ? null : s.enrolled ? "On" : s.pending ? "Started" : "Off";
  const twoStepFoot = sso ? "the identity provider asks for the second factor"
    : !s ? undefined : s.enrolled ? "authenticator app · after your password" : !s.available ? "not available on this server" : "set it up below";
  return (
    <div className="grid g3" data-section="security.tiles">
      <Kpi label="Two-step sign-in" value={twoStep} foot={twoStepFoot} tone={!sso && s && !s.enrolled ? "warm" : s?.enrolled ? "volt" : undefined} />
      <Kpi label="Recovery codes left" value={sso || !s ? null : s.enrolled ? s.recoveryCodesLeft : null}
        foot={sso ? "not used with single sign-on" : s?.enrolled ? (s.recoveryCodesLeft <= 2 ? "running low: make new ones below" : "each works once") : "made when the code is set up"}
        tone={s?.enrolled && s.recoveryCodesLeft <= 2 ? "warm" : undefined} />
      <Kpi label="Sign-in method" value={me ? (sso ? "Single sign-on" : "Local account") : null}
        foot={me ? <>signed in as <strong>{me.username}</strong> · {me.role}</> : undefined} />
    </div>
  );
}
