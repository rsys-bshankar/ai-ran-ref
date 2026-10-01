# Call Flow: FOCOM Resource/Inventory Lifecycle — Subscribe → Provision → Notify → Deprovision

FOCOM's resource and inventory lifecycle (NFO+FOCOM LLD section 4): a consumer subscribes
to inventory changes with a callback, a resource is provisioned and deprovisioned, and each
change is notified to the matching subscriptions (HISTORY.md OI-5-focom-subscription).
Call flow 10 uses `provision_resource` as its INFRA step.

```mermaid
sequenceDiagram
    actor Operator
    participant Focom as FOCOM
    actor NFO as NFO (inventory subscriber)

    rect rgb(240, 255, 240)
    Note over NFO,Focom: Subscribe before anything exists to be notified about
    NFO->>Focom: POST /inventory/subscriptions (callback, resourceTypeId?, consumerSubscriptionId)
    Focom-->>NFO: subscriptionId, consumerSubscriptionId (echoed back — "for tracking,<br/>routing, or identifying the subscription," per the real O2IMS spec text)
    end

    rect rgb(240, 248, 255)
    Note over Operator,Focom: Provision — a real Resource row, not a random UUID
    Operator->>Focom: POST /resources/provision (resourceTypeId?, description, globalAssetId?, tags?, groups?)
    Focom->>Focom: resourceTypeId unrecognized -> auto-register a new ResourceType<br/>(Phase 1 never validated this field — rejecting it now would be scope creep)
    Note over Focom: real bug caught here, against real Postgres only: the new<br/>ResourceType row must be flush()'d before the Resource row that<br/>references it, or a genuine ForeignKeyViolation follows — SQLite's<br/>test harness never enforces the FK, so no unit test had caught it
    Focom->>Focom: create Resource(resourceTypeId, resourcePoolId=PHASE1_POOL_ID, description, globalAssetId, tags, groups)
    Focom->>NFO: best-effort POST callback (objectType=resource,<br/>notificationEventType=CREATE, resourceId, resourceTypeId, consumerSubscriptionId)
    Focom-->>Operator: resourceId, clusterId
    end

    Operator->>Focom: GET /resource-pools/{PHASE1_POOL_ID}/resources
    Focom-->>Operator: [Resource, ...] — the drill-down this same resource now has something behind it for

    rect rgb(255, 240, 240)
    Note over Operator,Focom: Deprovision — idempotent, filtered notification either way
    Operator->>Focom: DELETE /resources/{resourceId}
    Focom->>Focom: delete Resource row
    Focom->>NFO: best-effort POST callback (notificationEventType=DELETE, resourceId, resourceTypeId)
    Focom-->>Operator: {status: deprovisioned}

    Operator->>Focom: DELETE /resources/{already-deprovisioned-or-bogus-id}
    Note over Focom: non-UUID or never-provisioned id -> a no-op, not an error —<br/>still notifies (resourceTypeId=null), an unset filter always matches<br/>rather than being silently dropped
    Focom-->>Operator: {status: deprovisioned}
    end
```

**Key decisions this flow depends on:**
- `consumerSubscriptionId` is stored *and* echoed back on every notification — the O2IMS spec's own text says it exists "for tracking, routing, or identifying the subscription used to report the event," not just to be recorded and forgotten (HISTORY.md SA-FOCOM-5).
- Notification is best-effort and filtered, not broadcast: a subscription with a `resourceTypeId` filter only hears about matching resources; one with no filter hears about everything, including a deprovision against an id FOCOM never actually provisioned (`resourceTypeId=null` on that notification, matching an unset filter always matching rather than being silently dropped).
- Provisioning an unrecognized `resourceTypeId` auto-registers it rather than rejecting the request — Phase 1 never validated this field, so rejecting it would be a behavior change, not just filling in a schema gap. O2IMS treats `ResourceType` as read-only; tightening this is OPEN_ITEMS.md SA-FOCOM-9.
- Deprovisioning a never-provisioned or malformed `resourceId` is a deliberate no-op, not an error — mirroring DME's idempotent unsubscribe/deregister routes and NFO's idempotent double-terminate-on-missing-row shape (call flow 15).
- A fresh `ResourceType` auto-registered and then referenced by a new `Resource` row in the same request needs an explicit `flush()` between the two inserts (the same pattern NFO's `Instantiate` uses between its own dependent inserts); without it Postgres raises a `ForeignKeyViolation` that SQLite's test harness, which doesn't enforce the FK, never shows.
