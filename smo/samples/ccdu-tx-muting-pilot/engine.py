"""CCDU TX-muting decision engine (HLD/LLD §6, Appendix A.5): pure functions, no I/O.

evaluate() takes one cell's latest snapshot and the threshold configuration and returns
REDUCED_TX (MUTING_ON), FULL_TX (MUTING_OFF) or NO_CHANGE, with every check it made.
"""

import datetime

MANDATORY = ("dlPrbUtilization", "rrcConnectedUeCount", "mruSynchronizationState",
             "txMutingActivation", "txMutingFeatureEnable")


class ThresholdError(ValueError):
    pass


def validate_thresholds(cfg: dict) -> None:
    """A.5.4: activation thresholds must sit strictly below deactivation thresholds (hysteresis)."""
    act, deact = cfg["activation"], cfg["deactivation"]
    if not act["prbUtilizationPercent"] < deact["prbUtilizationPercent"]:
        raise ThresholdError("activation.prbUtilizationPercent must be below deactivation.prbUtilizationPercent")
    if not act["rrcConnectedUeCount"] < deact["rrcConnectedUeCount"]:
        raise ThresholdError("activation.rrcConnectedUeCount must be below deactivation.rrcConnectedUeCount")


def quality(measurement: dict | None, now: datetime.datetime, max_age_s: int) -> str:
    """VALID, STALE or MISSING. A measurement with no timestamp (a configuration read) is VALID when present."""
    if measurement is None or measurement.get("value") is None:
        return "MISSING"
    ts = measurement.get("timestamp")
    if ts is None:
        return measurement.get("quality", "VALID")
    if isinstance(ts, str):
        ts = datetime.datetime.fromisoformat(ts)
    if (now - ts).total_seconds() > max_age_s:
        return "STALE"
    return measurement.get("quality", "VALID")


def evaluate(snapshot: dict, cfg: dict, now: datetime.datetime) -> dict:
    """snapshot: {name: {"value": ..., "timestamp": ...}} for MANDATORY, plus "blockingAlarms": [alarmId, ...]."""
    validate_thresholds(cfg)
    act, deact = cfg["activation"], cfg["deactivation"]
    policy, alarms, req = cfg["measurementPolicy"], cfg["alarmPolicy"], cfg["requestedConfiguration"]

    qualities = {n: quality(snapshot.get(n), now, policy["maximumSampleAgeSeconds"]) for n in MANDATORY}
    value = {n: (snapshot.get(n) or {}).get("value") for n in MANDATORY}
    blocking = sorted(set(snapshot.get("blockingAlarms", [])) & set(alarms["blockingAlarmIds"]))
    measurements_ok = all(q == "VALID" for q in qualities.values())
    current = value["txMutingActivation"]

    ev = {
        "qualities": qualities,
        "allMeasurementsValid": measurements_ok,
        "blockingAlarms": blocking,
        "featureEnabled": value["txMutingFeatureEnable"] is True,
        "mruSynchronized": value["mruSynchronizationState"] == "SYNCHRONIZED",
    }
    if measurements_ok:
        prb, ue = float(value["dlPrbUtilization"]), int(value["rrcConnectedUeCount"])
        ev.update(prbBelowActivation=prb < act["prbUtilizationPercent"], ueBelowActivation=ue < act["rrcConnectedUeCount"],
                  prbAtOrAboveDeactivation=prb >= deact["prbUtilizationPercent"],
                  ueAtOrAboveDeactivation=ue >= deact["rrcConnectedUeCount"])

    def result(decision: str, reason: str, changes: dict | None = None) -> dict:
        return {"decision": decision, "reason": reason, "currentState": current, "evaluation": ev, "changes": changes or {}}

    if current == req["reducedTxYangValue"]:
        triggers = []
        if not measurements_ok:
            bad = {n: q for n, q in qualities.items() if q != "VALID"}
            fail_safe = any((q == "MISSING" and policy["missingMeasurementAction"] == "REQUEST_FULL_TX")
                            or (q == "STALE" and policy["staleMeasurementAction"] == "REQUEST_FULL_TX") for q in bad.values())
            if fail_safe:
                triggers.append(f"MEASUREMENT_{'_'.join(sorted(set(bad.values())))}")
        else:
            if ev["prbAtOrAboveDeactivation"]:
                triggers.append("PRB_HIGH")
            if ev["ueAtOrAboveDeactivation"]:
                triggers.append("UE_COUNT_HIGH")
        if value["mruSynchronizationState"] not in (None, "SYNCHRONIZED"):
            triggers.append("MRU_NOT_SYNCHRONIZED")
        if blocking and alarms["requestFullTxOnBlockingAlarm"]:
            triggers.append("BLOCKING_ALARM")
        if triggers:
            return result("FULL_TX", ",".join(triggers), {"txMutingActivation": req["fullTxYangValue"]})
        return result("NO_CHANGE", "LOAD_WITHIN_HYSTERESIS" if measurements_ok else "MEASUREMENTS_NOT_VALID")

    if current == req["fullTxYangValue"]:
        blockers = []
        if not measurements_ok:
            blockers.append("MEASUREMENTS_NOT_VALID")
        else:
            if not ev["prbBelowActivation"]:
                blockers.append("PRB_NOT_LOW")
            if not ev["ueBelowActivation"]:
                blockers.append("UE_COUNT_NOT_LOW")
        if req["requireFeatureEnabled"] and not ev["featureEnabled"]:
            blockers.append("FEATURE_DISABLED")
        if not ev["mruSynchronized"]:
            blockers.append("MRU_NOT_SYNCHRONIZED")
        if blocking and alarms["blockReducedTxOnActiveAlarm"]:
            blockers.append("BLOCKING_ALARM")
        if blockers:
            return result("NO_CHANGE", ",".join(blockers))
        return result("REDUCED_TX", "INSTANTANEOUS_LOW_LOAD", {
            "txMutingFeatureEnable": "true", "txPathOffPattern": req["txPathOffPattern"],
            "txMutingActivation": req["reducedTxYangValue"]})

    return result("NO_CHANGE", "CURRENT_STATE_UNKNOWN")
