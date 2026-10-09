"""Who a request is for (smo_shared/invoker.py): an rApp's id travels through the SMO modules acting for it, so the per-rApp safeguards at RAN NF OAM
apply to an rApp that writes through DME, and an rApp cannot pose as another."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from smo_shared import invoker, r1_client
from smo_shared.correlation import apply_correlation_id
from smo_shared.invoker import INVOKER_ID_HEADER, ON_BEHALF_OF_HEADER, get_originator, invoker_id, originator_of
from smo_shared.roles import ROLE_HEADER

RAPP = {ROLE_HEADER: "rapp", INVOKER_ID_HEADER: "es-client"}
MODULE = {ROLE_HEADER: "internal", INVOKER_ID_HEADER: "dme-client"}
MODULE_FOR_RAPP = {**MODULE, ON_BEHALF_OF_HEADER: "es-client"}


def test_the_originator_is_the_rapp_that_called_or_whom_a_module_passed_on():
    assert originator_of(RAPP) == "es-client"
    assert originator_of(MODULE_FOR_RAPP) == "es-client"                  # a module acting for an rApp
    assert originator_of(MODULE) is None                                    # a module acting on its own account
    assert originator_of({}) is None                                        # not through R1 at all


def test_an_rapp_cannot_name_somebody_else_as_the_originator():
    assert originator_of({**RAPP, ON_BEHALF_OF_HEADER: "other-client"}) == "es-client"


def _request(headers):
    app = FastAPI()
    seen = {}

    @app.get("/who")
    def who(request: Request):
        seen["invoker"] = invoker_id(request)
        return {}

    TestClient(app).get("/who", headers=headers)
    return seen["invoker"]


def test_the_invoker_the_safeguards_see_is_the_rapp_behind_a_module():
    assert _request(RAPP) == "es-client"
    assert _request(MODULE_FOR_RAPP) == "es-client"                        # DME's call to RAN NF OAM is the rApp's
    assert _request(MODULE) == "dme-client"                                 # a module on its own account is itself
    assert _request({}) is None


def test_the_header_is_only_believed_from_an_internal_caller():
    assert _request({**RAPP, ON_BEHALF_OF_HEADER: "other-client"}) == "es-client"
    assert _request({INVOKER_ID_HEADER: "x", ON_BEHALF_OF_HEADER: "other-client"}) == "x"        # no role stamp: not through R1, not trusted


@pytest.fixture
def service():
    """A service as every module is built: correlation + invoker context installed, one route that calls onward through R1Client."""
    app = FastAPI()
    apply_correlation_id(app)
    sent = {}

    @app.get("/inside")
    def inside():
        sent["originator"] = get_originator()
        sent["headers"] = r1_client.R1Client(bearer_token="t")._headers()
        return {}

    return TestClient(app), sent


def test_r1client_tells_the_next_module_who_the_request_is_for(service):
    client, sent = service
    client.get("/inside", headers=RAPP)
    assert sent["originator"] == "es-client" and sent["headers"][ON_BEHALF_OF_HEADER] == "es-client"


def test_the_chain_holds_across_modules_and_is_not_invented(service):
    client, sent = service
    client.get("/inside", headers=MODULE_FOR_RAPP)
    assert sent["headers"][ON_BEHALF_OF_HEADER] == "es-client"
    client.get("/inside", headers=MODULE)                                   # a module acting for nobody says nothing
    assert ON_BEHALF_OF_HEADER not in sent["headers"]
    client.get("/inside")
    assert ON_BEHALF_OF_HEADER not in sent["headers"]


def test_outside_a_request_nothing_is_added():
    assert get_originator() is None
    assert ON_BEHALF_OF_HEADER not in r1_client.R1Client(bearer_token="t")._headers()


def test_one_request_does_not_leak_its_originator_into_the_next(service):
    client, sent = service
    client.get("/inside", headers=RAPP)
    client.get("/inside", headers=MODULE)
    assert sent["originator"] is None and invoker.get_originator() is None


def test_a_module_can_act_on_its_own_account_for_a_while_inside_a_request():
    """What the platform does about an rApp (not for it): R1Client adds neither the id nor the claim, and the request goes on as the rApp's afterwards."""
    app = FastAPI()
    apply_correlation_id(app)
    seen = {}

    @app.get("/inside")
    def inside():
        seen["before"] = get_originator()
        with invoker.on_own_account():
            seen["during"] = get_originator()
            seen["headers_during"] = r1_client.R1Client(bearer_token="t")._headers()
        seen["after"] = get_originator()
        seen["headers_after"] = r1_client.R1Client(bearer_token="t")._headers()
        return {}

    TestClient(app).get("/inside", headers={**RAPP, "X-R1-Scope": '{"regions":["eu"]}'})
    assert (seen["before"], seen["during"], seen["after"]) == ("es-client", None, "es-client")
    assert ON_BEHALF_OF_HEADER not in seen["headers_during"] and "X-R1-On-Behalf-Scope" not in seen["headers_during"]
    assert seen["headers_after"][ON_BEHALF_OF_HEADER] == "es-client" and seen["headers_after"]["X-R1-On-Behalf-Scope"] == '{"regions":["eu"]}'


def test_leaving_the_own_account_block_by_an_error_restores_the_originator():
    app = FastAPI()
    apply_correlation_id(app)
    seen = {}

    @app.get("/inside")
    def inside():
        try:
            with invoker.on_own_account():
                raise RuntimeError("boom")
        except RuntimeError:
            seen["after"] = get_originator()
        return {}

    TestClient(app).get("/inside", headers=RAPP)
    assert seen["after"] == "es-client"
