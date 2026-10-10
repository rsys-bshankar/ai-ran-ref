"""smo_shared.idempotency, exactly: the thresholds, the answers' wording, the compare-and-swap, and what each helper leaves alone.

Written for the mutants the wider mutation scope (PR-V-2c) found that no test noticed. The clock is fixed, so a boundary is tested at the boundary.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_idempotency_exact.py -q
"""

import datetime
import hashlib
import json

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from smo_shared import idempotency
from smo_shared.db import Base
from smo_shared.idempotency import COMPLETED, IN_PROGRESS, IdempotencyKey

NOW = datetime.datetime(2026, 10, 7, 12, 0, 0, tzinfo=datetime.UTC)


def seconds(n):
    """Shorthand for a timedelta of `n` seconds."""
    return datetime.timedelta(seconds=n)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A SQLite session with the idempotency table, the module's clock frozen at NOW and the TTL and in-progress limits at their defaults."""
    engine = create_engine(f"sqlite:///{tmp_path / 'i.db'}", future=True)
    Base.metadata.create_all(engine, tables=[IdempotencyKey.__table__])
    monkeypatch.setattr(idempotency, "_now", lambda: NOW)
    monkeypatch.delenv("IDEMPOTENCY_KEY_TTL_SECONDS", raising=False)
    monkeypatch.delenv("IDEMPOTENCY_IN_PROGRESS_SECONDS", raising=False)
    with Session(engine) as session:
        yield session


def put(db, key="k", state=IN_PROGRESS, age=0, req_hash="h", status=None, body=None, module="m", scope="s"):
    """Helper: stores a record `age` seconds old in the given state."""
    db.add(IdempotencyKey(module=module, scope=scope, key=key, request_hash=req_hash, state=state, response_status=status, response_body=body,
                          created_at=NOW - seconds(age)))
    db.commit()


def keys(db):
    """Helper: all stored records as {key: row}, re-read from the database."""
    db.expire_all()
    return {row.key: row for row in db.scalars(select(IdempotencyKey))}


def refused(call):
    """Helper: calls `call`, expecting an HTTPException, and returns (status, title, detail)."""
    with pytest.raises(HTTPException) as caught:
        call()
    return caught.value.status_code, caught.value.detail["title"], caught.value.detail["detail"]


# ---- the request hash -------------------------------------------------------------------------------------------------------------------

def test_the_request_hash_is_the_sha256_of_the_sorted_canonical_form():
    """The request hash is the SHA-256 of a canonical JSON form with sorted keys, and values JSON cannot encode are hashed as text."""
    canonical = '{"method": "POST", "path": "/a", "payload": {"a": 2, "b": 1}}'
    assert idempotency.request_hash("POST", "/a", {"b": 1, "a": 2}) == hashlib.sha256(canonical.encode()).hexdigest()
    assert idempotency.request_hash("POST", "/a", {"a": 2, "b": 1}) == idempotency.request_hash("POST", "/a", {"b": 1, "a": 2})
    assert idempotency.request_hash("POST", "/a", {"at": NOW}) == idempotency.request_hash("POST", "/a", {"at": str(NOW)})     # not JSON: written as text


# ---- a stored answer is replayed as it was stored ----------------------------------------------------------------------------------------

def test_a_replay_carries_the_stored_status_body_and_the_replay_header_and_a_damaged_row_is_a_500(db):
    """A replay returns the stored status and body with the replay header; a COMPLETED row with no status is answered as a 500."""
    put(db, key="ok", state=COMPLETED, status=201, body={"id": 7})
    put(db, key="damaged", state=COMPLETED, status=None, body=None)
    good = idempotency._replay(db.get(IdempotencyKey, ("m", "s", "ok")))
    assert good.status_code == 201 and json.loads(good.body) == {"id": 7} and good.headers[idempotency.REPLAY_HEADER] == "true"
    assert idempotency._replay(db.get(IdempotencyKey, ("m", "s", "damaged"))).status_code == 500


# ---- starting ---------------------------------------------------------------------------------------------------------------------------

def test_a_new_key_is_reserved_and_only_rows_older_than_the_ttl_are_purged(db):
    """Reserving a key purges only records strictly older than the TTL (a record exactly one day old stays)."""
    put(db, key="exactly", age=86400)         # not older than a day: kept
    put(db, key="just-over", age=86401)       # older: purged
    put(db, key="half-over", age=86400.5)
    assert idempotency._begin(db, "m", "s", "new", "h") is None
    assert set(keys(db)) == {"exactly", "new"}


def test_the_ttl_is_the_one_in_the_environment(db, monkeypatch):
    """The purge age comes from IDEMPOTENCY_KEY_TTL_SECONDS."""
    monkeypatch.setenv("IDEMPOTENCY_KEY_TTL_SECONDS", "10")
    put(db, key="old", age=11)
    put(db, key="fresh", age=9)
    idempotency._begin(db, "m", "s", "new", "h")
    assert set(keys(db)) == {"fresh", "new"}


def test_a_key_reused_for_a_different_request_is_refused_with_its_reason(db):
    """A key whose stored request hash differs answers 422 IDEMPOTENCY_KEY_REUSED with the exact detail text."""
    put(db, req_hash="one")
    assert refused(lambda: idempotency._begin(db, "m", "s", "k", "two")) == (
        422, "IDEMPOTENCY_KEY_REUSED", "this Idempotency-Key was used for a different request (method, path or payload)")


def test_a_completed_key_is_replayed(db):
    """A COMPLETED key is replayed with its stored status."""
    put(db, state=COMPLETED, status=202, body={"a": 1})
    replay = idempotency._begin(db, "m", "s", "k", "h")
    assert replay.status_code == 202 and replay.headers[idempotency.REPLAY_HEADER] == "true"


def test_a_running_key_is_refused_until_it_is_older_than_five_minutes_and_then_taken_over(db):
    """A running key is refused up to exactly 300 s of age; beyond that it is taken over and its clock restarts."""
    put(db, key="five-minutes", age=300)
    put(db, key="just-over", age=301)
    assert refused(lambda: idempotency._begin(db, "m", "s", "five-minutes", "h")) == (
        409, "IDEMPOTENCY_KEY_IN_PROGRESS", "the first request with this Idempotency-Key has not finished; repeat later")
    assert idempotency._begin(db, "m", "s", "just-over", "h") is None       # taken over: its clock restarts
    assert keys(db)["just-over"].created_at.replace(tzinfo=datetime.UTC) == NOW


def test_the_takeover_window_is_the_one_in_the_environment_and_a_half_second_over_is_over(db, monkeypatch):
    """The takeover age comes from IDEMPOTENCY_IN_PROGRESS_SECONDS, and half a second past it counts as over."""
    monkeypatch.setenv("IDEMPOTENCY_IN_PROGRESS_SECONDS", "10")
    put(db, key="over", age=10.5)
    put(db, key="within", age=10)
    assert idempotency._begin(db, "m", "s", "over", "h") is None
    assert refused(lambda: idempotency._begin(db, "m", "s", "within", "h"))[1] == "IDEMPOTENCY_KEY_IN_PROGRESS"


def test_a_takeover_changes_only_its_own_row(db):
    """Taking over one abandoned key leaves other keys' clocks alone."""
    put(db, key="mine", age=400)
    put(db, key="other", age=400)
    assert idempotency._begin(db, "m", "s", "mine", "h") is None
    assert keys(db)["other"].created_at.replace(tzinfo=datetime.UTC) == NOW - seconds(400)


def test_a_takeover_lost_to_another_replica_is_not_a_takeover(db, monkeypatch):
    """The row read was stale: another replica already restarted its clock. The compare-and-swap finds nothing to change, so this request looks again,
    finds a running key, and is refused (it does not run twice)."""
    put(db, key="k", age=400)
    stale = IdempotencyKey(module="m", scope="s", key="k", request_hash="h", state=IN_PROGRESS, created_at=NOW - seconds(500))     # what was read
    reads = iter([stale])
    real_get = db.get
    monkeypatch.setattr(db, "get", lambda *a, **kw: next(reads, None) or real_get(*a, **kw))
    db.execute(update(IdempotencyKey).values(created_at=NOW - seconds(5)))        # the other replica's takeover
    db.commit()
    assert refused(lambda: idempotency._begin(db, "m", "s", "k", "h")) == (
        409, "IDEMPOTENCY_KEY_IN_PROGRESS", "the first request with this Idempotency-Key has not finished; repeat later")


def test_a_reservation_lost_to_a_concurrent_request_looks_again_and_replays(db, monkeypatch):
    """When the insert loses the race to a concurrent request, the second look finds that request's completed answer and replays it."""
    put(db, state=COMPLETED, status=201, body={"won": True})
    reads, real_get = [], db.get

    def get(*args, **kwargs):          # the first read saw no row; the insert then collides with the one committed meanwhile
        reads.append(1)
        return None if len(reads) == 1 else real_get(*args, **kwargs)

    monkeypatch.setattr(db, "get", get)
    replay = idempotency._begin(db, "m", "s", "k", "h")
    assert replay.status_code == 201 and json.loads(replay.body) == {"won": True} and len(reads) == 2


def test_a_key_that_keeps_vanishing_is_refused_not_looped_on(db, monkeypatch):
    """Two passes, then 'being taken over': a row read as stale twice whose takeover is lost both times."""
    stale = IdempotencyKey(module="m", scope="s", key="k", request_hash="h", state=IN_PROGRESS, created_at=NOW - seconds(900))
    reads = iter([stale, stale])
    real_get = db.get
    monkeypatch.setattr(db, "get", lambda *a, **kw: next(reads, None) or real_get(*a, **kw))
    assert refused(lambda: idempotency._begin(db, "m", "s", "k", "h")) == (
        409, "IDEMPOTENCY_KEY_IN_PROGRESS", "the Idempotency-Key is being taken over; repeat later")


# ---- finishing --------------------------------------------------------------------------------------------------------------------------

def test_releasing_deletes_only_this_running_key(db):
    """Releasing deletes the caller's IN_PROGRESS record only: other keys and COMPLETED records stay."""
    put(db, key="mine")
    put(db, key="other-running")
    put(db, key="mine-done", state=COMPLETED, status=200, body={})
    idempotency._release(db, "m", "s", "mine")
    assert set(keys(db)) == {"other-running", "mine-done"}
    idempotency._release(db, "m", "s", "mine-done")             # a completed key is never released
    assert "mine-done" in keys(db)


def test_completing_stores_a_response_s_status_and_json_body_or_none_for_an_empty_one_or_the_encoded_result(db):
    """Completing stores a Response's own status and JSON body (None for an empty body) or, for a plain result, the route's status and the JSON-encoded
    value.
    """
    for key in ("a", "b", "c"):
        put(db, key=key)
    idempotency._complete(db, "m", "s", "a", 200, JSONResponse(status_code=202, content={"x": [1, 2]}))
    idempotency._complete(db, "m", "s", "b", 200, Response(status_code=204))
    idempotency._complete(db, "m", "s", "c", 201, {"when": NOW})
    rows = keys(db)
    assert (rows["a"].state, rows["a"].response_status, rows["a"].response_body) == (COMPLETED, 202, {"x": [1, 2]})
    assert (rows["b"].response_status, rows["b"].response_body) == (204, None)
    assert rows["c"].response_status == 201 and rows["c"].response_body["when"].startswith("2026-10-07T12:00:00")


# ---- the key itself and the decorator ---------------------------------------------------------------------------------------------------

class _Request:
    """Minimal stand-in for a Starlette request: headers (with the optional Idempotency-Key), a POST method and path `/p`."""
    def __init__(self, key):
        self.headers = {} if key is None else {idempotency.HEADER_NAME: key}
        self.method, self.url = "POST", type("U", (), {"path": "/p"})()


def test_a_key_of_255_printable_characters_is_accepted_and_longer_or_empty_or_unprintable_ones_say_why(db):
    """A 255 character key is accepted; longer, empty and non-printable keys are 422 with the exact message; no header just runs the route."""
    ran = []
    assert idempotency.run_idempotent(_Request("k" * 255), db, "m", 201, {}, lambda: ran.append(1) or "done") == "done"
    for bad in ("k" * 256, "", "tab\there", "new\nline"):
        assert refused(lambda bad=bad: idempotency.run_idempotent(_Request(bad), db, "m", 201, {}, lambda: "never")) == (
            422, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must be 1 to 255 printable characters")
    assert idempotency.run_idempotent(_Request(None), db, "m", 201, {}, lambda: "no key, runs") == "no key, runs"


def test_a_failed_run_releases_its_key_and_re_raises(db):
    """When the route raises, its reservation is released and the error propagates."""
    def boom():
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        idempotency.run_idempotent(_Request("k1"), db, "m", 201, {}, boom)
    assert keys(db) == {}


def test_the_decorator_needs_both_the_request_and_the_session():
    """@idempotent refuses a function missing `request` or `db` (with the exact message) and accepts one that has both."""
    with pytest.raises(TypeError, match="needs `request: Request` and `db: Session` parameters"):
        idempotency.idempotent("m")(lambda request: None)
    with pytest.raises(TypeError):
        idempotency.idempotent("m")(lambda db: None)
    with pytest.raises(TypeError):
        idempotency.idempotent("m")(lambda: None)
    idempotency.idempotent("m")(lambda request, db: None)


def test_a_route_with_no_stated_status_stores_and_replays_200(db):
    """Without an explicit status the decorator stores and replays 200."""
    from fastapi import Depends, FastAPI, Request
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/things")
    @idempotency.idempotent("demo")
    def create(request: Request, db: Session = Depends(lambda: db)):
        # Test route behind @idempotent with the default status; not part of any published API.
        return {"made": True}

    client = TestClient(app)
    assert client.post("/things", headers={"Idempotency-Key": "k"}).status_code == 200
    again = client.post("/things", headers={"Idempotency-Key": "k"})
    assert again.status_code == 200 and again.headers[idempotency.REPLAY_HEADER] == "true"
    stored = keys(db)
    assert [row.response_status for row in stored.values()] == [200]
