/** The recovery codes box of Account security (`security.recovery`). The backend tells only how many codes are left (`recoveryCodesLeft`), never the
 * codes again nor which ones were used, so this box shows the count and one slot per remaining code; the codes themselves appear once, in the
 * authenticator box, right after enrolment or "Generate new recovery codes". */
import { Card } from "../../../components/ui";
import { Empty, QueryState } from "../../../kit/states";
import { recoveryLeftText } from "../../../lib/mfa";
import { useTotpStatus } from "../data/queries";

/** The box; hidden for an identity-provider user (no codes here). */
export function RecoveryCodes() {
  const status = useTotpStatus();
  if (status.data?.reason === "identity provider") return null;
  return (
    <Card section="security.recovery" title="Recovery codes" sub="each signs you in once if you lose your device">
      <QueryState q={status} isEmpty={() => false}>
        {status.data && !status.data.enrolled ? (
          <Empty title="No recovery codes yet.">They are made when you set up the one-time code.</Empty>
        ) : status.data && (
          <>
            <p className="small">{recoveryLeftText(status.data.recoveryCodesLeft)}</p>
            <ul className="recovery-codes" aria-label={`${status.data.recoveryCodesLeft} unused recovery codes`}>
              {Array.from({ length: Math.min(status.data.recoveryCodesLeft, 20) }, (_, i) => (
                <li key={i}><code aria-hidden>••••-••••-••••-••••</code></li>
              ))}
            </ul>
            <p className="gap-note">The server does not show the codes again or which were used; save them when they are shown, or make new ones above.</p>
          </>
        )}
      </QueryState>
    </Card>
  );
}
