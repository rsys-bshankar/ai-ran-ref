#!/usr/bin/env python3
"""Writes the BFF's RBAC table to smo/gui/src/auth/permissions.fixture.json —
the same payload GET /api/permissions serves. The SPA's Vitest suite
evaluates its role gating against this snapshot, so the Python regexes are
proven to behave identically in JavaScript; tests/test_rbac.py fails if the
snapshot drifts from the live table. Re-run after changing rbac.py:

    cd smo/gui-bff && PYTHONPATH=. python scripts/export_permissions.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rbac import RULES  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[2] / "gui" / "src" / "auth" / "permissions.fixture.json"


def export() -> list[dict]:
    return [{"method": r.method, "pattern": r.pattern.pattern, "role": str(r.role), "queryMatch": r.query_match} for r in RULES]


if __name__ == "__main__":
    FIXTURE.write_text(json.dumps(export(), indent=2) + "\n")
    print(f"wrote {FIXTURE}")
