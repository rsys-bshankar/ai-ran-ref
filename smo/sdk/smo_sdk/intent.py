"""sdk.intent — a thin client over Intent Service (`intent-service/`),
TS 28.312 Intent NRM.
"""

import uuid

from ._common import BaseClient, ensure_ok


class IntentClient(BaseClient):
    def create_intent(self, expectations: list[dict], priority: int = 1, rmio_id: str = "",
                       intent_mgmt_purpose: str = "FULFILMENT_WITHOUT_NEGOTIATION",
                       intent_handling_scope: str | None = None) -> dict:
        return ensure_ok(self._r1.post("/intent-service/intents", json={
            "expectations": expectations, "priority": priority, "rmioId": rmio_id,
            "intentMgmtPurpose": intent_mgmt_purpose, "intentHandlingScope": intent_handling_scope,
        }))

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

    def publish_intent_report(self, intent_id: uuid.UUID | str, fulfilment_report: dict,
                               conflict_reports: list | None = None) -> dict:
        return ensure_ok(self._r1.post("/intent-service/intent-reports", json={
            "intentId": str(intent_id), "fulfilmentReport": fulfilment_report, "conflictReports": conflict_reports,
        }))

    def list_intent_reports(self, intent_id: uuid.UUID | str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/intent-service/intent-reports", params={"intent_id": intent_id}))

    def register_intent_handling_function(self, rmih_id: str, sme_service_id: str, capabilities: list[dict],
                                           notification_callback_uri: str, intent_handling_scope: list[str] | None = None) -> dict:
        return ensure_ok(self._r1.post("/intent-service/intent-handling-functions", json={
            "rmihId": rmih_id, "smeServiceId": sme_service_id, "capabilities": capabilities,
            "notificationCallbackUri": notification_callback_uri, "intentHandlingScope": intent_handling_scope,
        }))

    def deregister_intent_handling_function(self, rmih_id: str) -> None:
        ensure_ok(self._r1.delete(f"/intent-service/intent-handling-functions/{rmih_id}"))

    def list_intent_handling_functions(self) -> list[dict]:
        return ensure_ok(self._r1.get("/intent-service/intent-handling-functions"))
