/** Software · campaign controls (part of `software.detail`): Continue, Halt, Roll back and Abort, offered only in the states the backend
 * accepts them (`campaignActions`), each a role-gated `ActionButton` (POST `/software-campaigns/{id}/<action>`, operator in gui-bff/app/rbac.py).
 * Roll back and Abort ask first; a continue during an unexpired pause between waves asks too and sends `force`. */
import { ActionButton } from "../../../components/ui";
import { campaignPath } from "../data/queries";
import { campaignActions, type Campaign, type CampaignAction } from "../data/types";

/** Button text, tone, the question asked first and the toast of each action. */
const LOOK: Record<CampaignAction, { label: string; tone: "primary" | "default" | "danger"; confirm?: string; success: string }> = {
  continue: { label: "Continue", tone: "primary", success: "Campaign continued" },
  halt: { label: "Halt", tone: "default", success: "Campaign halted after its current wave" },
  rollback: { label: "Roll back", tone: "danger", confirm: "Roll back this campaign? Every element it upgraded gets a revert software job.", success: "Rollback started" },
  abort: { label: "Abort", tone: "danger", confirm: "Abort this campaign? The waves that ran stay as they are; the others never run.", success: "Campaign aborted" },
};

/** The buttons of one campaign, or nothing in a state that takes no action. */
export function CampaignActions({ campaign }: { campaign: Pick<Campaign, "campaignId" | "status" | "haltedReason" | "nextWaveAt" | "wave" | "rollbackOrder"> }) {
  const actions = campaignActions(campaign);
  if (actions.length === 0) return null;
  return (
    <div className="row wrap">
      {actions.map(({ action, force }) => {
        const look = LOOK[action];
        const label = action === "continue" ? (campaign.haltedReason === "GATE_FAILED" ? "Continue anyway" : force ? "Continue now" : "Continue")
          : action === "rollback" && campaign.wave > 0 ? `Roll back waves 1–${campaign.wave}` : look.label;
        return (
          <ActionButton key={action} label={label} tone={look.tone}
            confirm={force ? "The pause between waves has not elapsed. Start the next wave now?"
              : action === "rollback" ? `Roll back this campaign? Every element it upgraded gets a revert software job, ${campaign.rollbackOrder === "reverse" ? "the last wave first, then each earlier wave" : "all at once"}. It is refused while a job of the campaign is still running.`
              : look.confirm}
            action={{ method: "POST", path: campaignPath(campaign.campaignId, action), json: { force }, success: look.success }} />
        );
      })}
    </div>
  );
}
