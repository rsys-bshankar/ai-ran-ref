# Call Flow: MLMFSubscription — Subscribe → Notify → Unsubscribe

Stitches together `SPEC_AUDIT.md`'s AI/ML Workflow section, `MLMFSubscription` finding,
closed: every other subscription-shaped resource in this build (DME's type subscriptions,
MDAF's analytics subscriptions, A1 Related's EI jobs, Intent Service's RMIH registration)
notifies a real destination and can be torn down with a real `DELETE`; `MLMFSubscription`
previously could only be created and read. This flow is the dedicated subscribe→notify→
unsubscribe walkthrough call flow 02 only touches in passing.

```mermaid
sequenceDiagram
    actor SA as SA SMOS (push subscriber)
    actor Auditor as Read-only consumer (pull subscriber)
    participant AIMgF as AIMgF
    actor Producer as Model Producer rApp

    rect rgb(240, 255, 240)
    Note over SA,AIMgF: Push subscriber — registers a real callback
    SA->>AIMgF: SubscribePerformanceMonitoring(modelId, metricTypes, dmeTypeId, guardKpiFloor, notificationDestination)
    AIMgF-->>SA: subscriptionId=S1
    end

    rect rgb(240, 248, 255)
    Note over Auditor,AIMgF: Pull-only subscriber — never registers a destination
    Auditor->>AIMgF: SubscribePerformanceMonitoring(modelId, metricTypes, dmeTypeId)
    Note over AIMgF: notificationDestination omitted — same permissive shape<br/>every other subscription-shaped resource in this build allows
    AIMgF-->>Auditor: subscriptionId=S2
    end

    loop per reporting cycle
        Producer->>AIMgF: ReportPerformance(subscriptionId=S1, metrics)
        AIMgF->>AIMgF: persist PerformanceReport, breachedFloor = metrics violate guardKpiFloor
        AIMgF->>SA: best-effort POST notificationDestination<br/>(reportId, modelId, metrics, breachedFloor)
        Note over AIMgF,SA: unreachable subscriber never fails ReportPerformance itself —<br/>same pattern as every other notification in this build
        AIMgF-->>Producer: reportId, breachedFloor

        Producer->>AIMgF: ReportPerformance(subscriptionId=S2, metrics)
        AIMgF->>AIMgF: persist PerformanceReport
        Note over AIMgF,Auditor: S2 registered no destination — nothing is POSTed —<br/>S2 must poll GET /mlmf/subscriptions/{id}/reports instead
        AIMgF-->>Producer: reportId, breachedFloor
    end

    Auditor->>AIMgF: GET /mlmf/subscriptions/S2/reports
    AIMgF-->>Auditor: [PerformanceReport, ...] — the only path a pull-only subscriber has

    SA->>AIMgF: DELETE /mlmf/subscriptions/S1
    AIMgF-->>SA: 204
    SA->>AIMgF: DELETE /mlmf/subscriptions/S1
    Note over AIMgF: idempotent — a second DELETE of an already-gone (or never-existed)<br/>id still returns 204, matching every other subscription-shaped<br/>resource's own unsubscribe route
    AIMgF-->>SA: 204

    Producer->>AIMgF: ReportPerformance(subscriptionId=S1, metrics)
    AIMgF-->>Producer: 404 MLMF_SUBSCRIPTION_NOT_FOUND — sub is None post-unsubscribe<br/>(see "Key decisions" below: this used to be an unhandled 500)
```

**Key decisions this flow depends on:**
- `SubscribePerformanceMonitoring` requires `dmeTypeId` — an MLMF subscription is always scoped to a specific DME data type feeding the model's own performance signal, not a bare `modelId` alone.
- `notificationDestination` is optional on creation, exactly like DME's `DMETypeSubscription`, MDAF's own subscriptions, and A1 Related's EI jobs — a purely poll-based consumer (the `Auditor` actor here) is a first-class, fully-supported shape, not a degraded one.
- The push is best-effort per report, not per subscription lifetime — an unreachable `SA` on one `ReportPerformance` call doesn't disable future pushes; each call tries independently and swallows its own `httpx.HTTPError`.
- `DELETE /mlmf/subscriptions/{id}` is idempotent by construction (`if sub is not None: delete`), matching every other subscription-shaped resource's own unsubscribe route in this build (DME's type subscriptions, MDAF's, A1 Related's, Intent Service's RMIH deregistration) — deleting twice, or deleting an id that never existed, is never an error.
- **Closed since this flow was first written**: `report_performance` (`aimgf/app/main.py`) used to read `sub.guard_kpi_floor`/`sub.notification_destination` straight off `db.get(MLMFSubscription, subscription_id)` with no null-check in between — `ReportPerformance` against an unsubscribed or never-existed `subscriptionId` raised an unhandled `AttributeError` (a bare 500), not a clean 404. Now a real `MLMF_SUBSCRIPTION_NOT_FOUND` (404), matching every comparable cross-reference elsewhere in this build.
