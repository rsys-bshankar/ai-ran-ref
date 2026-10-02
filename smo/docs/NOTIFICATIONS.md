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

Everything classed A ignored the response (`post_webhook` returns it and no caller kept it), including the Intent Service's
RMIH callback, so no A-class site needed the response: the first sort of site the plan worried about did not occur. All of them have
moved (`PR-MSG-1.5`–`1.9`); what is left inline is the class-B DELETE and the two class-C reads.
After the move, a notification is sent after the transaction that caused it commits, never before, and a rolled-back change sends nothing.

## Call sites

| File | Function | Helper | Class | What it tells whom | Moves in |
|---|---|---|---|---|---|
| `dme/app/main.py` | `_notify_type_subscribers` | `enqueue` | A | Every type subscriber: a type was created, changed or removed | moved (MSG-1.5) |
| `dme/app/main.py` | `terminate_data_offer` | `enqueue` | A | The offer owner's termination URI: the offer was removed | moved (MSG-1.5) |
| `dme/app/main.py` | `_push_job_to_producers` | `enqueue` | A | Each supporting producer's job callback: a data job exists | moved (MSG-1.5) |
| `dme/app/main.py` | `_stop_job_at_producers` | `delete_webhook` | B | Each supporting producer: stop this job | open (needs a method column) |
| `dme/app/main.py` | `_producer_is_healthy` | `get_webhook` | C | A producer's health URL: answers the type's ENABLED/DISABLED status | stays inline |
| `sme/app/main.py` | `_deliver` | `enqueue` | A | Event subscribers: a service API became available, changed or went away | moved (MSG-1.6) |
| `aimgf/app/main.py` | `_notify_job_completion` | `enqueue` | A | The job's notification URI: a training or inference job finished | moved (MSG-1.7) |
| `aimgf/app/main.py` | `report_performance` | `enqueue` | A | A model subscriber: a performance report, possibly below its floor | moved (MSG-1.7) |
| `a1-related/app/main.py` | `_notify_policy_status_subscribers` | `enqueue` | A | Policy-status subscribers: an enforcement status changed | moved (MSG-1.8) |
| `focom/app/main.py` | `_notify_inventory_subscribers` | `enqueue` | A | Inventory subscribers: a resource changed | moved (MSG-1.8) |
| `focom/app/fcaps.py` | `_notify` | `enqueue` | A | Alarm subscribers: an alarm was raised, changed or cleared | moved (MSG-1.8) |
| `focom/app/fcaps.py` | `_report` | `enqueue` | A | Performance subscribers: new measurements | moved (MSG-1.8) |
| `mdaf/app/main.py` | `_notify_report_subscribers` | `enqueue` | A | Analytics subscribers: a report crossed their threshold | moved (MSG-1.8) |
| `mdaf/app/mda.py` | `_deliver` | `enqueue` | A | An MDA request's reporting target: a report, or a report file, is ready (two call sites). `delivery.notified` is set whatever the answer was; after the move it means "enqueued" | moved (MSG-1.8) |
| `intent-service/app/main.py` | `_create_intent_row` | `enqueue` | A | The RMIH's notification destination: an intent was created | moved (MSG-1.9) |
| `intent-service/app/main.py` | `_deliver_report` | `enqueue` | A | Each report recipient of an intent: a report is available | moved (MSG-1.9) |
| `intent-service/app/main.py` | `_notify_autonomy_operator` | `enqueue` | A | The operator's destination: an autonomy dispatch outcome | moved (MSG-1.9) |
| `ran-nf-oam/app/main.py` | `report_pm_file` | `enqueue` | A | File subscribers: a PM file is ready (one per subscriber, after the commit already) | moved (MSG-1.9) |
| `ran-nf-oam/app/vendors.py` | `onboard_vendor` | `get_webhook` | C | The adaptor's `/capabilities`: the answer fills the vendor profile | stays inline |

SA SMOS has no call site: its notifications, where it has any, travel through R1 Termination, not to a registered destination.
