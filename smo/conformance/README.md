# O1 adaptor conformance kit (`conformance/o1`)

What RAN NF OAM relies on when it talks to an O1 adaptor, as checks you run against one. Written for a vendor or an integrator who has an adaptor and wants to know, before registering it, whether RAN NF OAM can use it; and for this repository, which runs it against its own mock adaptor on every pull request (CI job `o1-conformance`).

```bash
python -m conformance.o1 --adaptor http://adaptor:8000 --out report        # report.json and report.md
python -m conformance.o1 --adaptor http://adaptor:8000 --protocol netconf   # one transport; the default, auto, is what /capabilities declares
python -m conformance.o1 --adaptor http://adaptor:8000 --only NC-2 --only RESTCONF
python -m conformance.o1 --adaptor http://gateway --netconf-path /vendor/netconf --header "Authorization: Bearer ..."   # an adaptor not at the default paths
python -m conformance.o1 --adaptor x --list
```

Needs `httpx` and `defusedxml` (the SMO's runtime requirements). Exit status 0: every check that ran passed; 1: a check failed (the report says which and what the adaptor did instead).

## What is checked, and what is not

The checks cover the contract RAN NF OAM's clients actually use: `ran-nf-oam/app/netconf_client.py` (an `<rpc><edit-config>` / `<get-config>` posted as XML to the adaptor's registered URI), `restconf_client.py` (RFC 8040 data resources under a RESTCONF root) and the vendor discovery that reads `GET /capabilities`. A request RAN NF OAM never makes is not tested: this is not a NETCONF or RESTCONF test suite, and an adaptor that passes it can still be unfit for something the SMO does not use it for. Each check is one sentence in the table below, and a failure says what the adaptor answered.

A check that is acknowledged-but-not-applied is the commonest real defect (an agent that says `<ok/>` and keeps nothing), so every write is read back.

| Check | Group | What |
|---|---|---|
| DISC-1 | DISC | GET /capabilities answers a JSON object with vendorName, supportedServices and supportedVendorModes |
| DISC-2 | DISC | every declared service and transport is one RAN NF OAM knows |
| DISC-3 | DISC | a transport that is declared is one this run exercises (and the declaration covers the ones it does) |
| NC-1 | NETCONF | an edit-config (merge) of a managed object is acknowledged with <ok/> |
| NC-2 | NETCONF | what an edit-config wrote is what get-config reads back (a write that is acknowledged and not applied is the commonest lie) |
| NC-3 | NETCONF | a second merge keeps the attributes the first one wrote |
| NC-4 | NETCONF | a managed function is addressed apart from its element (function-ref) |
| NC-5 | NETCONF | a delete removes what was written |
| NC-6 | NETCONF | a merge that carries no attribute is refused with an <rpc-error>, not acknowledged |
| NC-7 | NETCONF | a request with no managed-object ref is refused with an <rpc-error> |
| NC-8 | NETCONF | a body that is not XML is answered, not crashed on (no 5xx) |
| NC-9 | NETCONF | the reply carries the request's message-id |
| NC-10 | NETCONF | values with XML special characters round-trip, escaped |
| NC-11 | NETCONF | an XML entity in a request is not expanded (no entity expansion, no 5xx) |
| RC-1 | RESTCONF | /.well-known/host-meta names the RESTCONF root |
| RC-2 | RESTCONF | a PUT of a new object answers 201 and a PUT of an existing one answers 204 |
| RC-3 | RESTCONF | a GET reads back what a PUT wrote, as a list entry keyed by the object |
| RC-4 | RESTCONF | a PATCH merges: it changes what it names and keeps the rest |
| RC-5 | RESTCONF | a PUT replaces: attributes it does not name are gone |
| RC-6 | RESTCONF | a managed function is a resource below its element |
| RC-7 | RESTCONF | a DELETE answers 204 and a second DELETE answers 404 |
| RC-8 | RESTCONF | a body whose key does not match the resource is refused with 400 |
| RC-9 | RESTCONF | a body that is not a list entry is refused with 400 and an ietf-restconf:errors body |
| RC-10 | RESTCONF | a PATCH that names no attribute is refused with 400 |

Each run uses managed-object references that are new to it (`conf-<run>-<n>`), so a second run against the same adaptor does not meet the first one's objects; the kit does not remove what it created (a RESTCONF `DELETE` and a NETCONF `delete` are themselves checked, and the other objects stay).

## Not covered yet

* **Fault management, performance data, software management and heartbeat** (`PR-SB-9.3` to `9.5`). These run the other way: RAN NF OAM receives them (`POST /alarms/ingest`, `/pm-files`, `/pm-reports`, `/software-management-jobs`, `/o1-adaptor-endpoints/{id}/heartbeat`), and a check needs the adaptor to emit something on request, which has no standard trigger. The plan is a trigger option for the kit and an event emitter in the stub (`OPEN_ITEMS.md`, `SB-9.8`).
* **NETCONF over SSH or TLS** (the transports RAN NF OAM also speaks, `PR-SB-1`): tested against a real server (netopeer2) in CI job `netconf-lab`, not by this kit, which speaks the HTTP-carried form the mock and a vendor's HTTP front end use.
* **YANG validation** of what an adaptor accepts (`PR-SB-5`).

## The stub

`mock-o1-adaptor` is the RAN O1 stub of this repository: it answers both forms, declares a vendor, can inject faults (`POST /faults`: a timeout, an `<rpc-error>`, an acknowledged write that is not applied) and passes this kit. Running the kit against it with a fault injected is how the kit itself is tested (`mock-o1-adaptor/tests/test_conformance_kit.py`: a write that is acknowledged and not applied fails NC-2 and RC-3).
