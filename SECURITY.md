# Security Policy

## Scope and intended use

This repository is a **reference implementation** of an O-RAN SMO platform
(`smo/`) together with the 3GPP and O-RAN specifications it is grounded on
(`specs/`). It is meant for learning, integration testing and demos. It has not
been hardened or audited for production, and it has never been exposed to an
untrusted network. Do not deploy it as-is on the public internet or in front of
a live RAN.

## Supported versions

There are no numbered releases. Only the tip of the default branch (`main`) is
maintained, and security fixes land there. Older commits and forks receive no
backports.

| Version | Supported |
| ------- | --------- |
| `main` (latest commit) | :white_check_mark: |
| Anything else | :x: |

## Reporting a vulnerability

**Please do not open a public issue or pull request for a security problem.**

Report it privately through GitHub: open the repository's **Security** tab and
choose **Report a vulnerability** (GitHub private vulnerability reporting), or go
straight to
<https://github.com/rsys-bshankar/ai-ran-ref/security/advisories/new>.
If that option is not available to you, open an issue that says only that you
have a security report to make, with no details, and a maintainer will arrange
a private channel.

Please include:

- what you found and which module or file it is in (for example `r1-termination`,
  `gui-bff`, `sme`);
- the steps or request that reproduce it, and the commit you tested;
- the impact as you see it.

What to expect (best-effort targets, since this is a maintained reference
project and not a product with an SLA):

| Step | Target |
|---|---|
| Acknowledgement of your report | within 5 working days |
| First assessment (accepted, declined, or need more information) | within 14 days |
| Fix or mitigation for an accepted report | depends on severity; you are kept informed |

If the report is accepted, we fix it on `main`, credit you in the advisory
unless you prefer otherwise, and publish a GitHub Security Advisory once the fix
is available. If it is declined (for example because it is a documented
limitation below, or is not reproducible), we say why.

## Security model of the SMO reference implementation

What is in place, so you can judge what counts as a vulnerability:

- **R1 gateway authentication.** Every call from an rApp goes through R1
  Termination, which introspects the bearer token against SME's OAuth2 issuer
  (RFC 7662) before proxying. Only `/health` and `/bootstrap` are exempt at the
  gateway; tokens are issued by SME directly.
  See [`smo/docs/ARCHITECTURE.md`](smo/docs/ARCHITECTURE.md#r1-api-conventions).
- **Network exposure.** In `docker-compose.yml` only R1 Termination (`:8080`),
  the GUI (`:3000`) and Postgres (`:5432`) publish host ports. The A1 Near-RT
  RIC test double sits on an isolated network reachable only from `a1-related`.
- **Operator GUI.** Session JWT in an `HttpOnly; Secure; SameSite=Strict`
  cookie, CSRF double-submit on unsafe methods, role checks (viewer / operator /
  admin) re-read on every request, account lockout after repeated failures, an
  append-only audit log, a strict CSP, and a proxy that never forwards browser
  credentials to R1. Details: [`smo/gui/README.md`](smo/gui/README.md#security).
- **Outbound callbacks.** Any caller-supplied callback URL is called through
  one helper (`smo_shared.webhook`); RAN NF OAM vendor discovery never fetches a
  URL taken from a request body.
- **Secrets.** The GUI admin password is generated at first start when
  `GUI_ADMIN_PASSWORD` is unset and written to a `0600` file; `GUI_JWT_SECRET`
  is random per boot unless set. The only committed credential is the demo
  Postgres password (see below).

## Known limitations (not vulnerabilities)

These are deliberate Phase 1 scope cuts. Reports that only restate them are
declined, but a way to exploit one in a surprising way is welcome.

- The compose file ships **default demo credentials** for Postgres
  (`smo` / `smo`) and publishes `:5432`. Change them and unpublish the port for
  anything beyond a laptop demo.
- **No TLS** is terminated inside the stack. The GUI cookie is marked `Secure`,
  so put a TLS-terminating proxy in front of it; R1 Termination at `:8080` is
  plain HTTP.
- Module-to-module traffic on the compose network is unauthenticated apart from
  the R1 token check at the gateway; there is no mTLS between services.
- OAuth2 tokens are opaque and introspected. SME checks a token's `scope`
  against the published APIs when it issues the token, but R1 Termination
  checks only that a token is active, not what its scope allows. An invoker
  onboarded with a PEM key can authenticate with an RFC 7523 signed client
  assertion; SMO's own modules authenticate with their onboarding secret
  (`OI-2-oauth2-scope`, `SA-SME-1-public-key` in
  [`smo/HISTORY.md`](smo/HISTORY.md)).
- Onboarding's package signature check is an internal-consistency check, not
  verification against a trust anchor.
- The O1 transports are not hardened: RAN NF OAM speaks RFC 6241-shaped
  NETCONF `edit-config` and RFC 8040 RESTCONF over plain HTTP, without TLS or
  authentication, to the adaptor, and the stack has not been run against a real
  RAN.
- Backend RBAC exists only in `gui-bff`; R1 service APIs authenticate callers
  but do not authorise per resource.

The authoritative, current list of open items is
[`smo/OPEN_ITEMS.md`](smo/OPEN_ITEMS.md).

## Dependencies

The Python services install their dependencies unpinned from PyPI
(`smo/Dockerfile`); the GUI is locked by `smo/gui/package-lock.json`. No
Dependabot configuration is committed. Enabling GitHub Dependabot alerts, code
scanning and secret scanning on the repository is recommended, and so is pinning
the Python dependencies before any non-demo use.

## Specifications in `specs/`

The files under `specs/` are reference copies of third-party standards material
(3GPP, O-RAN). Report issues in their content to the originating standards body, not
here.
