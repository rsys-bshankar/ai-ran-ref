/** View types of the Admin page that `api/types.ts` does not hold: the MSAC objects of RAN NF OAM (`GET /ran-nf-oam/msac/roles`, `/identities`,
 * `/access-rules`; ran-nf-oam/app/msac.py `_role_view`, `_identity_view`, `_rule_view`). Each is `{id, attributes}` as 3GPP TS 28.532 MSAC shapes it. */

/** An MSAC role: a name and the access rules it grants. */
export interface MsacRole { id: string; attributes: { roleName: string; accessRulesList: string[] } }

/** The identity types MSAC knows. */
export type IdentityType = "USERNAME" | "EMAIL_ADDRESS" | "PHONE_NUMBER" | "IP_ADDRESS" | "MACHINEUSER";

/** An MSAC identity: who (by type and name) holds which roles. The credential is never returned. */
export interface MsacIdentity { id: string; attributes: { identityType: IdentityType; identityName: string; roleList: string[] } }

/** An MSAC access rule: ALLOW or DENY of some operations on the data nodes a selector picks. */
export interface MsacAccessRule {
  id: string;
  attributes: { ruleName: string; dataNodeSelector: string; operations: string[]; actions: "ALLOW" | "DENY"; componentCData?: unknown[] | null };
}
