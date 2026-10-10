"""smo_shared.audit, exactly: the wording of what verify finds, rows stored out of order, the export's format, and the command line.

Written for the mutants the wider mutation scope (PR-V-2c) found that no test noticed.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_audit_exact.py -q
"""

import datetime
import io
import json

import pytest
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from smo_shared import audit
from smo_shared.db import Base
from smo_shared.testing import make_test_engine

T0 = datetime.datetime(2026, 10, 4, 12, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def engine():
    """An in-memory SQLite engine with every table created."""
    engine = make_test_engine()
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db(engine):
    """A session on the `engine` fixture."""
    with Session(engine) as session:
        yield session


def fill(db, n=3, actor="Zoë"):
    """Helper: writes and commits `n` rows by `actor` one second apart from T0, so tests have a valid chain to damage."""
    for i in range(n):
        audit.record(db, actor=actor, action="post", target=f"/t/{i}", result="201", role="rapp", correlation_id=f"c{i}", now=T0 + datetime.timedelta(seconds=i))
        db.commit()


# ---- verify -----------------------------------------------------------------------------------------------------------------------------

def test_verify_says_exactly_what_it_found(db):
    """A wrong prev_hash is reported with the exact reason wording and the row's number."""
    fill(db, 4)
    db.execute(update(audit.AuditEntry).where(audit.AuditEntry.seq == 3).values(prev_hash="f" * 64))
    db.commit()
    assert audit.verify(db) == {"seq": 3, "reason": "prev_hash does not match the hash of the row before"}


def test_a_gap_names_the_row_that_is_missing_and_the_one_found_instead(db):
    """A deleted row is reported as 'row N is missing (next row is M)'."""
    fill(db, 4)
    db.execute(delete(audit.AuditEntry).where(audit.AuditEntry.seq == 2))
    db.commit()
    assert audit.verify(db) == {"seq": 2, "reason": "row 2 is missing (next row is 3)"}


def test_rows_are_checked_in_sequence_whatever_order_they_are_stored_in(db):
    """verify orders by seq, so rows inserted in reverse order still verify."""
    fill(db, 3)
    rows = [(r.seq, r.audit_id, r.occurred_at, r.actor, r.actor_role, r.action, r.target, r.result, r.correlation_id, r.detail, r.prev_hash, r.hash)
            for r in db.query(audit.AuditEntry).order_by(audit.AuditEntry.seq)]
    db.execute(delete(audit.AuditEntry))
    for row in reversed(rows):              # written 3, 2, 1: the table's own order is not the chain's
        db.add(audit.AuditEntry(seq=row[0], audit_id=row[1], occurred_at=row[2], actor=row[3], actor_role=row[4], action=row[5], target=row[6],
                                result=row[7], correlation_id=row[8], detail=row[9], prev_hash=row[10], hash=row[11]))
    db.commit()
    assert audit.verify(db) is None


def test_a_missing_head_is_found_when_there_are_rows_and_is_nothing_when_there_are_none(db):
    """No head row is fine for an empty chain and a reported break when rows exist."""
    assert audit.verify(db) is None
    fill(db, 2)
    db.execute(delete(audit.AuditHead))
    db.commit()
    assert audit.verify(db) == {"seq": 2, "reason": "the head row is missing"}


def test_a_head_that_disagrees_in_either_the_number_or_the_hash_is_found(db):
    """A head with the wrong seq, or the right seq and a wrong hash, is a break; restoring it makes the chain intact again."""
    fill(db, 3)
    head = db.get(audit.AuditHead, 1)
    good_seq, good_hash = head.last_seq, head.last_hash
    head.last_seq = good_seq + 1
    db.commit()
    assert audit.verify(db)["seq"] == 3 and "ends at row 4; it ends at 3" in audit.verify(db)["reason"]
    head.last_seq, head.last_hash = good_seq, "e" * 64
    db.commit()
    assert audit.verify(db)["seq"] == 3
    head.last_hash = good_hash
    db.commit()
    assert audit.verify(db) is None


# ---- export -----------------------------------------------------------------------------------------------------------------------------

def test_export_writes_sorted_keys_unescaped_text_and_the_head_of_an_empty_chain_too(db):
    """Export lines have sorted keys and keep non-ASCII text as text, and an empty chain still ends with a head line at seq 0."""
    out = io.StringIO()
    audit.export(db, out=out)
    assert out.getvalue() == json.dumps({"head": {"seq": 0, "hash": audit.GENESIS}}) + "\n"
    fill(db, 2)
    out = io.StringIO()
    audit.export(db, out=out)
    first, _, head = out.getvalue().splitlines()
    assert "Zoë" in first and "\\u00eb" not in first
    assert list(json.loads(first)) == sorted(json.loads(first))                       # sorted keys
    assert first == json.dumps(audit.as_dict(db.get(audit.AuditEntry, 1)), sort_keys=True, ensure_ascii=False)
    stored = db.get(audit.AuditHead, 1)
    assert json.loads(head) == {"head": {"seq": 2, "hash": stored.last_hash}}


def test_export_since_skips_rows_up_to_and_including_that_number_and_syslog_names_the_host(db):
    """`since` is exclusive, and the syslog line carries the given hostname and the row's seq."""
    fill(db, 3)
    out = io.StringIO()
    audit.export(db, since=1, fmt="syslog", out=out, hostname="box")
    lines = out.getvalue().splitlines()
    assert len(lines) == 3 and lines[0].startswith("<110>1 2026-10-04T12:00:01.000000Z box smo-audit - 2 - {") and "smo-audit - 3 - {" in lines[1]


def test_as_dict_has_the_text_form_of_the_id_the_iso_time_the_hash_and_the_other_fields(db):
    """as_dict gives the UUID as text, the time in ISO form with Z, the hash and the other fields, and nothing else."""
    fill(db, 1)
    entry = db.get(audit.AuditEntry, 1)
    shown = audit.as_dict(entry)
    assert shown["audit_id"] == str(entry.audit_id) and isinstance(shown["audit_id"], str)
    assert shown["occurred_at"] == "2026-10-04T12:00:00.000000Z" and shown["hash"] == entry.hash
    assert set(shown) == set(audit.FIELDS) | {"hash"}
    assert (shown["seq"], shown["actor"], shown["actor_role"], shown["action"], shown["target"], shown["result"], shown["correlation_id"]) == (
        1, "Zoë", "rapp", "POST", "/t/0", "201", "c0")


# ---- the command line ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def cli(engine, monkeypatch):
    """The audit command line entry point, reading the test engine's database instead of the process one."""
    monkeypatch.setattr("smo_shared.db.SessionLocal", lambda: Session(engine))
    return audit.main


def test_verify_exits_0_when_intact_and_1_with_the_reason_when_not(cli, db, capsys):
    """`verify` prints the verdict and exits 0 when intact, 1 with the reason when broken."""
    fill(db, 3)
    assert cli(["verify"]) == 0 and capsys.readouterr().out == "audit chain intact\n"
    db.execute(update(audit.AuditEntry).where(audit.AuditEntry.seq == 2).values(actor="x"))
    db.commit()
    assert cli(["verify"]) == 1
    assert capsys.readouterr().out == "audit chain BROKEN at row 2: the row was changed: its hash does not match its content\n"


def test_export_from_the_command_line_takes_since_as_a_number_and_a_format(cli, db, capsys):
    """`export` accepts `--since` (a number) and `--format`, and argparse rejects a non-number or an unknown format."""
    fill(db, 3)
    assert cli(["export"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 4                           # all three rows and the head: since defaults to 0
    assert cli(["export", "--since", "2"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 2
    assert cli(["export", "--format", "syslog"]) == 0
    assert capsys.readouterr().out.startswith("<110>1 ")
    with pytest.raises(SystemExit):
        cli(["export", "--since", "two"])
    with pytest.raises(SystemExit):
        cli(["export", "--format", "xml"])


def test_a_command_is_required(cli):
    """Running the command line with no command exits with status 2."""
    with pytest.raises(SystemExit) as caught:
        cli([])
    assert caught.value.code == 2


def test_export_defaults_to_standard_output(db, capsys):
    """export writes to stdout when no stream is given."""
    fill(db, 1)
    audit.export(db)
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 2 and '"head"' in out


def test_verify_and_export_ask_the_database_for_the_rows_in_sequence(engine, db):
    """Both verify and export issue their row query ordered by seq (one SELECT each)."""
    from sqlalchemy import event
    statements = []
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, statement, *a: statements.append(statement))
    fill(db, 2)
    statements.clear()
    audit.verify(db)
    audit.export(db, out=io.StringIO())
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT") and "FROM audit_log" in s]
    assert len(selects) == 2 and all("ORDER BY audit_log.seq" in s for s in selects), selects


def test_the_default_format_is_json_lines_and_both_named_formats_are_accepted(cli, db, capsys):
    """The default export is JSON lines, and `jsonl` and `syslog` are both accepted."""
    fill(db, 1)
    assert cli(["export"]) == 0 and not capsys.readouterr().out.startswith("<110>")
    assert cli(["export", "--format", "jsonl"]) == 0 and not capsys.readouterr().out.startswith("<110>")
    assert cli(["export", "--format", "syslog"]) == 0 and capsys.readouterr().out.startswith("<110>")


def test_the_help_names_the_program_and_each_command(capsys):
    """--help shows the program name and both commands."""
    with pytest.raises(SystemExit):
        audit.main(["--help"])
    text = capsys.readouterr().out
    assert "python -m smo_shared.audit" in text and "verify" in text and "export" in text


def test_export_refuses_a_format_it_does_not_know(db):
    """An unknown export format raises ValueError naming the accepted ones."""
    with pytest.raises(ValueError, match="unknown audit export format 'xml': expected one of jsonl, syslog"):
        audit.export(db, fmt="xml", out=io.StringIO())


def test_the_help_describes_the_program_and_each_command_and_option(capsys):
    """The help text carries the program description and the help string of each command and of --since."""
    def helped(*argv):
        with pytest.raises(SystemExit):
            audit.main(list(argv))
        return " ".join(capsys.readouterr().out.split())

    assert audit._DESCRIPTION in helped("--help")
    top = helped("--help")
    assert audit._VERIFY_HELP in top and audit._EXPORT_HELP in top
    assert audit._SINCE_HELP in helped("export", "--help")
