"""The console preferences (`GET`/`PUT /api/me/preferences`, app/preferences.py) and the summary counts (`GET /api/summary/{page}`, app/summary.py).

R1 Termination and SME are faked by test_main.py's FakeSmo, extended here with list routes that answer a paged envelope whose `total` depends on the
filter, so the BFF's real gateway client runs unmodified. Run: `PYTHONPATH=.:../shared python -m pytest tests/test_preferences_summary.py -q`.
"""

import httpx
import pytest

from app import summary
from app.config import Settings
from app.db import Database
from app.main import create_app, seed_users
from app.smo_client import R1Gateway
from test_main import PASSWORDS, R1, FakeSmo, login


class CountingSmo(FakeSmo):
    """FakeSmo whose list routes answer `{"items": [], "total": N}`: N from `totals[(path, filter)]`, 0 when not listed. A module in `down_modules` fails."""

    def __init__(self):
        super().__init__()
        self.totals: dict[tuple[str, str], int] = {("/ran-nf-oam/alarms", "severity=critical"): 1234, ("/ran-nf-oam/alarms", ""): 5000,
                                                   ("/ran-nf-oam/rapp-approvals", "status=PENDING"): 18}
        self.count_calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        module = path.split("/")[1]
        if request.url.host == "r1-termination" and not path.endswith(("/health", "/ready", "/version", "/bootstrap")) and request.method == "GET" \
                and request.url.params.get("limit") == "1":
            if request.headers.get("authorization", "").removeprefix("Bearer ") not in self.issued:
                return httpx.Response(401, json={"title": "UNAUTHORIZED"})
            if module in self.down_modules:
                raise httpx.ConnectError("down")
            self.count_calls.append(request)
            filt = "&".join(f"{k}={v}" for k, v in request.url.params.multi_items() if k not in ("limit", "since"))
            return httpx.Response(200, json={"items": [], "total": self.totals.get((path, filt), 0), "limit": 1, "offset": 0})
        return super().handler(request)


@pytest.fixture
def smo():
    return CountingSmo()


@pytest.fixture
def app(smo):
    cfg = Settings(r1_url=R1, jwt_secret="test-secret", cookie_secure=False, admin_password=PASSWORDS["admin"],
                   operator_password=PASSWORDS["operator"], viewer_password=PASSWORDS["viewer"])
    db = Database("sqlite://")
    seed_users(db, cfg)
    return create_app(cfg, db=db, gateway=R1Gateway(R1, db, transport=httpx.MockTransport(smo.handler)))


# ------------------------------------------------------------------ preferences

def test_preferences_default_until_saved_and_need_a_session(app):
    """A user who never saved gets the defaults (dark, m, volt, 50 rows); without a session the route is a 401."""
    from fastapi.testclient import TestClient
    assert TestClient(app).get("/api/me/preferences").status_code == 401
    body = login(app, "viewer").get("/api/me/preferences").json()
    assert body["theme"] == "dark" and body["size"] == "m" and body["accent"] == "volt" and body["rowsPerPage"] == 50 and body["startPage"] == "/"


def test_saved_preferences_come_back_and_belong_to_their_user_only(app):
    """A save is read back on the next GET, by that user only: another user still sees the defaults."""
    viewer = login(app, "viewer")
    saved = viewer.put("/api/me/preferences", json={"theme": "light", "accent": "radisys", "size": "xl", "rowsPerPage": 100, "startPage": "/alarms"})
    assert saved.status_code == 200 and saved.json()["accent"] == "radisys"
    again = viewer.get("/api/me/preferences").json()
    assert (again["theme"], again["accent"], again["size"], again["rowsPerPage"], again["startPage"]) == ("light", "radisys", "xl", 100, "/alarms")
    assert login(app, "operator").get("/api/me/preferences").json()["theme"] == "dark"


@pytest.mark.parametrize("bad", [{"theme": "neon"}, {"accent": "#ff0000"}, {"size": "xxl"}, {"rowsPerPage": 1000}, {"startPage": "https://evil"},
                                 {"colour": "red"}])
# Values outside their closed set, and unknown fields, are refused, because theme, accent and size are written onto <html> as they are.
def test_preferences_outside_their_sets_are_refused(app, bad):
    """Every field is validated against its enum and an unknown field is a 422, so nothing unvalidated reaches the page."""
    assert login(app, "viewer").put("/api/me/preferences", json=bad).status_code == 422


def test_saving_preferences_needs_the_csrf_token(app):
    """The PUT is a state change: without the double-submit token it is refused like every other one."""
    client = login(app, "viewer")
    del client.headers["X-CSRF-Token"]
    assert client.put("/api/me/preferences", json={"theme": "light"}).status_code == 403


def test_deleting_a_user_forgets_their_preferences(app):
    """A deleted user's preferences go with the account, so a new account of the same name starts on the defaults."""
    admin = login(app, "admin")
    assert admin.post("/api/admin/users", json={"username": "kim", "password": "a-long-password", "role": "viewer"}).status_code == 201
    app.state.db.save_preferences("kim", '{"theme": "light"}')
    assert admin.delete("/api/admin/users/kim").status_code == 204
    assert app.state.db.preferences("kim") is None


# ------------------------------------------------------------------ summary counts

def test_summary_reads_true_totals_not_a_first_page(app, smo):
    """Counts are each module's own `total` for a one-row page with the filter, so 1,234 critical alarms read 1,234, not 100."""
    body = login(app, "viewer").get("/api/summary/alarms").json()
    assert body["counts"]["alarms.critical"] == 1234 and body["counts"]["alarms.total"] == 5000 and body["counts"]["alarms.major"] == 0
    assert body["partial"] == []
    assert all(c.url.params["limit"] == "1" for c in smo.count_calls)


def test_summary_is_cached_and_shared_between_users(app, smo):
    """A second ask within the cache time, by any user, costs no upstream call."""
    login(app, "viewer").get("/api/summary/nav")
    calls = len(smo.count_calls)
    assert login(app, "operator").get("/api/summary/nav").json()["counts"]["approvals.PENDING"] == 18
    assert len(smo.count_calls) == calls


def test_a_module_that_does_not_answer_makes_the_summary_partial_not_failed(app, smo):
    """A down module's counts are null and the module is named in `partial`; the other counts still come back."""
    smo.down_modules.add("focom")
    body = login(app, "viewer").get("/api/summary/alarms").json()
    assert body["counts"]["ocloudAlarms.total"] is None and body["partial"] == ["focom"] and body["counts"]["alarms.critical"] == 1234


def test_decision_counts_are_bounded_to_the_last_day(app, smo):
    """The 24-hour decision counts send a `since` one day back, so the count never scans the whole decision history."""
    login(app, "viewer").get("/api/summary/decisions")
    sent = [c for c in smo.count_calls if c.url.path == "/ran-nf-oam/decision-records"]
    assert sent and all("since" in c.url.params for c in sent)


def test_an_unknown_summary_page_is_a_404_and_a_session_is_needed(app):
    """Only the pages of `PAGES` exist; the route needs a session like every other."""
    from fastapi.testclient import TestClient
    assert login(app, "viewer").get("/api/summary/nope").status_code == 404
    assert TestClient(app).get("/api/summary/nav").status_code == 401
    assert set(summary.PAGES) >= {"nav", "dashboard", "alarms", "rapps"}
