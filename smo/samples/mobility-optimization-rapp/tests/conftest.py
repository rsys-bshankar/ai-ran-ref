import sys
from pathlib import Path

SMO = Path(__file__).resolve().parents[3]
for p in (SMO / "shared", SMO / "sdk"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
