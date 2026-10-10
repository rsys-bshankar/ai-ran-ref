"""Test setup for the Mobility Optimization rApp: puts `smo/shared` and `smo/sdk` on sys.path so `smo_shared` and `smo_sdk` import when pytest
runs from the sample's own directory. Run with: cd samples/mobility-optimization-rapp && PYTHONPATH=.:../../shared:../../sdk python -m
pytest tests -q
"""

import sys
from pathlib import Path

SMO = Path(__file__).resolve().parents[3]
for p in (SMO / "shared", SMO / "sdk"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
