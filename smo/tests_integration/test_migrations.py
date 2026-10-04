"""Schema migrations (PR-OPS-1.2, 1.3): one linear history, a baseline equal to 001_init.sql, stamping of existing databases.

The history tests need no database. The rest need SMO_TEST_POSTGRES_URL (CI's `migration-postgres` job, or a local server).
"""

import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SMO_ROOT = Path(__file__).resolve().parent.parent
MIGRATE = SMO_ROOT / "scripts" / "migrate.py"
CHECK = SMO_ROOT / "scripts" / "check_migration_matches_models.py"
ADMIN_URL = os.environ.get("SMO_TEST_POSTGRES_URL")
needs_postgres = pytest.mark.skipif(not ADMIN_URL, reason="SMO_TEST_POSTGRES_URL not set")
HEAD = "0019"          # raise this with every new revision: the tests below then check it is the head


def _scripts() -> ScriptDirectory:
    config = Config(str(SMO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SMO_ROOT / "migrations"))
    return ScriptDirectory.from_config(config)


def test_the_history_is_one_linear_chain_from_the_baseline():
    scripts = _scripts()
    assert len(scripts.get_heads()) == 1, "two revisions share a parent: merge them (alembic merge)"
    revisions = list(scripts.walk_revisions())          # head first
    assert revisions[-1].revision == "0001" and revisions[-1].down_revision is None


def test_the_baseline_revision_runs_the_001_init_sql_file_unchanged():
    spec = importlib.util.spec_from_file_location("baseline", SMO_ROOT / "migrations" / "versions" / "0001_baseline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.BASELINE_SQL == SMO_ROOT / "migrations" / "001_init.sql" and module.BASELINE_SQL.is_file()


def test_the_head_is_the_revision_these_tests_expect():
    assert _scripts().get_current_head() == HEAD, "a new revision: update HEAD here and add what it needs to the tests below"


def test_every_revision_has_a_downgrade_or_says_it_cannot():
    for revision in _scripts().walk_revisions():
        assert callable(getattr(revision.module, "downgrade", None)), revision.revision


@pytest.fixture
def databases():
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    names = {kind: f"mig_{kind}_{uuid.uuid4().hex[:8]}" for kind in ("fresh", "legacy")}
    with admin.connect() as connection:
        for name in names.values():
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    yield {kind: make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False) for kind, name in names.items()}
    with admin.connect() as connection:
        for name in names.values():
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _run(script: Path, url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "SMO_DATABASE_URL": url}
    return subprocess.run([sys.executable, str(script), *args], env=env, capture_output=True, text=True)


def _schema(url: str) -> dict:
    """{(table, column): (type, nullable, default)}, plus the constraint names, from the catalog."""
    engine = create_engine(url)
    with engine.connect() as connection:
        columns = connection.execute(text(
            "SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name <> 'alembic_version'")).all()
        constraints = connection.execute(text(
            "SELECT conrelid::regclass::text, conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace "
            "AND conrelid::regclass::text <> 'alembic_version'")).all()
    engine.dispose()
    return {"columns": sorted(map(tuple, columns)), "constraints": sorted(map(tuple, constraints))}


@needs_postgres
def test_a_fresh_database_and_a_hand_created_one_reach_the_same_schema(databases):
    fresh = _run(MIGRATE, databases["fresh"])
    assert fresh.returncode == 0, fresh.stderr
    assert f"upgraded to {HEAD}" in fresh.stdout and "stamped" not in fresh.stdout

    engine = create_engine(databases["legacy"])        # created the way docker compose's initdb does: the file, no alembic
    with engine.begin() as connection:
        connection.connection.cursor().execute((SMO_ROOT / "migrations" / "001_init.sql").read_text())
    engine.dispose()
    legacy = _run(MIGRATE, databases["legacy"])
    assert legacy.returncode == 0, legacy.stderr
    assert "stamped the existing schema at 0001" in legacy.stdout

    assert _schema(databases["fresh"]) == _schema(databases["legacy"])
    assert _run(MIGRATE, databases["legacy"], "--current").stdout.strip() == HEAD


@needs_postgres
def test_migrating_twice_is_a_no_op_and_never_restamps(databases):
    assert _run(MIGRATE, databases["fresh"]).returncode == 0
    again = _run(MIGRATE, databases["fresh"])
    assert again.returncode == 0 and "stamped" not in again.stdout


@needs_postgres
def test_the_models_check_passes_at_head_and_refuses_a_database_that_is_not(databases):
    assert _run(MIGRATE, databases["fresh"]).returncode == 0
    ok = _run(CHECK, databases["fresh"])
    assert ok.returncode == 0, ok.stdout + ok.stderr

    behind = _run(CHECK, databases["legacy"])          # empty database: no revision at all
    assert behind.returncode != 0 and "run `python scripts/migrate.py` first" in behind.stderr + behind.stdout


@needs_postgres
def test_a_model_change_without_a_revision_fails_the_check(databases):
    """The ORM declares a column the migrated schema lacks (here: dropped after migrating): the check names it."""
    assert _run(MIGRATE, databases["fresh"]).returncode == 0
    engine = create_engine(databases["fresh"])
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE service_profile DROP COLUMN api_supp_feats"))
    engine.dispose()
    result = _run(CHECK, databases["fresh"])
    assert result.returncode == 1 and "service_profile.api_supp_feats" in result.stdout


@needs_postgres
def test_the_revision_after_the_baseline_applies_to_a_baseline_database_and_rolls_back(databases):
    """OPS-1.4: from 0001 to head and back, with the schema after the round trip equal to a database that never left 0001."""
    assert _run(MIGRATE, databases["fresh"], "--revision", "0001").returncode == 0
    at_baseline = _schema(databases["fresh"])
    assert "notification_outbox" not in {table for table, _ in at_baseline["constraints"]}

    up = _run(MIGRATE, databases["fresh"])
    assert up.returncode == 0 and f"upgraded to {HEAD}" in up.stdout, up.stderr
    assert any(table == "notification_outbox" for table, *_ in _schema(databases["fresh"])["columns"])

    at_head = _schema(databases["fresh"])
    previous = _scripts().get_revision(HEAD).down_revision                                   # one step down, whatever the head is
    one = _run(MIGRATE, databases["fresh"], "--downgrade", "-1")
    assert one.returncode == 0 and f"downgraded to {previous}" in one.stdout, one.stderr
    assert _schema(databases["fresh"]) != at_head                                             # the head's change is undone
    down = _run(MIGRATE, databases["fresh"], "--downgrade", "0001")
    assert down.returncode == 0 and "downgraded to 0001" in down.stdout, down.stderr
    assert _schema(databases["fresh"]) == at_baseline

    assert _run(MIGRATE, databases["fresh"]).returncode == 0           # and forward again
    assert _run(MIGRATE, databases["fresh"], "--current").stdout.strip() == HEAD
