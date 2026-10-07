"""Retention of the BFF's audit log (PR-DB-3.6): `python -m app.retention` deletes the rows of `gui_audit_log` older than
`GUI_AUDIT_RETENTION_DAYS` (default 0: keep them all). Run it from cron or a Kubernetes CronJob (the BFF has no worker: it is not on `smo_shared`).

With `GUI_AUDIT_EXPORT_DIR` set, the rows to be deleted are first written there as JSON lines (`gui-audit-<utc date>-<last id>.jsonl`, the
same fields as the table) and only the rows written are deleted, so a failed export deletes nothing. Rows are removed by a bulk statement, not
through the ORM, because the ORM refuses any change to the log (`_audit_log_is_append_only`): this module is the one deliberate exception.
"""

import datetime
import json
import os
import sys
from pathlib import Path

from sqlalchemy import delete, select

from .db import AuditEntry, Database

BATCH = 1000


def _row(entry: AuditEntry) -> dict:
    return {"id": entry.id, "at": entry.at.isoformat(), "username": entry.username, "role": entry.role, "action": entry.action,
            "method": entry.method, "path": entry.path, "status_code": entry.status_code, "detail": entry.detail}


def purge_audit(db: Database, older_than_days: int, export_dir: Path | None = None, now: datetime.datetime | None = None) -> int:
    """Delete the audit rows older than `older_than_days` days (after exporting them when `export_dir` is given); returns how many."""
    if older_than_days <= 0:
        raise ValueError("older_than_days must be positive")
    cutoff = (now or datetime.datetime.now(datetime.UTC)) - datetime.timedelta(days=older_than_days)
    total = 0
    while True:
        with db.session() as session:
            rows = session.execute(select(AuditEntry).where(AuditEntry.at < cutoff).order_by(AuditEntry.id).limit(BATCH)).scalars().all()
            if not rows:
                return total
            if export_dir is not None:
                export_dir.mkdir(parents=True, exist_ok=True)
                target = export_dir / f"gui-audit-{cutoff:%Y%m%d}-{rows[-1].id}.jsonl"
                target.write_text("".join(json.dumps(_row(r), sort_keys=True) + "\n" for r in rows))
            session.execute(delete(AuditEntry).where(AuditEntry.id.in_([r.id for r in rows])))
            session.commit()
            total += len(rows)


def main() -> int:
    from .config import Settings
    try:
        days = int(os.environ.get("GUI_AUDIT_RETENTION_DAYS", "0") or 0)
    except ValueError:
        days = 0
    if days <= 0:
        print("GUI_AUDIT_RETENTION_DAYS is not set: nothing to purge")
        return 0
    export = os.environ.get("GUI_AUDIT_EXPORT_DIR", "").strip()
    deleted = purge_audit(Database(Settings().database_url), days, Path(export) if export else None)
    print(f"deleted {deleted} audit row(s) older than {days} day(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
