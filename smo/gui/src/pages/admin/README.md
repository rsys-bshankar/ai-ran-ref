# Admin

Route: `/admin` (admin role)    Design: handoff `Admin.dc.html`

Tabs (URL hash): `#users`, `#audit`, `#msac`. Only the visible tab's queries run.

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| admin.users | sections/UsersTable.tsx (+ UserDialogs.tsx) | users: avatar, role (editable), sign-in method, status, one-time code, break-glass, last active, last sign-in, actions; Add user, Reset password | `GET/POST/PATCH/DELETE /api/admin/users…`, `/revoke-sessions`, `/reset-totp` | on change | 1 call |
| admin.roles | sections/RoleMatrix.tsx | what viewer / operator / admin may do | — (fixed, mirrors gui-bff/app/rbac.py) | — | 0 |
| admin.audit | sections/AuditLog.tsx | audit log, newest first, filters by user, event, range (24 h / 7 d default / 30 d / all) and until, HTTP outcome badge, keyset paging (Newer / Older), Export CSV link | `GET /api/admin/audit?username&action&since&until&limit&after_id`; link `/api/admin/audit.csv?…` | 10 s (newest page) | 1 call/page |
| admin.msac | sections/MsacTab.tsx (+ MsacForms.tsx) | RAN NF OAM MSAC roles, identities, access rules (server tables); admin: New role / identity / rule forms, Delete | `/ran-nf-oam/msac/roles`, `/identities`, `/access-rules`; POST / DELETE on each | 15 s | 3 calls (+2 picker reads when the forms render) |

## Known limits

- **Last active** is the user's newest audit row (sign-ins, changes, refusals); plain reads are not audited, so a user who only looked reads older.
- The users route returns the whole list (no paging or search on the server); fine for tens to hundreds of users.
- The audit log's "Older" pages by id (keyset); the total is shown on the newest page only. The CSV export is a synchronous streamed download
  (at most 1,000,000 rows), not an async export job.
- MSAC: roles, identities and rules can be created and deleted here (admin); editing one in place (`PUT`) is API-only.

## Troubleshooting

- The MSAC tables show an error: RAN NF OAM did not answer; check `/modules/status`.
- A user change shows "FORBIDDEN": the BFF re-checks the admin role on every call; the session may have been downgraded.
