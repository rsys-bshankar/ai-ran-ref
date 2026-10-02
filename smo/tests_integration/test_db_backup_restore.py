"""scripts/db_backup.sh and db_restore.sh round trip on a real Postgres (PR-DB-6.1).

Needs SMO_TEST_POSTGRES_URL (CI's `migration-postgres` job, or a local server) and a pg_dump / pg_restore /
psql at least as new as that server; otherwise it skips, saying why.
"""

import os
import re
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SMO_ROOT = Path(__file__).resolve().parent.parent
BACKUP = SMO_ROOT / "scripts" / "db_backup.sh"
RESTORE = SMO_ROOT / "scripts" / "db_restore.sh"
MIGRATION = SMO_ROOT / "migrations" / "001_init.sql"
ADMIN_URL = os.environ.get("SMO_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="SMO_TEST_POSTGRES_URL not set")


def _client_major(tool: str) -> int:
    path = shutil.which(tool)
    if path is None:
        pytest.skip(f"{tool} not installed")
    return int(re.search(r"\d+", subprocess.run([path, "--version"], capture_output=True, text=True).stdout).group(0))


@pytest.fixture(scope="module")
def admin():
    engine = create_engine(ADMIN_URL, future=True, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        server_major = int(connection.execute(text("SHOW server_version_num")).scalar()) // 10000
    for tool in ("pg_dump", "pg_restore", "psql"):
        if _client_major(tool) < server_major:
            pytest.skip(f"{tool} is older than the Postgres {server_major} server; pg_dump refuses that")
    yield engine
    engine.dispose()


def _url_for(database: str) -> str:
    return make_url(ADMIN_URL).set(database=database).render_as_string(hide_password=False)


@pytest.fixture
def databases(admin):
    names = [f"bk_{kind}_{uuid.uuid4().hex[:8]}" for kind in ("src", "dst")]
    with admin.connect() as connection:
        for name in names:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    yield names
    with admin.connect() as connection:
        for name in names:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _env(database: str) -> dict:
    return {**os.environ, "SMO_DATABASE_URL": _url_for(database)}


def _psql_file(database: str, path: Path) -> None:
    url = make_url(_url_for(database))
    env = {**os.environ, "PGPASSWORD": url.password or ""}
    subprocess.run(["psql", "-h", url.host, "-p", str(url.port or 5432), "-U", url.username, "-d", database,
                    "-q", "-v", "ON_ERROR_STOP=1", "-f", str(path)], env=env, check=True, capture_output=True)


def _rows(database: str) -> dict:
    """Every public table's content, as comparable data."""
    engine = create_engine(_url_for(database), future=True)
    try:
        with engine.connect() as connection:
            tables = [row[0] for row in connection.execute(text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY 1"))]
            return {t: sorted(map(tuple, connection.execute(text(f'SELECT * FROM "{t}"')).all()), key=repr)
                    for t in tables}
    finally:
        engine.dispose()


def _seed(database: str) -> None:
    engine = create_engine(_url_for(database), future=True)
    with engine.begin() as connection:
        for module, invoker in (("aimgf", "inv-1"), ("nfo", "inv-2"), ("sme", "inv-3")):
            connection.execute(text("INSERT INTO module_identity (module, invoker_id, invoker_secret) "
                                    "VALUES (:m, :i, 'a secret with ''quotes'' and ünïcode')"), {"m": module, "i": invoker})
        connection.execute(text("INSERT INTO periodic_run (name, last_run_at) VALUES ('collect', now())"))
    engine.dispose()


def _run(script: Path, *args: str, database: str, **kwargs):
    return subprocess.run([str(script), *args], env=_env(database), capture_output=True, text=True, timeout=120, **kwargs)


def test_a_backup_restores_into_an_empty_database_with_the_same_content(databases, tmp_path):
    src, dst = databases
    _psql_file(src, MIGRATION)
    _seed(src)
    dump = tmp_path / "smo.dump"

    made = _run(BACKUP, str(dump), database=src)
    assert made.returncode == 0, made.stderr
    assert dump.stat().st_size > 0 and not (tmp_path / "smo.dump.partial").exists()
    assert stat.S_IMODE(dump.stat().st_mode) == 0o600

    restored = _run(RESTORE, "--yes", str(dump), database=dst)
    assert restored.returncode == 0, restored.stderr
    source, copy = _rows(src), _rows(dst)
    assert source == copy and len(source) > 100 and len(source["module_identity"]) == 3
    assert "ünïcode" in source["module_identity"][0][2]


def test_a_restore_replaces_what_is_there_so_it_can_be_repeated_over_a_live_database(databases, tmp_path):
    src, dst = databases
    _psql_file(src, MIGRATION)
    _seed(src)
    dump = tmp_path / "smo.dump"
    assert _run(BACKUP, str(dump), database=src).returncode == 0
    assert _run(RESTORE, "--yes", str(dump), database=dst).returncode == 0

    engine = create_engine(_url_for(dst), future=True)
    with engine.begin() as connection:                       # drift after the backup
        connection.execute(text("DELETE FROM module_identity WHERE module = 'nfo'"))
        connection.execute(text("INSERT INTO module_identity (module, invoker_id, invoker_secret) VALUES ('x','x','x')"))
    engine.dispose()
    assert _rows(dst) != _rows(src)

    assert _run(RESTORE, "--yes", str(dump), database=dst).returncode == 0
    assert _rows(dst) == _rows(src)


def test_restore_refuses_without_yes_and_changes_nothing(databases, tmp_path):
    src, dst = databases
    _psql_file(src, MIGRATION)
    _seed(src)
    dump = tmp_path / "smo.dump"
    assert _run(BACKUP, str(dump), database=src).returncode == 0
    refused = _run(RESTORE, str(dump), database=dst)
    assert refused.returncode == 2 and "--yes" in refused.stderr
    assert _rows(dst) == {}


def test_a_broken_backup_file_is_refused_in_one_transaction_leaving_the_database_as_it_was(databases, tmp_path):
    src, dst = databases
    _psql_file(src, MIGRATION)
    _seed(src)
    good = tmp_path / "smo.dump"
    assert _run(BACKUP, str(good), database=src).returncode == 0
    assert _run(RESTORE, "--yes", str(good), database=dst).returncode == 0
    before = _rows(dst)

    truncated = tmp_path / "truncated.dump"
    truncated.write_bytes(good.read_bytes()[: good.stat().st_size // 2])
    failed = _run(RESTORE, "--yes", str(truncated), database=dst)
    assert failed.returncode != 0
    assert _rows(dst) == before


def test_a_failed_backup_leaves_no_file_behind(databases, tmp_path):
    src, _ = databases
    dump = tmp_path / "none.dump"
    env = {**os.environ, "SMO_DATABASE_URL": _url_for("no_such_database_for_the_backup_test")}
    failed = subprocess.run([str(BACKUP), str(dump)], env=env, capture_output=True, text=True, timeout=120)
    assert failed.returncode != 0
    assert not dump.exists() and not (tmp_path / "none.dump.partial").exists()


def test_the_password_is_not_on_the_command_line(databases, tmp_path):
    src, _ = databases
    _psql_file(src, MIGRATION)
    password = make_url(ADMIN_URL).password
    if not password:
        pytest.skip("the test database has no password")
    watcher = subprocess.Popen(["bash", "-c", f'for i in $(seq 1 60); do ps -eo args | grep -F "{password}" | '
                                f'grep -v grep | grep -E "pg_dump|pg_restore"; sleep 0.05; done'],
                               stdout=subprocess.PIPE, text=True)
    assert _run(BACKUP, str(tmp_path / "p.dump"), database=src).returncode == 0
    assert watcher.communicate(timeout=30)[0].strip() == ""
