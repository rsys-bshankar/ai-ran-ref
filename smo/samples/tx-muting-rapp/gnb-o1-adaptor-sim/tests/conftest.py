import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SMO = Path(__file__).resolve().parents[4]
for p in (HERE, SMO / "shared", SMO / "sdk"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
