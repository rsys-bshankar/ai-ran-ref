"""The runtime checks: one rApp package taken through its life on a running stack. The kit does the operator's part (onboard, create, terminate) and the rApp's own
calls to the platform (bootstrap-complete, configuration, performance and fault reports), and reads the platform back after each step, so a step that is
answered 2xx and did nothing fails.

What this is not: it does not run the rApp's code. A package's workload is started by the deployment (compose, Kubernetes), not by the platform, so the kit plays the
calls the workload makes; whether the workload itself makes them is for the rApp's own tests (the SDK, `sdk/README.md`).

    RT-1       the package is fetched and onboarded (Onboarding)
    RT-2, 3    register: the instance is created, reaches RUNNING after bootstrap-complete, and holds the package's usage registration
    RT-4       heartbeat: the rApp's periodic performance reports are recorded and read back, newest first
    RT-5, 6    R1 usage: configuration round trip, the package's declarations readable, a warning fault recorded without leaving RUNNING
    RT-7, 8    terminate: UNDEPLOYED, workload and usage registration released, then the row deleted

The platform has no heartbeat route of its own: the performance report is what a running rApp posts periodically, so that is what RT-4 plays.
Whatever the run created is removed at the end, also after a failure (`--keep` leaves the package onboarded).
"""

import httpx

from .kit import RUNTIME, Fail, RuntimeContext, check


def _json(resp: httpx.Response, what: str, expected: int | tuple[int, ...]):
    expected = (expected,) if isinstance(expected, int) else expected
    if resp.status_code not in expected:
        raise Fail(f"{what}: answered {resp.status_code}, expected {' or '.join(map(str, expected))}" + _reason(resp))
    try:
        return resp.json() if resp.content else None
    except ValueError:
        raise Fail(f"{what}: the answer is not JSON") from None


def _reason(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        text = body.get("failureReason") or body.get("detail") or body.get("title") or body.get("message")
        if isinstance(text, str):
            return f" ({text[:200]})"
    return ""


def _instance(ctx: RuntimeContext) -> dict:
    return _json(ctx.call("rapp-mgmt", "get", f"/instances/{ctx.need('instance_id', 'RT-2 (the instance)')}"), "reading the instance", 200)


def _delete_package(ctx: RuntimeContext, package_id: str) -> None:
    ctx.call("onboarding", "delete", f"/packages/{package_id}")


def _remove_instance(ctx: RuntimeContext, instance_id: str) -> None:
    """Clean-up of an instance a failed run left: terminate (a no-op answered 409 once it is UNDEPLOYED), then delete. Nothing to do when RT-8 already did."""
    if ctx.state.get("deleted"):
        return
    ctx.call("rapp-mgmt", "post", f"/instances/{instance_id}/terminate")
    ctx.call("rapp-mgmt", "delete", f"/instances/{instance_id}")


@check("RT-1", "ONBOARD", "the package is fetched by Onboarding and reaches AVAILABLE with a deployment descriptor", RUNTIME)
def rt_onboard(ctx: RuntimeContext) -> None:
    answer = _json(ctx.call("onboarding", "post", "/packages", json={"location": ctx.package_url}), "onboarding the package", 202)
    package_id = answer.get("packageId") if isinstance(answer, dict) else None
    if not package_id:
        raise Fail("POST /packages answered no packageId")
    if not ctx.keep:
        ctx.cleanups.append(lambda: _delete_package(ctx, package_id))
    status = _json(ctx.call("onboarding", "get", f"/packages/{package_id}/onboarding-status"), "reading the onboarding status", 200)
    if status.get("state") != "AVAILABLE":
        raise Fail(f"the package is {status.get('state')}, not AVAILABLE" + (f": {answer['failureReason']}" if answer.get("failureReason") else ""))
    if not status.get("nfDeploymentDescriptorId"):
        raise Fail("the package is AVAILABLE but has no NFO deployment descriptor")
    ctx.state["package_id"] = package_id


@check("RT-2", "REGISTER", "an instance is created (DEPLOYING, with an identity and an NFO workload) and reaches RUNNING when it reports bootstrap-complete", RUNTIME)
def rt_register(ctx: RuntimeContext) -> None:
    package_id = ctx.need("package_id", "RT-1 (the package)")
    answer = _json(ctx.call("rapp-mgmt", "post", "/instances", json={"packageId": package_id, "autonomyMode": ctx.autonomy_mode, "config": {"conformanceRun": ctx.run_id}}),
                   "creating the instance", 202)
    instance_id = answer.get("instanceId") if isinstance(answer, dict) else None
    if not instance_id or not answer.get("oauthClientId"):
        raise Fail("POST /instances answered no instanceId or no oauthClientId (the instance's identity at SME)")
    ctx.state["instance_id"] = instance_id
    if not ctx.keep:
        ctx.cleanups.append(lambda: _remove_instance(ctx, instance_id))
    created = _instance(ctx)
    if created.get("state") != "DEPLOYING":
        raise Fail(f"a new instance is {created.get('state')}, expected DEPLOYING")
    if not created.get("workloadRef"):
        raise Fail("the instance has no workloadRef: NFO did not accept the deployment")
    if created.get("autonomyMode") != ctx.autonomy_mode:
        raise Fail(f"autonomyMode is {created.get('autonomyMode')}, expected {ctx.autonomy_mode} as requested")
    done = _json(ctx.call("rapp-mgmt", "post", f"/instances/{instance_id}/bootstrap-complete"), "bootstrap-complete", 200)
    if done.get("state") != "RUNNING":
        raise Fail(f"bootstrap-complete answered state {done.get('state')}, expected RUNNING")
    if _instance(ctx).get("state") != "RUNNING":
        raise Fail("bootstrap-complete was answered RUNNING but reading the instance back does not say so")


@check("RT-3", "REGISTER", "Onboarding holds one active usage registration for the package, consumed by the instance", RUNTIME)
def rt_usage(ctx: RuntimeContext) -> None:
    package_id, instance_id = ctx.need("package_id", "RT-1 (the package)"), ctx.need("instance_id", "RT-2 (the instance)")
    page = _json(ctx.call("onboarding", "get", f"/packages/{package_id}/usage"), "listing the package's usage", 200)
    mine = [r for r in page.get("items", []) if r.get("consumerId") == instance_id]
    if len(mine) != 1:
        raise Fail(f"{len(mine)} usage registrations name the instance as consumer, expected 1 (the guard that blocks deleting a package in use depends on it)")
    if not mine[0].get("active"):
        raise Fail("the instance's usage registration is not active while the instance is RUNNING")


@check("RT-4", "HEARTBEAT", "performance reports (the rApp's periodic heartbeat) are recorded and listed newest first", RUNTIME)
def rt_heartbeat(ctx: RuntimeContext) -> None:
    instance_id = ctx.need("instance_id", "RT-2 (the instance)")
    for beat in (1, 2):
        answer = _json(ctx.call("rapp-mgmt", "post", f"/instances/{instance_id}/performance", json={"heartbeat": f"{ctx.run_id}-{beat}", "beat": beat}),
                       f"performance report {beat}", 200)
        if not isinstance(answer, dict) or answer.get("status") != "recorded":
            raise Fail(f"performance report {beat} was not acknowledged as recorded")
    page = _json(ctx.call("rapp-mgmt", "get", f"/instances/{instance_id}/performance"), "listing the performance reports", 200)
    beats = [r["metrics"].get("beat") for r in page.get("items", []) if isinstance(r.get("metrics"), dict) and str(r["metrics"].get("heartbeat", "")).startswith(ctx.run_id)]
    if beats != [2, 1]:
        raise Fail(f"the reports read back as {beats}, expected [2, 1] (both recorded, newest first)")
    if _instance(ctx).get("state") != "RUNNING":
        raise Fail("the instance left RUNNING after ordinary reports")


@check("RT-5", "R1-USAGE", "the instance's configuration is stored and read back, and its package declarations are readable over R1", RUNTIME)
def rt_config(ctx: RuntimeContext) -> None:
    instance_id, package_id = ctx.need("instance_id", "RT-2 (the instance)"), ctx.need("package_id", "RT-1 (the package)")
    config = {"conformanceRun": ctx.run_id, "threshold": 0.8, "cells": ["c1", "c2"]}
    answer = _json(ctx.call("rapp-mgmt", "put", f"/instances/{instance_id}/config", json=config), "writing the configuration", 200)
    if not isinstance(answer, dict) or answer.get("status") != "updated":
        raise Fail("the configuration write was not acknowledged as updated")
    read = _json(ctx.call("rapp-mgmt", "get", f"/instances/{instance_id}/config"), "reading the configuration", 200)
    if read != config:
        raise Fail(f"the configuration read back differs from what was written: {str(read)[:200]}")
    status = _json(ctx.call("onboarding", "get", f"/packages/{package_id}/onboarding-status"), "reading the package declarations", 200)
    if "aiCapabilities" not in status or "smeDeclarations" not in status:
        raise Fail("onboarding-status carries no aiCapabilities or smeDeclarations field (the platform's view of the package's manifest)")


@check("RT-6", "R1-USAGE", "a warning fault is recorded and listed, and does not take the instance out of RUNNING", RUNTIME)
def rt_fault(ctx: RuntimeContext) -> None:
    instance_id = ctx.need("instance_id", "RT-2 (the instance)")
    answer = _json(ctx.call("rapp-mgmt", "post", f"/instances/{instance_id}/fault", params={"severity": "warning", "description": f"conformance {ctx.run_id}"}),
                   "reporting a fault", 200)
    if not isinstance(answer, dict) or answer.get("status") != "recorded" or answer.get("instanceState") != "RUNNING":
        raise Fail(f"a warning fault was answered {answer}, expected recorded with the instance still RUNNING")
    page = _json(ctx.call("rapp-mgmt", "get", f"/instances/{instance_id}/faults"), "listing the faults", 200)
    if not any(r.get("description") == f"conformance {ctx.run_id}" and r.get("severity") == "warning" for r in page.get("items", [])):
        raise Fail("the fault was acknowledged but is not in the fault list")
    if _instance(ctx).get("state") != "RUNNING":
        raise Fail("a warning fault took the instance out of RUNNING (only a critical one may)")


@check("RT-7", "TERMINATE", "terminate lands in UNDEPLOYED and releases the NFO workload and the usage registration", RUNTIME)
def rt_terminate(ctx: RuntimeContext) -> None:
    instance_id, package_id = ctx.need("instance_id", "RT-2 (the instance)"), ctx.need("package_id", "RT-1 (the package)")
    answer = _json(ctx.call("rapp-mgmt", "post", f"/instances/{instance_id}/terminate"), "terminating the instance", 200)
    if answer.get("state") != "UNDEPLOYED":
        raise Fail(f"terminate answered state {answer.get('state')}, expected UNDEPLOYED")
    teardown = answer.get("lastTeardown") or {}
    bad = {k: teardown.get(k) for k in ("nfoTerminate", "usageStop") if teardown.get(k) != "DONE"}
    if bad:
        raise Fail(f"the teardown did not release everything: {bad}")
    page = _json(ctx.call("onboarding", "get", f"/packages/{package_id}/usage"), "listing the package's usage", 200)
    if [r for r in page.get("items", []) if r.get("consumerId") == instance_id and r.get("active")]:
        raise Fail("the usage registration is still active after terminate (it would block deleting the package)")
    if _instance(ctx).get("state") != "UNDEPLOYED":
        raise Fail("reading the instance back after terminate does not say UNDEPLOYED")
    ctx.state["terminated"] = True


@check("RT-8", "TERMINATE", "the terminated instance is deleted, then answers 404", RUNTIME)
def rt_delete(ctx: RuntimeContext) -> None:
    instance_id = ctx.need("instance_id", "RT-2 (the instance)")
    ctx.need("terminated", "RT-7 (terminate)")
    resp = ctx.call("rapp-mgmt", "delete", f"/instances/{instance_id}")
    if resp.status_code != 204:
        raise Fail(f"DELETE of a terminated instance answered {resp.status_code}, expected 204" + _reason(resp))
    ctx.state["deleted"] = True
    gone = ctx.call("rapp-mgmt", "get", f"/instances/{instance_id}")
    if gone.status_code != 404:
        raise Fail(f"the deleted instance answers {gone.status_code} instead of 404")
