# Outbound notifications

Every place the platform calls a caller-registered destination (`PR-MSG-1.1`). They all go through `smo_shared/webhook.py` (the SSRF
guard) today; the ones that are pure notifications move to the transactional outbox (`smo_shared/outbox.py`) one module at a time
(`MSG-1.5`–`1.9`, `OPEN_ITEMS.md` §5.3). `tests_integration/test_notification_inventory.py` fails when a call site is added or moved
without a row here.

## Classes

| Class | Meaning | Where it goes |
|---|---|---|
| **A** | A POST whose answer nothing reads: a notification. A lost one means a subscriber never learns of a change | Moves to the outbox (the PR named in the last column) |
| **B** | A command to a destination (a DELETE) whose answer nothing reads | Stays inline for now: the outbox row carries only a POST body. Needs a method column (a later step) before it can move |
| **C** | A read whose answer the caller uses to decide something (a health probe, a capability discovery) | Stays inline, always: the caller needs the answer in the request |

Everything classed A ignores the response today (`post_webhook` returns it and no caller keeps it), including the Intent Service's
RMIH callback, so there is no A-class site that needs the response: the first sort of site the plan worried about does not occur.
After the move, a notification is sent after the transaction that caused it commits, never before, and a rolled-back change sends nothing.

## Call sites

| File | Function | Helper | Class | What it tells whom | Moves in |
|---|---|---|---|---|---|
| `dme/app/main.py` | `_notify_type_subscribers` | `post_webhook` | A | Every type subscriber: a type was created, changed or removed | MSG-1.5 |
| `dme/app/main.py` | `terminate_data_offer` | `post_webhook` | A | The offer owner's termination URI: the offer was removed | MSG-1.5 |
| `dme/app/main.py` | `_push_job_to_producers` | `post_webhook` | A | Each supporting producer's job callback: a data job exists | MSG-1.5 |
| `dme/app/main.py` | `_stop_job_at_producers` | `delete_webhook` | B | Each supporting producer: stop this job | open (needs a method column) |
| `dme/app/main.py` | `_producer_is_healthy` | `get_webhook` | C | A producer's health URL: answers the type's ENABLED/DISABLED status | stays inline |
| `sme/app/main.py` | `_deliver` | `post_webhook` | A | Event subscribers: a service API became available, changed or went away | MSG-1.6 |
| `aimgf/app/main.py` | `_notify_job_completion` | `post_webhook` | A | The job's notification URI: a training or inference job finished | MSG-1.7 |
| `aimgf/app/main.py` | `report_performance` | `post_webhook` | A | A model subscriber: a performance report, possibly below its floor | MSG-1.7 |
| `a1-related/app/main.py` | `_notify_policy_status_subscribers` | `post_webhook` | A | Policy-status subscribers: an enforcement status changed | MSG-1.8 |
| `focom/app/main.py` | `_notify_inventory_subscribers` | `post_webhook` | A | Inventory subscribers: a resource changed | MSG-1.8 |
| `focom/app/fcaps.py` | `_notify` | `post_webhook` | A | Alarm subscribers: an alarm was raised, changed or cleared | MSG-1.8 |
| `focom/app/fcaps.py` | `_report` | `post_webhook` | A | Performance subscribers: new measurements | MSG-1.8 |
| `mdaf/app/main.py` | `_notify_report_subscribers` | `post_webhook` | A | Analytics subscribers: a report crossed their threshold | MSG-1.8 |
| `mdaf/app/mda.py` | `_deliver` | `post_webhook` | A | An MDA request's reporting target: a report, or a report file, is ready (two call sites). `delivery.notified` is set whatever the answer was; after the move it means "enqueued" | MSG-1.8 |
| `intent-service/app/main.py` | `_create_intent_row` | `post_webhook` | A | The RMIH's notification destination: an intent was created | MSG-1.9 |
| `intent-service/app/main.py` | `_deliver_report` | `post_webhook` | A | Each report recipient of an intent: a report is available | MSG-1.9 |
| `intent-service/app/main.py` | `_notify_autonomy_operator` | `post_webhook` | A | The operator's destination: an autonomy dispatch outcome | MSG-1.9 |
| `ran-nf-oam/app/main.py` | `report_pm_file` | `post_webhook` | A | File subscribers: a PM file is ready (one per subscriber, after the commit already) | MSG-1.9 (with the Intent Service PR: both are TS 28 notifications) |
| `ran-nf-oam/app/vendors.py` | `onboard_vendor` | `get_webhook` | C | The adaptor's `/capabilities`: the answer fills the vendor profile | stays inline |

SA SMOS has no call site: its notifications, where it has any, travel through R1 Termination, not to a registered destination.
