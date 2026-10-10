# Account security

Route: `/security`    Design: handoff `Security.dc.html` (PR-SEC-7)

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| security.tiles | sections/StatusTiles.tsx | two-step sign-in on/off, recovery codes left, sign-in method (local / single sign-on) | `/api/me` (session), `GET /api/me/totp` | on change | shared |
| security.authenticator | sections/Authenticator.tsx | set up / confirm the one-time code, make new recovery codes (`components/TotpEnrolment`) | `/api/me/totp/begin`, `/confirm`, `/recovery-codes` | on change | shared |
| security.recovery | sections/RecoveryCodes.tsx | how many recovery codes are left, one slot each | `GET /api/me/totp` | on change | 1 call (shared key `["bff","totp"]`) |
| security.signins | sections/SignIns.tsx | gap note | — | — | 0 |

## Known limits

- **Recent sign-ins** (BRIEF §5 "sign-in history"): no per-user route; sign-ins are only in the admin audit log. The box shows a gap note.
- The server returns the recovery codes once and only their count afterwards, so used codes cannot be struck through and the codes cannot be
  downloaded again; the box shows the count.
- Moving to a new device: an administrator resets the one-time code (Admin → Users), then the user sets it up again (the BFF has no self re-enrol).
- Password age and "change password" from the mockup: not shown (no password age is served).

## Troubleshooting

- "One-time codes are not set up on this server": the BFF needs `GUI_TOTP_KEY` (or `GUI_TOTP_KEY_FILE`).
- An admin lands here and cannot leave: `GUI_ADMIN_MFA_REQUIRED` is on and the account has no one-time code yet.
