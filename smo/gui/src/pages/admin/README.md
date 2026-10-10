# Admin

Route: `/admin` (admin role)    Design: handoff `Admin.dc.html`

Tabs (URL hash): `#users`, `#audit`, `#msac`. Only the visible tab's queries run.

## Sections

| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| admin.users | sections/UsersTable.tsx (+ UserDialogs.tsx) | users: avatar, role (editable), sign-in method, status, one-time code, break-glass, last active (gap), actions; Add user, Reset password | `GET/POST/PATCH/DELETE /api/admin/users…`, `/revoke-sessions`, `/reset-totp` | on change | 1 call |
| admin.roles | sections/RoleMatrix.tsx | what viewer / operator / admin may do | — (fixed, mirrors gui-bff/app/rbac.py) | — | 0 |
| admin.audit | sections/AuditLog.tsx | audit log, newest first, filters by user and event, HTTP outcome badge, server paging | `GET /api/admin/audit?username&action&limit&offset` | 10 s | 1 call/page |
| admin.msac | sections/MsacTab.tsx | RAN NF OAM MSAC roles, identities, access rules (server tables) | `/ran-nf-oam/msac/roles`, `/identities`, `/access-rules` | 15 s | 3 calls |

## Known limits

- **Last active** (BRIEF §5): the BFF records no last activity per user; the column reads "—".
- The users route returns the whole list (no paging or search on the server); fine for tens to hundreds of users.
- The audit route takes no time range and has no export job: the mockup's 24 h / 7 d range and Export are not offered.
- MSAC is read-only from the console: the BFF's permission table (gui-bff/app/rbac.py) has no rule for MSAC writes, so the role-gated Delete
  buttons render for nobody until one is added. Create / edit forms are not built for the same reason.

## Troubleshooting

- The MSAC tables show an error: RAN NF OAM did not answer; check `/modules/status`.
- A user change shows "FORBIDDEN": the BFF re-checks the admin role on every call; the session may have been downgraded.
