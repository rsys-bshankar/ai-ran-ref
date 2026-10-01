# Call Flow: SME CAPIF Security Lifecycle — Onboard → Trust → Token → Introspect → Update

SME's CAPIF security surface: Provider (APF) enrolment, API Invoker onboarding, the CAPIF
trust-context (`TrustedInvoker`) registry, and the `/oauth2/token` + `/oauth2/introspect`
pair behind R1 Termination's advertised `tokenEndPoint` (HISTORY.md OI-2-oauth2,
OI-5-sme-provider-enrolment, SA-SME-1, SA-SME-2). This is the security exchange that other
flows' `Container->>R1: obtain OAuth2.0 token` line (call flow 01) abbreviates. A requested
scope is checked against the published APIs before a token is issued, and an invoker onboarded
with a PEM key can authenticate with a signed client assertion instead of its secret
(HISTORY.md OI-2-oauth2-scope, SA-SME-1-public-key, OI-5-sme-filters).

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
    alt authenticate with the onboarding secret
        Container->>SME: POST /oauth2/token (client_credentials, client_id=apiInvokerId,<br/>client_secret=onboardingSecret, scope=3gpp#aef-1:kpi-api)
        SME->>SME: IsInvokerRegistered? VerifyInvokerSecret? — 400 on either failure
    else authenticate with a signed client assertion (RFC 7523)
        Container->>SME: POST /oauth2/token (client_credentials, client_id,<br/>client_assertion_type=jwt-bearer, client_assertion=JWT, scope)
        SME->>SME: verify the JWT with the onboarded PEM key, iss=sub=client_id,<br/>aud=token endpoint, exp within 300 s, jti never used — else 400 invalid_client
    end
    SME->>SME: check scope — each API published, exposed by that AEF,<br/>discoverable by the invoker — else 400 invalid_scope
    SME-->>Container: access_token, expires_in, token_type=Bearer, scope
    Note over SME: an opaque, server-tracked token — this build has no real IdP<br/>to delegate JWT signing to, unlike the reference's own Keycloak

    Container->>R1: GET /some-r1-route (Authorization: Bearer access_token)
    R1->>SME: POST /oauth2/introspect (token)
    Note over R1,SME: unauthenticated internal call — SME<->R1 traffic never<br/>leaves the docker-compose network, same reasoning /bootstrap<br/>itself already gives for staying unauthenticated
    SME-->>R1: active=true, client_id=apiInvokerId, exp, scope
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
- `register_trusted_invoker` (`PUT`) is gated on the invoker already being onboarded; `update_trusted_invoker` (`POST .../update`) is not — it only requires a trust context to already exist, matching the reference's own asymmetric behavior between the two endpoints.
- `selSecurityMethod` is adapted, not fabricated: the CAPIF core cross-checks a requested security method against a published `AefProfile`'s declared support; this build has no AEF publishing that catalog, so it takes the invoker's first preference instead of inventing a match.
- `GetTrustedInvokersApiInvokerId` redacts `authenticationInfo`/`authorizationInfo` to empty strings by default — a caller must explicitly ask for each via its own query parameter to see the raw value, mirroring the reference's default-deny shape rather than handing out secrets to anyone who can read the record.
- `/oauth2/introspect` is deliberately unauthenticated — the same network-isolation reasoning applied to `/bootstrap` (call flow 01): this is R1 Termination checking a token on the SME<->R1 internal link, never exposed past the docker-compose network boundary.
- This build issues opaque, server-tracked tokens (`IssuedAccessToken`, looked up by hash) rather than self-contained signed JWTs — the reference delegates signing to an external Keycloak instance this build has no equivalent of; introspection is the substitute.
- A `3gpp#aefId:apiName` scope is granted only if every API is published, exposed by that AEF and discoverable by the invoker; otherwise 400 `invalid_scope`. Introspection returns the granted scope. SMO's own clients request `smo-internal` / `smo-gui`, granted as-is.
- An RFC 7523 client assertion is verified with the invoker's onboarded PEM key and can be exchanged once (its `jti` is recorded until it expires). An invoker onboarded with an opaque label can only use its secret.
- Onboarding, key update and offboarding emit `API_INVOKER_ONBOARDED` / `_UPDATED` / `_OFFBOARDED`; offboarding revokes the invoker's tokens and drops its trust context.
