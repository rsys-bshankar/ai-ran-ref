"""PR-ST-5: nothing the BFF needs to agree on between instances lives in a process.

Every instance below has its own `Database` object (its own engine and connections) on one SQLite
file, which is what several BFF replicas on a shared `GUI_DATABASE_URL` look like.
"""

import asyncio
import json
import os
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import app.main as main
from app.config import Settings
from app.db import Base, Database, GuiSetting, GuiUser, LoginFailure, SmoCredential
from app.main import SESSION_COOKIE, create_app, seed_users
from app.smo_client import R1Gateway
from test_main import PASSWORDS, R1, SME, FakeSmo


def make_instance(path, smo, jwt_secret="", **settings):
    """Builds one BFF instance: its own `Database` object (own engine) on the shared SQLite file at `path`, its own settings (`jwt_secret` empty means the key is shared through the database) and a gateway on the fake SMO. Returns the app, the database and the settings.
    """
    db = Database(f"sqlite:///{path}")
    cfg = Settings(r1_url=R1, jwt_secret=jwt_secret, cookie_secure=False, admin_password=PASSWORDS["admin"],
                   operator_password=PASSWORDS["operator"], viewer_password=PASSWORDS["viewer"], **settings)
    app = create_app(cfg, db=db, gateway=R1Gateway(R1, db, transport=httpx.MockTransport(smo.handler)))
    return app, db, cfg


@pytest.fixture
def smo():
    return FakeSmo()


@pytest.fixture
def path(tmp_path):
    return tmp_path / "gui.db"


def login(client, username="operator", password=None):
    return client.post("/api/login", json={"username": username, "password": password or PASSWORDS[username]})


def hand_over_session(source: TestClient, target: TestClient) -> None:
    target.cookies.set(SESSION_COOKIE, source.cookies.get(SESSION_COOKIE), path="/api")


# ---------------------------------------------------------------- the session signing key

def test_instances_without_gui_jwt_secret_sign_with_one_stored_key(path, smo):
    """Two instances started without `GUI_JWT_SECRET` begin with different random keys but, once started, use the one key stored in the database, so a session from one is accepted by the other.
    """
    a, _, cfg_a = make_instance(path, smo)
    b, _, cfg_b = make_instance(path, smo)
    assert cfg_a.jwt_secret != cfg_b.jwt_secret                 # each starts with its own random value ...
    with TestClient(a) as client_a, TestClient(b) as client_b:   # ... and startup replaces it with the stored one
        assert cfg_a.jwt_secret == cfg_b.jwt_secret
        assert login(client_a).status_code == 200
        hand_over_session(client_a, client_b)
        me = client_b.get("/api/me")
        assert me.status_code == 200 and me.json()["username"] == "operator"


def test_a_restart_keeps_sessions_signed_with_the_stored_key(path, smo):
    """A restarted instance still accepts a session issued before the restart, because the key is read from the database and not generated again.
    """
    first, _, _ = make_instance(path, smo)
    with TestClient(first) as old:
        assert login(old).status_code == 200
        cookie = old.cookies.get(SESSION_COOKIE)
    restarted, _, _ = make_instance(path, smo)
    with TestClient(restarted) as new:
        new.cookies.set(SESSION_COOKIE, cookie, path="/api")
        assert new.get("/api/me").status_code == 200


def test_an_explicit_gui_jwt_secret_is_used_as_is_and_stores_nothing(path, smo):
    """When `GUI_JWT_SECRET` is set the instance uses it unchanged and writes no key to the database."""
    a, db, cfg = make_instance(path, smo, jwt_secret="operator-managed")
    with TestClient(a):
        assert cfg.jwt_secret == "operator-managed"
    with db.session() as s:
        assert s.get(GuiSetting, "jwt_secret") is None


def test_instances_with_different_explicit_secrets_do_not_accept_each_others_sessions(path, smo):
    """The control: without a shared key, a session from one instance is refused by the other."""
    a, _, _ = make_instance(path, smo, jwt_secret="secret-a")
    b, _, _ = make_instance(path, smo, jwt_secret="secret-b")
    with TestClient(a) as client_a, TestClient(b) as client_b:
        assert login(client_a).status_code == 200
        hand_over_session(client_a, client_b)
        assert client_b.get("/api/me").status_code == 401


def run_racing(workers, work):
    """Run `work(n)` in `workers` threads released together; return their results, failing on any thread error."""
    barrier = threading.Barrier(workers, timeout=20)
    results, errors, lock = [], [], threading.Lock()

    def target(n):
        try:
            barrier.wait()
            outcome = work(n)
            with lock:
                results.append(outcome)
        except Exception as exc:      # noqa: BLE001 - reported below, so a failing thread cannot strand the others
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=target, args=(n,)) for n in range(workers)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert errors == []
    return results


def test_racing_instances_all_end_up_with_the_first_stored_value(path):
    """Eight instances asking for the same setting at once all get one value, the first one stored."""
    dbs = [Database(f"sqlite:///{path}") for _ in range(8)]
    results = run_racing(8, lambda n: dbs[n].shared_setting("jwt_secret", f"candidate-{n}"))
    assert len(results) == 8 and len(set(results)) == 1


def test_instances_starting_together_on_one_new_database_all_create_the_schema(path):
    """Instances that start together on an empty database all manage to create the tables despite the check-then-create race."""
    results = run_racing(6, lambda n: Database(f"sqlite:///{path}").shared_setting("k", f"v{n}"))
    assert len(results) == 6 and len(set(results)) == 1


# ---------------------------------------------------------------- the login lockout

def test_failed_logins_count_across_instances(path, smo):
    """Failures on two instances add up in the shared counter, so the lockout applies on both after five in all."""
    a, _, _ = make_instance(path, smo)
    b, _, _ = make_instance(path, smo)
    with TestClient(a) as client_a, TestClient(b) as client_b:
        for _ in range(3):
            assert login(client_a, "operator", "wrong").status_code == 401
        for _ in range(2):
            assert login(client_b, "operator", "wrong").status_code == 401
        # five failures in all: the sixth attempt is locked out on either instance, even with the right password
        locked_b = login(client_b, "operator")
        locked_a = login(client_a, "operator")
        assert locked_b.status_code == locked_a.status_code == 429
        assert locked_b.json()["title"] == "TOO_MANY_ATTEMPTS"


def test_a_successful_login_on_one_instance_clears_the_count_for_all(path, smo):
    """A successful sign-in on one instance resets the failure counter that the other instance sees."""
    a, _, _ = make_instance(path, smo)
    b, _, _ = make_instance(path, smo)
    with TestClient(a) as client_a, TestClient(b) as client_b:
        for _ in range(4):
            login(client_a, "operator", "wrong")
        assert login(client_b, "operator").status_code == 200
        for _ in range(4):
            assert login(client_a, "operator", "wrong").status_code == 401   # the count started again from zero
        assert login(client_a, "operator").status_code == 200


def test_an_unknown_username_is_locked_exactly_like_a_real_one(path, smo):
    """The lockout must not reveal which usernames exist."""
    a, _, _ = make_instance(path, smo)
    with TestClient(a) as client:
        for _ in range(5):
            assert login(client, "no-such-user", "wrong").status_code == 401
        assert login(client, "no-such-user", "wrong").status_code == 429


def test_the_counting_window_restarts_after_the_lockout_period(path):
    """Once the window has passed the account is unlocked, and the next failure restarts the counter at one; clearing removes the row.
    """
    db = Database(f"sqlite:///{path}")
    for n in range(5):
        db.record_login_failure("operator", 300, now=1000.0 + n)
    assert db.login_locked("operator", 5, 300, now=1010.0) is True
    assert db.login_locked("operator", 5, 300, now=1000.0 + 300) is False        # first failure + window has passed
    db.record_login_failure("operator", 300, now=1400.0)                          # an expired counter restarts at 1
    with db.session() as s:
        row = s.get(LoginFailure, "operator")
        assert (row.count, row.first_failed_at) == (1, 1400.0)
    db.clear_login_failures("operator")
    with db.session() as s:
        assert s.get(LoginFailure, "operator") is None


def test_concurrent_failures_are_all_counted(path):
    """Twelve failures recorded at the same moment from separate connections are all counted (the increment is done in SQL, not read and written back).
    """
    dbs = [Database(f"sqlite:///{path}") for _ in range(12)]
    run_racing(12, lambda n: dbs[n].record_login_failure("operator", 300, now=1000.0))
    with Database(f"sqlite:///{path}").session() as s:
        assert s.get(LoginFailure, "operator").count == 12


# ---------------------------------------------------------------- first-boot seeding

def test_two_instances_seeding_one_empty_database_do_not_crash_and_leave_one_password_file(path, tmp_path, monkeypatch):
    """Two instances that both found the user table empty and seed it at once do not crash, and the loser deletes the generated password file so the one left matches the stored admin.
    """
    barrier = threading.Barrier(2, timeout=20)
    seen = threading.local()
    real_hash = main.hash_password

    def hash_after_both_saw_an_empty_table(password):
        if not getattr(seen, "waited", False):
            seen.waited = True
            barrier.wait()                 # both instances have passed the "is the table empty?" check
        return real_hash(password)

    monkeypatch.setattr(main, "hash_password", hash_after_both_saw_an_empty_table)
    files = [tmp_path / "pw-a", tmp_path / "pw-b"]
    dbs = [Database(f"sqlite:///{path}") for _ in files]

    def seed(n):
        cfg = Settings(r1_url=R1, jwt_secret="x", admin_password="", initial_password_file=str(files[n]))
        seed_users(dbs[n], cfg)

    outer = threading.Barrier(2, timeout=20)
    errors, lock = [], threading.Lock()

    def target(n):
        try:
            outer.wait()
            seed(n)
        except Exception as exc:     # noqa: BLE001 - the point is that nothing is raised
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=target, args=(n,)) for n in range(2)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert errors == []
    with Database(f"sqlite:///{path}").session() as s:
        admin = s.get(GuiUser, "admin")
    remaining = [f for f in files if f.exists()]
    assert len(remaining) == 1                                    # the loser removed the password it generated
    assert main.verify_password(remaining[0].read_text().strip(), admin.password_hash)


# ---------------------------------------------------------------- the BFF's own SME invoker

class Sme:
    """R1 /bootstrap plus SME's invoker registry and token endpoint, with an `await` in registration so that two
    gateways are both mid-onboarding before either stores its identity."""

    def __init__(self):
        self.known: set[str] = set()
        self.registrations = 0
        self.deleted: list[str] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{R1}/bootstrap":
            return httpx.Response(200, json={"apiEndpoints": [{"tokenEndPoint": {"uri": f"{SME}/oauth2/token"}}]})
        if url == f"{SME}/invoker-registrations" and request.method == "POST":
            await asyncio.sleep(0)
            self.registrations += 1
            invoker_id = f"api-invoker-{self.registrations}"
            self.known.add(invoker_id)
            return httpx.Response(201, json={"apiInvokerId": invoker_id, "onboardingSecret": "s3cret"})
        if request.method == "DELETE" and url.startswith(f"{SME}/invoker-registrations/"):
            invoker_id = url.rsplit("/", 1)[1]
            self.deleted.append(invoker_id)
            self.known.discard(invoker_id)
            return httpx.Response(204)
        assert url == f"{SME}/oauth2/token"
        if json.loads(request.content)["client_id"] not in self.known:
            return httpx.Response(400, json={"error": "invalid_client"})
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})


def gateway(path, sme):
    """Builds an `R1Gateway` on the fake SME over its own database object on the shared file; returns the gateway and the database.
    """
    db = Database(f"sqlite:///{path}")
    return R1Gateway(R1, db, transport=httpx.MockTransport(sme.handler)), db


def test_two_instances_onboarding_at_once_keep_one_invoker_and_offboard_the_duplicate(path):
    """Two gateways registering at SME at the same moment keep one stored invoker identity, and the loser deletes the duplicate it registered.
    """
    sme = Sme()
    (first, _), (second, db_second) = gateway(path, sme), gateway(path, sme)

    async def both():
        return await asyncio.gather(first.token(), second.token())

    assert asyncio.run(both()) == ["tok", "tok"]
    assert sme.registrations == 2 and len(sme.deleted) == 1
    with db_second.session() as s:
        stored = s.get(SmoCredential, 1)
    assert stored.api_invoker_id in sme.known and stored.api_invoker_id not in sme.deleted
    assert sme.known == {stored.api_invoker_id}


def test_the_bff_presents_the_enrollment_secret_when_it_registers(path, monkeypatch, tmp_path):
    """PR-SEC-14: the BFF is an SMO module: SME records it as internal only when it presents the secret every module mounts."""
    secret_file = tmp_path / "enrollment_secret"
    secret_file.write_text("the-enrollment-secret\n")
    monkeypatch.setenv("SMO_ENROLLMENT_SECRET_FILE", str(secret_file))
    sent = []

    class Recording(Sme):
        async def handler(self, request):
            if str(request.url) == f"{SME}/invoker-registrations" and request.method == "POST":
                sent.append(request.headers.get("x-smo-enrollment"))
            return await super().handler(request)

    sme = Recording()
    bff, _ = gateway(path, sme)
    assert asyncio.run(bff.token()) == "tok"
    assert sent == ["the-enrollment-secret"]


def test_without_a_secret_the_bff_sends_no_enrollment_header(path, monkeypatch):
    """With no enrollment secret configured, the registration request carries no enrollment header."""
    monkeypatch.delenv("SMO_ENROLLMENT_SECRET", raising=False)
    monkeypatch.delenv("SMO_ENROLLMENT_SECRET_FILE", raising=False)
    sent = []

    class Recording(Sme):
        async def handler(self, request):
            if str(request.url) == f"{SME}/invoker-registrations" and request.method == "POST":
                sent.append(request.headers.get("x-smo-enrollment"))
            return await super().handler(request)

    bff, _ = gateway(path, Recording())
    asyncio.run(bff.token())
    assert sent == [None]


def test_an_invoker_sme_forgot_is_replaced_once_and_the_other_instance_adopts_it(path):
    """When SME forgets the invoker, the instances register a replacement once between them and both end up using the stored one.
    """
    sme = Sme()
    (first, _), (second, db) = gateway(path, sme), gateway(path, sme)
    asyncio.run(first.token())
    assert sme.registrations == 1
    sme.known.clear()                                            # SME lost its registry

    async def both():
        return await first.token(force_refresh=True), await second.token(force_refresh=True)

    assert asyncio.run(both()) == ("tok", "tok")
    assert sme.registrations == 2                                # one replacement, not one per instance
    with db.session() as s:
        assert s.get(SmoCredential, 1).api_invoker_id == "api-invoker-2"


def test_an_existing_database_from_before_this_change_gains_the_new_tables(path):
    """A BFF volume made by the earlier code has no gui_setting or gui_login_failure; opening it adds them."""
    from sqlalchemy import create_engine, inspect

    from app.db import AuditEntry, Base, SmoCredential
    old = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(old, tables=[GuiUser.__table__, AuditEntry.__table__, SmoCredential.__table__])
    assert "gui_setting" not in inspect(old).get_table_names()
    old.dispose()

    db = Database(f"sqlite:///{path}")
    assert {"gui_setting", "gui_login_failure"} <= set(inspect(db.engine).get_table_names())
    assert db.shared_setting("jwt_secret", "x") == "x"


# ---------------------------------------------------------------- the shared-database operations, SQLite and real Postgres

@pytest.fixture(params=["sqlite", "postgres"])
def url(request, tmp_path):
    """The database several BFF instances would share: a SQLite file, and real Postgres when
    SMO_TEST_POSTGRES_URL is set (CI's migration-postgres job)."""
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'shared.db'}"
        return
    if not os.environ.get("SMO_TEST_POSTGRES_URL"):
        pytest.skip("SMO_TEST_POSTGRES_URL not set")
    engine = create_engine(os.environ["SMO_TEST_POSTGRES_URL"])
    Base.metadata.drop_all(engine)
    yield os.environ["SMO_TEST_POSTGRES_URL"]
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_the_stored_setting_is_one_value_under_a_race_on_either_database(url):
    """The shared-setting race has one winner on SQLite and, when `SMO_TEST_POSTGRES_URL` is set, on Postgres."""
    dbs = [Database(url) for _ in range(8)]
    results = run_racing(8, lambda n: dbs[n].shared_setting("jwt_secret", f"candidate-{n}"))
    assert len(set(results)) == 1


def test_no_failed_login_is_lost_under_a_race_on_either_database(url):
    """Concurrent failure counting loses no failure on SQLite or Postgres."""
    dbs = [Database(url) for _ in range(12)]
    run_racing(12, lambda n: dbs[n].record_login_failure("operator", 300, now=1000.0))
    with dbs[0].session() as s:
        assert s.get(LoginFailure, "operator").count == 12


def test_the_sme_credential_has_one_first_writer_and_one_replacer_on_either_database(url):
    """Of eight instances storing the SME credential at once exactly one wins, and of eight replacing a stale one exactly one wins, on SQLite and Postgres.
    """
    dbs = [Database(url) for _ in range(8)]
    firsts = run_racing(8, lambda n: dbs[n].store_smo_credential(f"inv-{n}", "s"))
    assert sorted(firsts) == [False] * 7 + [True]
    with dbs[0].session() as s:
        stale = s.get(SmoCredential, 1).api_invoker_id
    replaced = run_racing(8, lambda n: dbs[n].store_smo_credential(f"new-{n}", "s", stale_invoker_id=stale))
    assert sorted(replaced) == [False] * 7 + [True]

