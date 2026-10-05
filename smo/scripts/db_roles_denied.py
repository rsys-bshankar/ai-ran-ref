#!/usr/bin/env python3
"""The tables a module's database role must be refused, one name per line, for the CI checks (PR-DB-2.6, 2.7).

    scripts/db_roles_denied.py sme

Everything in `migrations/table_owners.json` except the module's own tables, the shared tables the manifest gives it and what it may read, thinned to one table per
other module and the shared ones (about thirty names), so the check asks the question of every kind of table without a hundred round trips.
"""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
owners = {m: t for m, t in json.loads((root / "migrations" / "table_owners.json").read_text()).items() if m != "_comment"}
roles = {m: s for m, s in json.loads((root / "migrations" / "db_roles.json").read_text()).items() if m != "_comment"}
name = sys.argv[1]
module = next(m for m in roles if m.rsplit("/", 1)[-1] == name)
spec = roles[module]
allowed = set(owners.get(module, [])) | set(spec.get("shared", [])) | {q.split(".")[1] for q in spec.get("read", [])}
for owner, tables in owners.items():
    candidates = [t for t in tables if t not in allowed]
    if candidates and owner != module:
        print(candidates[0])
