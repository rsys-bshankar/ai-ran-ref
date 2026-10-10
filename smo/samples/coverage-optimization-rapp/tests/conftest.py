"""Test setup for the Coverage Optimization rApp: puts `smo/shared` and `smo/sdk` on sys.path so `smo_shared` and `smo_sdk` import when pytest
runs from the sample's own directory. Run with: cd samples/coverage-optimization-rapp && PYTHONPATH=.:../../shared:../../sdk python -m
pytest tests -q
"""

import os

# The in-memory SQLite fallback of smo_shared.db is an explicit opt-in (SMO_ALLOW_SQLITE_FALLBACK), never inferred from pytest being loaded; set here,
# before any application module is imported, because smo_shared.db builds its engine at import.
os.environ.setdefault("SMO_ALLOW_SQLITE_FALLBACK", "1")

import sys
from pathlib import Path

SMO = Path(__file__).resolve().parents[3]
for p in (SMO / "shared", SMO / "sdk"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
