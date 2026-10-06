"""TX-muting decision engine (README section 4): pure functions, no I/O.

evaluate() takes one cell's latest PM values and TX-muting configuration, plus the threshold configuration, and returns
REDUCED_TX (MUTING_ON), FULL_TX (MUTING_OFF) or NO_CHANGE, with the checks it made.

    mute     MUTING_OFF, feature enabled, PRB < activation and UEs < activation
    restore  MUTING_ON, PRB >= deactivation or UEs >= deactivation
    else     NO_CHANGE (between the thresholds is the hysteresis band)
"""


class ThresholdError(ValueError):
    pass


def validate_thresholds(cfg: dict) -> None:
    """Activation thresholds must sit strictly below deactivation thresholds (hysteresis)."""
    act, deact = cfg["activation"], cfg["deactivation"]
    if not act["prbUtilizationPercent"] < deact["prbUtilizationPercent"]:
        raise ThresholdError("activation.prbUtilizationPercent must be below deactivation.prbUtilizationPercent")
    if not act["rrcConnectedUeCount"] < deact["rrcConnectedUeCount"]:
        raise ThresholdError("activation.rrcConnectedUeCount must be below deactivation.rrcConnectedUeCount")


def evaluate(snapshot: dict, cfg: dict) -> dict:
    """snapshot: {name: {"value": ...}} for dlPrbUtilization, rrcConnectedUeCount, txMutingActivation, txMutingFeatureEnable."""
    validate_thresholds(cfg)
    act, deact, req = cfg["activation"], cfg["deactivation"], cfg["requestedConfiguration"]
    value = {n: (snapshot.get(n) or {}).get("value") for n in
             ("dlPrbUtilization", "rrcConnectedUeCount", "txMutingActivation", "txMutingFeatureEnable")}
    current = value["txMutingActivation"]
    ev = {"featureEnabled": value["txMutingFeatureEnable"] is True}
    measured = value["dlPrbUtilization"] is not None and value["rrcConnectedUeCount"] is not None
    if measured:
        prb, ue = float(value["dlPrbUtilization"]), int(value["rrcConnectedUeCount"])
        ev.update(prbBelowActivation=prb < act["prbUtilizationPercent"], ueBelowActivation=ue < act["rrcConnectedUeCount"],
                  prbAtOrAboveDeactivation=prb >= deact["prbUtilizationPercent"],
                  ueAtOrAboveDeactivation=ue >= deact["rrcConnectedUeCount"])

    def result(decision: str, reason: str, changes: dict | None = None) -> dict:
        return {"decision": decision, "reason": reason, "currentState": current, "evaluation": ev, "changes": changes or {}}

    if current not in (req["reducedTxYangValue"], req["fullTxYangValue"]):
        return result("NO_CHANGE", "CURRENT_STATE_UNKNOWN")
    if not measured:
        return result("NO_CHANGE", "MEASUREMENT_MISSING")

    if current == req["reducedTxYangValue"]:
        triggers = [reason for hit, reason in ((ev["prbAtOrAboveDeactivation"], "PRB_HIGH"),
                                               (ev["ueAtOrAboveDeactivation"], "UE_COUNT_HIGH")) if hit]
        if triggers:
            return result("FULL_TX", ",".join(triggers), {"txMutingActivation": req["fullTxYangValue"]})
        return result("NO_CHANGE", "LOAD_WITHIN_HYSTERESIS")

    blockers = [reason for blocked, reason in ((not ev["prbBelowActivation"], "PRB_NOT_LOW"),
                                               (not ev["ueBelowActivation"], "UE_COUNT_NOT_LOW"),
                                               (req["requireFeatureEnabled"] and not ev["featureEnabled"], "FEATURE_DISABLED"))
                if blocked]
    if blockers:
        return result("NO_CHANGE", ",".join(blockers))
    return result("REDUCED_TX", "INSTANTANEOUS_LOW_LOAD", {
        "txMutingFeatureEnable": "true", "txPathOffPattern": req["txPathOffPattern"],
        "txMutingActivation": req["reducedTxYangValue"]})
