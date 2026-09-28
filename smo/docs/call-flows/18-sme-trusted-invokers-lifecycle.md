# Call Flow: SME CAPIF Security Lifecycle — Onboard → Trust → Token → Introspect → Update

Stitches together `OPEN_ITEMS.md` sections 2 and 5, and `SPEC_AUDIT.md`'s SME security
findings: Provider (APF) enrolment, API Invoker onboarding, the real CAPIF trust-context
(`TrustedInvoker`) registry, and the `/oauth2/token`+`/oauth2/introspect` pair R1
Termination's own advertised `tokenEndPoint` actually needs — all built, none of it ever
shown in a call flow before this one, despite being exactly the security surface every
other flow's `Container->>R1: obtain OAuth2.0 token` line (call flow 01) glosses over.

```mermaid
sequenceDiagram
    actor Vendor as rApp Vendor (out-of-band)
    actor Container as rApp container
    participant SME as SME
    participant R1 as R1 Termination
    actor AEF as (conceptual) AEF — no real one exists in this build

    rect rgb(240, 255, 240)
    Note over Vendor,SME: Provider enrolment — out-of-band, once per platform
    Vendor->>SME: POST /provider-registrations (apfId, providerDomainInfo?)
    SME-->>Vendor: apfId
    end

    rect rgb(240, 248, 255)
    Note over Container,SME: Invoker onboarding — the real trust direction: server mints identity, not the client
    Container->>SME: POST /invoker-registrations (apiInvokerPublicKey)
    Note over SME: apiInvokerId is NEVER client-supplied — the real CAPIF onboarding<br/>flow "shall not" accept one — SME generates both the id and the<br/>onboarding secret and returns them
    SME-->>Container: apiInvokerId, onboardingSecret
    end

    rect rgb(255, 240, 240)
    Note over Container,SME: Trust context — gated on the invoker already being onboarded
    Container->>SME: PUT /trusted-invokers/{apiInvokerId}<br/>(notificationDestination, securityInfo: [{aefId?, apiId?, prefSecurityMethods}])
    alt apiInvokerId never onboarded
        SME-->>Container: 400 INVOKER_NOT_REGISTERED
    else onboarded
        SME->>SME: selSecurityMethod = securityInfo[0].prefSecurityMethods[0]<br/>(honest adaptation — no real AEF-side capability catalog<br/>exists in this build to cross-check against, unlike the reference)
        SME-->>Container: TrustedInvoker{securityInfo: [...with selSecurityMethod]}
    end
    end

    rect rgb(255, 250, 230)
    Note over Container,R1: Token issuance and introspection — R1's own tokenEndPoint, finally real
    Container->>SME: POST /oauth2/token (grant_type=client_credentials, client_id=apiInvokerId, client_secret=onboardingSecret)
    SME->>SME: IsInvokerRegistered? VerifyInvokerSecret? — both real checks,<br/>400 on either failure, matching the reference exactly
    SME-->>Container: access_token, expires_in, token_type=Bearer
    Note over SME: an opaque, server-tracked token — this build has no real IdP<br/>to delegate JWT signing to, unlike the reference's own Keycloak

    Container->>R1: GET /some-r1-route (Authorization: Bearer access_token)
    R1->>SME: POST /oauth2/introspect (token)
    Note over R1,SME: unauthenticated internal call — SME<->R1 traffic never<br/>leaves the docker-compose network, same reasoning /bootstrap<br/>itself already gives for staying unauthenticated
    SME-->>R1: active=true, client_id=apiInvokerId, exp
    R1-->>Container: (request forwarded)
    end

    rect rgb(250, 240, 255)
    Note over AEF,SME: Read with redaction, update, deregister
    AEF->>SME: GET /trusted-invokers/{apiInvokerId}
    SME-->>AEF: securityInfo with authenticationInfo/authorizationInfo redacted to ""<br/>(the real reference's own default — raw secrets are opt-in via<br/>explicit query params, never handed out by default)
    AEF->>SME: GET /trusted-invokers/{apiInvokerId}?authentication_info=true
    SME-->>AEF: securityInfo with authenticationInfo now populated

    Container->>SME: POST /trusted-invokers/{apiInvokerId}/update (revised securityInfo)
    Note over SME: unlike the PUT above, an update never re-checks invoker<br/>registration — only that a trust context already exists
    SME-->>Container: updated TrustedInvoker

    Container->>SME: DELETE /trusted-invokers/{apiInvokerId}
    Note over SME: idempotent — matches every other deregister route in this build
    SME-->>Container: 204
    end
```

**Key decisions this flow depends on:**
- Invoker onboarding flips the trust direction from what a naive implementation would do: the client supplies only its public key; the server generates and returns both `apiInvokerId` and `onboardingSecret`. A self-asserted invoker identity or a client-chosen secret would be the wrong trust model entirely, not just a missing validation.
- `register_trusted_invoker` (`PUT`) is gated on the invoker already being onboarded; `update_trusted_invoker` (`POST .../update`) is not — it only requires a trust context to already exist, matching the real reference's own asymmetric behavior between the two endpoints exactly.
- `selSecurityMethod` is honestly adapted, not fabricated: the real CAPIF core cross-checks a requested security method against a published `AefProfile`'s own declared support; this build has no real AEF publishing that catalog, so it takes the invoker's own first preference instead of inventing a match that doesn't exist.
- `GetTrustedInvokersApiInvokerId` redacts `authenticationInfo`/`authorizationInfo` to empty strings by default — a caller must explicitly ask for each via its own query parameter to see the raw value, mirroring the real reference's own default-deny shape rather than handing out secrets to anyone who can read the record at all.
- `/oauth2/introspect` is deliberately unauthenticated — the same network-isolation reasoning already applied to `/bootstrap` (call flow 01): this is R1 Termination checking a token on the SME<->R1 internal link, never exposed past the docker-compose network boundary.
- This build issues opaque, server-tracked tokens (`IssuedAccessToken`, looked up by hash) rather than self-contained signed JWTs — the real reference delegates that signing to an external Keycloak instance this build has no equivalent of; introspection is the honest substitute, not a cosmetic stand-in.
