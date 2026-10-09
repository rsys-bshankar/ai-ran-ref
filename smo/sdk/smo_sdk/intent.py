"""sdk.intent: a thin client over Intent Service (`intent-service/`), the TS 28.312 Intent NRM.

Intent Service validates strictly against TS 28.312, so this client uses the spec's names (intentExpectations, intentPriority, intentReportControl, intentHandlingCapabilityList,
intentReference, ...). `energy_saving_expectation` builds the energy-saving template of HISTORY.md W6-03 (the one piece of logic here); `request_autonomy_dispatch` hands an inference outcome to the
platform, where the instance's autonomy mode decides what happens (HISTORY.md W10-03). Part of the SDK (`sdk/README.md`); no validation of its own.
"""

import uuid
from typing import Any

from ._common import BaseClient, ensure_ok


class IntentClient(BaseClient):
    """The `sdk.intent` namespace: intents, intent reports, intent handling function registration and autonomy dispatches at Intent Service."""
    def create_intent(self, intent_expectations: list[dict], rmih_id: str, user_label: str,
                      intent_report_control: list[dict] | None = None, intent_priority: int = 1, rmio_id: str = "",
                      intent_mgmt_purpose: str = "FULFILMENT_WITHOUT_NEGOTIATION",
                      intent_handling_scope: str | None = None, **spec_attributes) -> dict:
        """Create a strict TS 28.312 Intent addressed to one registered handling function `rmih_id` (consumer-side selection).

        `intent_report_control` defaults to a 60 s observation period with no recipient; any other spec attribute (intentContexts, guaranteePeriods, ...) can be passed by its spec name in `spec_attributes`,
        and overrides the defaults of the body. Raises `SdkError` 404 for an unknown RMIH, 422 for RMIH_CAPABILITY_MISMATCH or a spec validation error.
        """
        body = {"userLabel": user_label, "intentExpectations": intent_expectations, "intentPriority": intent_priority,
                "intentReportControl": intent_report_control or [{"observationPeriod": 60}], "rmioId": rmio_id,
                "rmihId": rmih_id, "intentMgmtPurpose": intent_mgmt_purpose, **spec_attributes}
        if intent_handling_scope is not None:
            body["intentHandlingScope"] = intent_handling_scope
        return ensure_ok(self._r1.post("/intent-service/intents", json=body))

    def get_intent(self, intent_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/intent-service/intents/{intent_id}"))

    def list_intents(self, admin_state: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/intent-service/intents", params={"admin_state": admin_state}))

    def update_intent_admin_state(self, intent_id: uuid.UUID | str, new_state: str, requester_id: str) -> dict:
        return ensure_ok(self._r1.patch(f"/intent-service/intents/{intent_id}/admin-state", json={
            "newState": new_state, "requesterId": requester_id,
        }))

    def delete_intent(self, intent_id: uuid.UUID | str) -> None:
        ensure_ok(self._r1.delete(f"/intent-service/intents/{intent_id}"))

    def publish_intent_report(self, intent_id: uuid.UUID | str, **reports) -> dict:
        """TS 28.312 IntentReport: pass any report kind by its spec name, for example `intentFulfilmentReport={...}` or `intentConflictReports=[...]`; `intentReference` is set from `intent_id`."""
        return ensure_ok(self._r1.post("/intent-service/intent-reports", json={"intentReference": str(intent_id), **reports}))

    def list_intent_reports(self, intent_id: uuid.UUID | str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/intent-service/intent-reports", params={"intent_id": intent_id}))

    def register_intent_handling_function(self, rmih_id: str, sme_service_id: str, intent_handling_capability_list: list[dict],
                                           notification_destination: str, intent_handling_scope: list[str] | None = None,
                                           supported_negotiation_functionalities: list[str] | None = None) -> dict:
        """Register an intent handling function (RMIH); `sme_service_id` names its SME service (`POST /intent-service/intent-handling-functions`); the scope and the negotiation functionalities are sent only when given."""
        body = {"rmihId": rmih_id, "smeServiceId": sme_service_id,
                "intentHandlingCapabilityList": intent_handling_capability_list,
                "notificationDestination": notification_destination}
        if intent_handling_scope is not None:
            body["intentHandlingScope"] = intent_handling_scope
        if supported_negotiation_functionalities is not None:
            body["supportedNegotiationFunctionalities"] = supported_negotiation_functionalities
        return ensure_ok(self._r1.post("/intent-service/intent-handling-functions", json=body))

    def deregister_intent_handling_function(self, rmih_id: str) -> None:
        ensure_ok(self._r1.delete(f"/intent-service/intent-handling-functions/{rmih_id}"))

    def list_intent_handling_functions(self) -> list[dict]:
        return ensure_ok(self._r1.get("/intent-service/intent-handling-functions"))


    # ---------------------------------------------------------------- Wave 10.1: autonomy dispatch
    def request_autonomy_dispatch(self, instance_id: uuid.UUID | str, expectations: list[dict], rmih_id: str,
                                  model_id: uuid.UUID | str | None = None, notification_destination: str | None = None,
                                  user_label: str | None = None, priority: int = 1) -> dict:
        """An inference outcome handed to the platform: the instance's own autonomy mode decides what happens (AUTONOMOUS creates the Intent now, ASSIST awaits the operator, SHADOW is never enacted)."""
        return ensure_ok(self._r1.post("/intent-service/autonomy-dispatches", json={
            "instanceId": str(instance_id), "expectations": expectations, "rmihId": rmih_id,
            "modelId": str(model_id) if model_id else None, "notificationDestination": notification_destination,
            "userLabel": user_label, "priority": priority,
        }))

    def get_autonomy_dispatch(self, dispatch_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/intent-service/autonomy-dispatches/{dispatch_id}"))


def _full_time(value: str) -> str:
    """An RFC 3339 full-time for a clock time: "05:00" becomes "05:00:00Z" (UTC), "05:00:00" gets "Z", and a value that already has an offset is kept."""
    if len(value) == 5:
        return f"{value}:00Z"
    return f"{value}Z" if len(value) == 8 else value


def energy_saving_expectation(object_instance: str, cells: list[dict] | None = None, max_energy_consumption: int | None = None,
                              daily_window: tuple[str, str] | None = ("00:00", "05:00"),
                              expectation_id: str = "energy-saving") -> dict:
    """The energy-saving template ("reduce energy in scope X during 00:00-05:00", HISTORY.md W6-03) as a TS 28.312 RadioNetworkExpectation.

    A RAN_SUBNETWORK object (`object_instance`, optionally narrowed to `cells` as a Cell context), the RANEnergyConsumption target (IS_LESS_THAN, the only condition the family allows) and, by default,
    a daily schedulingTime guarantee period (a TS 28.623 SchedulingTime: `timeIntervals` of RFC 3339 full-times, so every day); pass `daily_window=None` for none. With no
    `max_energy_consumption` the target value is 0.
    """
    obj: dict[str, Any] = {"objectType": "RAN_SUBNETWORK", "objectInstance": object_instance}
    if cells:
        obj["objectContexts"] = [{"contextAttribute": "Cell", "contextCondition": "IS_ALL_OF", "contextValueRange": cells}]
    expectation = {
        "expectationId": expectation_id, "expectationVerb": "DELIVER", "expectationObject": obj,
        "expectationTargets": [{"targetName": "RANEnergyConsumption", "targetCondition": "IS_LESS_THAN",
                                "targetValueRange": max_energy_consumption if max_energy_consumption is not None else 0}],
    }
    if daily_window:
        expectation["guaranteePeriods"] = [{"contextAttribute": "schedulingTime", "contextCondition": "IS_ALL_OF",
                                            "contextValueRange": {"timeIntervals": [
                                                {"intervalStart": _full_time(daily_window[0]), "intervalEnd": _full_time(daily_window[1])}]}}]
    return expectation
