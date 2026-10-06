"""Off-site backup, fetch and restore drill (PR-HA-6): scripts/dr_backup.sh, dr_fetch.sh, dr_drill.sh.

The bucket is a directory behind `tests_integration/fake_aws.py`, a stand-in for the AWS CLI; the real round trip against MinIO is the CI job
`disaster-recovery` (.github/workflows/smo-dr.yml). The database is real: SMO_TEST_POSTGRES_URL, with a pg_dump / pg_restore / psql at least as new
as the server (as test_db_backup_restore.py); otherwise those tests skip, saying why. The structure tests at the end need nothing.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SMO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SMO_ROOT / "scripts"
BACKUP, FETCH, DRILL = SCRIPTS / "dr_backup.sh", SCRIPTS / "dr_fetch.sh", SCRIPTS / "dr_drill.sh"
FAKE_AWS = SMO_ROOT / "tests_integration" / "fake_aws.py"
ADMIN_URL = os.environ.get("SMO_TEST_POSTGRES_URL")
needs_postgres = pytest.mark.skipif(not ADMIN_URL, reason="SMO_TEST_POSTGRES_URL not set")


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
def source(admin):
    name = f"dr_src_{uuid.uuid4().hex[:8]}"
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    migrated = subprocess.run([sys.executable, str(SCRIPTS / "migrate.py")], capture_output=True, text=True, cwd=SMO_ROOT,
                              env={**os.environ, "SMO_DATABASE_URL": _url_for(name), "PYTHONPATH": str(SMO_ROOT / "shared")})
    assert migrated.returncode == 0, migrated.stderr
    yield name
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        for leftover in connection.execute(text("SELECT datname FROM pg_database WHERE datname LIKE 'smo_drill_%'")).all():
            connection.execute(text(f'DROP DATABASE IF EXISTS "{leftover[0]}" WITH (FORCE)'))


@pytest.fixture
def bucket(tmp_path):
    root = tmp_path / "buckets"
    root.mkdir()
    return root


def _env(bucket: Path, database: str, **extra) -> dict:
    return {**os.environ, "SMO_DATABASE_URL": _url_for(database), "FAKE_AWS_ROOT": str(bucket), "SMO_BACKUP_AWS": str(FAKE_AWS),
            "SMO_BACKUP_S3_BUCKET": "smo-dr", "PYTHONPATH": str(SMO_ROOT / "shared"), **extra}


def _run(script: Path, *args: str, env: dict, timeout: int = 240):
    return subprocess.run([str(script), *args], env=env, capture_output=True, text=True, timeout=timeout, cwd=SMO_ROOT)


def _sql(database: str, statement: str):
    engine = create_engine(_url_for(database), future=True)
    with engine.begin() as connection:
        result = connection.execute(text(statement))
        rows = result.all() if result.returns_rows else None
    engine.dispose()
    return rows


def _gui_db(path: Path, users: int = 2) -> Path:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE gui_user (username TEXT PRIMARY KEY)")
    db.executemany("INSERT INTO gui_user VALUES (?)", [(f"user{i}",) for i in range(users)])
    db.commit()
    db.close()
    return path


def _sets(bucket: Path) -> list[str]:
    return sorted(p.name for p in (bucket / "smo-dr" / "smo").iterdir() if p.is_dir())


@needs_postgres
def test_a_backup_set_is_uploaded_with_a_manifest_and_latest_points_at_it(source, bucket, tmp_path):
    gui = _gui_db(tmp_path / "gui-bff.db")
    made = _run(BACKUP, env=_env(bucket, source, SMO_BACKUP_GUI_DB=str(gui)))
    assert made.returncode == 0, made.stderr
    (name,) = _sets(bucket)
    manifest = json.loads((bucket / "smo-dr" / "smo" / name / "manifest.json").read_text())
    assert json.loads((bucket / "smo-dr" / "smo" / "latest.json").read_text()) == manifest
    assert manifest["set"] == name and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", manifest["createdAt"])
    assert manifest["schemaRevision"] == _sql(source, "SELECT version_num FROM alembic_version")[0][0]
    assert manifest["tableCount"] > 100 and manifest["postgresServerVersion"]
    assert [f["name"] for f in manifest["files"]] == ["smo.dump", "gui-bff.db"]
    assert all(len(f["sha256"]) == 64 and f["bytes"] > 0 for f in manifest["files"])


@needs_postgres
def test_a_url_without_a_password_takes_it_from_the_password_file_as_the_compose_service_does(source, bucket, tmp_path):
    password_file = tmp_path / "db_password"
    password_file.write_text(make_url(ADMIN_URL).password or "")
    url = re.sub(r"://([^:@/]+):[^@]*@", r"://\1@", _url_for(source))          # the same URL without its password
    assert make_url(url).password is None
    made = _run(BACKUP, env=_env(bucket, source, SMO_DATABASE_URL=url, SMO_DATABASE_PASSWORD_FILE=str(password_file)))
    assert made.returncode == 0, made.stderr
    missing = _run(BACKUP, "--no-prune", env=_env(bucket, source, SMO_DATABASE_URL=url, SMO_DATABASE_PASSWORD_FILE=str(tmp_path / "nope")))
    assert missing.returncode != 0 and "SMO_DATABASE_PASSWORD_FILE" in missing.stderr


@needs_postgres
def test_no_bucket_means_no_backup(source, bucket):
    env = _env(bucket, source)
    del env["SMO_BACKUP_S3_BUCKET"]
    failed = _run(BACKUP, env=env)
    assert failed.returncode != 0 and "SMO_BACKUP_S3_BUCKET" in failed.stderr


@needs_postgres
def test_a_failed_upload_leaves_latest_on_the_last_complete_set(source, bucket, tmp_path):
    assert _run(BACKUP, env=_env(bucket, source)).returncode == 0
    latest = (bucket / "smo-dr" / "smo" / "latest.json").read_text()
    broken = tmp_path / "aws-that-drops-the-dump"
    broken.write_text(f"#!/bin/sh\ncase \"$*\" in *smo.dump*) exit 0 ;; esac\nexec {FAKE_AWS} \"$@\"\n")
    broken.chmod(0o755)
    time.sleep(1.1)                                              # a set is named by the second
    failed = _run(BACKUP, env=_env(bucket, source, SMO_BACKUP_AWS=str(broken)))
    assert failed.returncode == 1 and "upload check failed" in failed.stderr
    assert (bucket / "smo-dr" / "smo" / "latest.json").read_text() == latest


@needs_postgres
def test_retention_deletes_old_sets_but_keeps_the_newest_few(source, bucket):
    base = bucket / "smo-dr" / "smo"
    for stamp in ("20200101T000000Z", "20200102T000000Z", "20200103T000000Z", "20200104T000000Z"):
        (base / stamp).mkdir(parents=True)
        (base / stamp / "manifest.json").write_text("{}")
    made = _run(BACKUP, env=_env(bucket, source, SMO_BACKUP_RETENTION_DAYS="30", SMO_BACKUP_KEEP_MIN="2"))
    assert made.returncode == 0, made.stderr
    left = _sets(bucket)
    assert len(left) == 2 and left[0] == "20200104T000000Z" and left[1] > "20260101T000000Z"      # the two newest; the new set is never deleted
    kept = _run(BACKUP, "--no-prune", env=_env(bucket, source, SMO_BACKUP_RETENTION_DAYS="0", SMO_BACKUP_KEEP_MIN="0"))
    assert kept.returncode == 0 and len(_sets(bucket)) == 3


@needs_postgres
def test_fetch_verifies_the_manifest_and_refuses_a_damaged_file(source, bucket, tmp_path):
    assert _run(BACKUP, env=_env(bucket, source)).returncode == 0
    out = tmp_path / "fetched"
    ok = _run(FETCH, str(out), env=_env(bucket, source))
    assert ok.returncode == 0, ok.stderr
    assert (out / "smo.dump").stat().st_size > 0 and oct((out / "smo.dump").stat().st_mode & 0o777) == "0o600"
    (dump,) = (bucket / "smo-dr" / "smo").glob("*/smo.dump")
    dump.write_bytes(dump.read_bytes() + b"x")                    # one flipped byte count is enough
    bad = _run(FETCH, str(tmp_path / "again"), env=_env(bucket, source))
    assert bad.returncode == 1 and "smo.dump" in bad.stderr


@needs_postgres
def test_the_drill_restores_into_a_fresh_database_and_measures_the_loss_window(source, bucket, tmp_path):
    gui = _gui_db(tmp_path / "gui-bff.db")
    _sql(source, "INSERT INTO periodic_run (name, last_run_at) SELECT 'probe-' || g, now() FROM generate_series(1, 20) g")
    assert _run(BACKUP, env=_env(bucket, source, SMO_BACKUP_GUI_DB=str(gui))).returncode == 0
    high_water = _sql(source, "INSERT INTO periodic_run VALUES ('probe-late', now()) RETURNING to_char(last_run_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")[0][0]
    report = tmp_path / "report.json"
    args = ["--admin-url", _url_for("postgres"), "--probe", "public.periodic_run:last_run_at", "--high-water", high_water, "--report", str(report)]

    drilled = _run(DRILL, *args, env=_env(bucket, source))
    assert drilled.returncode == 0, drilled.stdout + drilled.stderr
    result = json.loads(report.read_text())
    assert result["passed"] and 0 < result["dataLossSeconds"] < 60 and 0 < result["rtoSeconds"] < 3600
    assert set(result["phases"]) == {"fetchVerify", "restore", "schemaChecks", "smoke"}
    assert "GUI database" in drilled.stdout and "2 user(s)" in drilled.stdout
    assert _sql("postgres", "SELECT count(*) FROM pg_database WHERE datname LIKE 'smo_drill_%'")[0][0] == 0      # the drill cleans up after itself

    too_slow = _run(DRILL, *args, "--rto-seconds", "0", env=_env(bucket, source))
    assert too_slow.returncode == 1 and "FAIL  recovery time" in too_slow.stdout
    time_gap = _run(DRILL, "--admin-url", _url_for("postgres"), "--probe", "public.periodic_run:last_run_at", "--high-water", "2999-01-01T00:00:00Z",
                    env=_env(bucket, source))
    assert time_gap.returncode == 1 and "FAIL  data-loss window" in time_gap.stdout


@needs_postgres
def test_the_drill_fails_on_a_set_that_is_not_the_schema_it_says(source, bucket, tmp_path):
    assert _run(BACKUP, env=_env(bucket, source)).returncode == 0
    manifest = next((bucket / "smo-dr" / "smo").glob("*/manifest.json"))
    data = json.loads(manifest.read_text())
    data["schemaRevision"] = "0001"
    manifest.write_text(json.dumps(data))
    (bucket / "smo-dr" / "smo" / "latest.json").write_text(json.dumps(data))
    failed = _run(DRILL, "--admin-url", _url_for("postgres"), env=_env(bucket, source))
    assert failed.returncode == 1 and "FAIL  schema revision" in failed.stdout


# -- structure: what must exist for the pieces to hang together --------------------------------------------------------------------------------

def test_the_scripts_are_executable_and_the_ci_job_runs_the_drill_with_minio_and_the_targets():
    for script in (BACKUP, FETCH, DRILL, FAKE_AWS):
        assert os.access(script, os.X_OK), script
    workflow = yaml.safe_load((SMO_ROOT.parent / ".github" / "workflows" / "smo-dr.yml").read_text())
    text_ = (SMO_ROOT.parent / ".github" / "workflows" / "smo-dr.yml").read_text()
    assert "dr_backup.sh" in text_ and "dr_drill.sh" in text_ and "minio" in text_.lower()
    assert 'RTO_SECONDS: "3600"' in text_ and 'RPO_SECONDS: "900"' in text_ and '--rto-seconds "$RTO_SECONDS"' in text_ and '--rpo-seconds "$RPO_SECONDS"' in text_
    assert "disaster-recovery" in workflow["jobs"]


def test_the_compose_backup_service_is_a_profile_and_runs_the_loop():
    compose = yaml.safe_load((SMO_ROOT / "docker-compose.yml").read_text())
    service = compose["services"]["db-backup"]
    assert service["profiles"] == ["backup"]
    assert "dr_backup.sh" in " ".join(service["command"]) and "--loop" in " ".join(service["command"])
    env = service["environment"]
    for name in ("SMO_BACKUP_S3_BUCKET", "SMO_BACKUP_S3_ENDPOINT", "SMO_BACKUP_GUI_DB"):
        assert name in env


def test_the_chart_can_schedule_cnpg_backups_only_when_asked():
    values = yaml.safe_load((SMO_ROOT / "deploy" / "helm" / "smo" / "values.yaml").read_text())
    assert values["postgres"]["cnpgBackup"]["enabled"] is False
    template = (SMO_ROOT / "deploy" / "helm" / "smo" / "templates" / "cnpg-backup.yaml").read_text()
    assert "ScheduledBackup" in template and "with .Values.postgres.cnpgBackup" in template and "if .enabled" in template
    cluster = list(yaml.safe_load_all((SMO_ROOT / "deploy" / "helm" / "smo" / "ci" / "cnpg-cluster-backup.yaml").read_text()))
    assert {doc["kind"] for doc in cluster} == {"Secret", "Cluster"}
    barman = next(doc for doc in cluster if doc["kind"] == "Cluster")["spec"]["backup"]["barmanObjectStore"]
    assert barman["wal"]["compression"] and barman["destinationPath"].startswith("s3://")
    assert int(next(doc for doc in cluster if doc["kind"] == "Cluster")["spec"]["postgresql"]["parameters"]["archive_timeout"]) <= 300     # bounds the RPO on a quiet database


def test_the_disaster_recovery_document_states_the_targets_and_is_linked():
    doc = (SMO_ROOT / "docs" / "DISASTER_RECOVERY.md").read_text()
    assert "RPO" in doc and "15 minutes" in doc and "RTO" in doc and "1 hour" in doc
    for linker in ("README.md", "docs/RELEASES.md", "docs/CONTROL_MATRIX.md", "docs/DATA_RESIDENCY.md", "docs/PRIVACY.md"):
        assert "DISASTER_RECOVERY.md" in (SMO_ROOT / linker).read_text(), linker
    assert "DISASTER_RECOVERY.md" in (SMO_ROOT.parent / "SECURITY.md").read_text()
