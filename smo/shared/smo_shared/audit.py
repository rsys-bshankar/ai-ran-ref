"""Tamper-evident audit of changes (PR-SEC-11): who changed what, when, with what result, in a hash chain.

Every row carries `prev_hash` (the hash of the row before it) and `hash` (SHA-256 over the row's own fields and `prev_hash`), and the rows
are numbered 1, 2, 3 ... by the one `audit_head` row, which is locked while a row is added, so replicas of the writer cannot fork the chain. An edited
row breaks its own hash, a deleted or inserted row breaks the numbering and the link after it, and a removed tail no longer matches the head.

    from smo_shared.audit import record, verify

    record(db, actor="api-invoker-1", role="rapp", action="POST", target="/ran-nf-oam/config-jobs", result="201", correlation_id=cid)
    db.commit()                          # the caller commits: the row and the head move together
    verify(db)                           # None when intact, else {"seq": n, "reason": "..."} for the first broken link

Operating it: `python -m smo_shared.audit verify` (exit 1 when broken) and `python -m smo_shared.audit export [--since SEQ] [--format jsonl|syslog]`
read the database named by `SMO_DATABASE_URL`. R1 Termination writes a row for every authenticated change that reaches it (`R1_AUDIT`, default on;
the request body is never recorded).

What it does not do: someone who can write the database *and* recompute every hash after the edit is not detected by the chain alone. Anchor the
head outside the database: `export` ends with the head's `seq` and `hash` (a `head` line), keep that line somewhere the database's writers cannot reach.
Rows are not purged: retention is by archiving an export, not by deleting.
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import sys
import uuid

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Uuid, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base

log = logging.getLogger(__name__)

GENESIS = "0" * 64
_STREAM = {"yield_per": 500}                                  # rows fetched at a time by verify and export: memory, not behaviour
_JSON = {"sort_keys": True, "ensure_ascii": False}           # one row per line: the same bytes for the same row, readable text kept as text
_FORMATS = ("jsonl", "syslog")
_PROG, _DESCRIPTION = "python -m smo_shared.audit", (__doc__ or "").split("\n")[0]
_VERIFY_HELP, _EXPORT_HELP, _SINCE_HELP = ("check the hash chain; exit 1 when it is broken", "write the rows as JSON lines (or syslog) and the head",
                                           "only rows after this seq")
FIELDS = ("seq", "audit_id", "occurred_at", "actor", "actor_role", "action", "target", "result", "correlation_id", "detail", "prev_hash")


class AuditEntry(Base):
    __tablename__ = "audit_log"
    seq: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=False)
    audit_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(String, index=True)               # the invoker id R1 vouches for
    actor_role: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String)                          # the HTTP method
    target: Mapped[str] = mapped_column(String)                          # module prefix and path, no query, no body
    result: Mapped[str] = mapped_column(String)                          # the status code, or REFUSED:<code>
    correlation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class AuditHead(Base):
    __tablename__ = "audit_head"
    head_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)     # always 1
    last_seq: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    last_hash: Mapped[str] = mapped_column(String(64))


def _iso(moment: datetime.datetime) -> str:
    if moment.tzinfo is None:                                            # SQLite hands a timestamp back without its zone
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_hash(entry: AuditEntry) -> str:
    body = {
        "seq": entry.seq, "audit_id": str(entry.audit_id), "occurred_at": _iso(entry.occurred_at), "actor": entry.actor,
        "actor_role": entry.actor_role, "action": entry.action, "target": entry.target, "result": entry.result,
        "correlation_id": entry.correlation_id, "detail": entry.detail, "prev_hash": entry.prev_hash,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _locked_head(db: Session) -> AuditHead:
    head = db.execute(select(AuditHead).where(AuditHead.head_id == 1).with_for_update()).scalar_one_or_none()
    if head is None:                                                     # revision 0021 seeds it; this is for a database made another way
        try:
            with db.begin_nested():
                db.add(AuditHead(head_id=1, last_seq=0, last_hash=GENESIS))
        except IntegrityError:
            pass
        head = db.execute(select(AuditHead).where(AuditHead.head_id == 1).with_for_update()).scalar_one()
    return head


def record(db: Session, *, actor: str, action: str, target: str, result: str, role: str | None = None,
           correlation_id: str | None = None, detail: dict | None = None, now: datetime.datetime | None = None) -> AuditEntry:
    """Adds one row to the chain in `db`'s transaction (the head row is locked until the caller commits)."""
    head = _locked_head(db)
    entry = AuditEntry(
        seq=head.last_seq + 1, audit_id=uuid.uuid4(), occurred_at=now or datetime.datetime.now(datetime.timezone.utc), actor=actor, actor_role=role,
        action=action.upper(), target=target, result=result, correlation_id=correlation_id, detail=detail, prev_hash=head.last_hash)
    entry.hash = compute_hash(entry)
    db.add(entry)
    head.last_seq, head.last_hash = entry.seq, entry.hash
    return entry


def verify(db: Session) -> dict | None:
    """None when the chain is intact; else the first problem: {"seq": the row it was found at, "reason": ...}."""
    expected_seq, previous = 1, GENESIS
    for entry in db.execute(select(AuditEntry).order_by(AuditEntry.seq).execution_options(**_STREAM)).scalars():
        if entry.seq != expected_seq:
            return {"seq": expected_seq, "reason": f"row {expected_seq} is missing (next row is {entry.seq})"}
        if entry.prev_hash != previous:
            return {"seq": entry.seq, "reason": "prev_hash does not match the hash of the row before"}
        if compute_hash(entry) != entry.hash:
            return {"seq": entry.seq, "reason": "the row was changed: its hash does not match its content"}
        expected_seq, previous = entry.seq + 1, entry.hash
    head = db.execute(select(AuditHead).where(AuditHead.head_id == 1)).scalar_one_or_none()
    last_seq = expected_seq - 1
    if head is None:
        return None if last_seq == 0 else {"seq": last_seq, "reason": "the head row is missing"}
    if head.last_seq != last_seq or head.last_hash != previous:
        return {"seq": last_seq, "reason": f"the head says the chain ends at row {head.last_seq}; it ends at {last_seq} (rows removed from the end?)"}
    return None


def audit_enabled(environ=os.environ) -> bool:
    return environ.get("R1_AUDIT", "on").strip().lower() not in ("off", "false", "0", "no")


def write_audit(**fields) -> bool:
    """Records one row in its own session and commits; never raises (a failed write is logged and counted, it does not fail the call it describes,
    which has already been answered). Returns whether the row was written."""
    from .db import SessionLocal
    from .metrics import record_audit_write
    try:
        for attempt in range(3):
            try:
                with SessionLocal() as db:
                    record(db, **fields)
                    db.commit()
                break
            except IntegrityError:           # two writers took the same number (a database that does not lock the head row, as SQLite): number again
                if attempt == 2:
                    raise
        record_audit_write("ok")
        return True
    except Exception:
        log.exception("audit: could not record %s %s", fields.get("action"), fields.get("target"))
        record_audit_write("failed")
        return False


def as_dict(entry: AuditEntry) -> dict:
    return {**{f: (str(getattr(entry, f)) if f == "audit_id" else getattr(entry, f)) for f in FIELDS},
            "occurred_at": _iso(entry.occurred_at), "hash": entry.hash}


def export(db: Session, since: int = 0, fmt: str = _FORMATS[0], out=None, hostname: str = "smo") -> None:
    """The rows after `since`, one per line, then a `head` line (the anchor to keep elsewhere). `syslog` is RFC 5424 with the JSON as the message."""
    if fmt not in _FORMATS:
        raise ValueError(f"unknown audit export format {fmt!r}: expected one of {', '.join(_FORMATS)}")
    out = out or sys.stdout
    for entry in db.execute(select(AuditEntry).where(AuditEntry.seq > since).order_by(AuditEntry.seq).execution_options(**_STREAM)).scalars():
        line = json.dumps(as_dict(entry), **_JSON)
        if fmt == "syslog":              # facility 13 (log audit), severity 6 (informational)
            line = f"<110>1 {_iso(entry.occurred_at)} {hostname} smo-audit - {entry.seq} - {line}"
        out.write(line + "\n")
    head = db.execute(select(AuditHead).where(AuditHead.head_id == 1)).scalar_one_or_none()
    out.write(json.dumps({"head": {"seq": head.last_seq if head else 0, "hash": head.last_hash if head else GENESIS}}) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=_PROG, description=_DESCRIPTION)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help=_VERIFY_HELP)
    exp = sub.add_parser("export", help=_EXPORT_HELP)
    exp.add_argument("--since", type=int, default=0, help=_SINCE_HELP)
    exp.add_argument("--format", choices=_FORMATS, default=_FORMATS[0])
    args = parser.parse_args(argv)
    from .db import SessionLocal
    with SessionLocal() as db:
        if args.command == "verify":
            problem = verify(db)
            print("audit chain intact" if problem is None else f"audit chain BROKEN at row {problem['seq']}: {problem['reason']}")
            return 0 if problem is None else 1
        export(db, since=args.since, fmt=args.format)
        return 0


if __name__ == "__main__":
    sys.exit(main())
