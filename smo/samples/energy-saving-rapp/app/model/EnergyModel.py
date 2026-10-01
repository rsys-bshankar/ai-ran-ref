"""EnergySavingPredictor — the EnergySaving rApp's model (Wave 10.1, W10-02,
decision D-6). It is a threshold rule on top of a linear regression that
predicts next-hour PRB utilisation:

    futurePrb = prbNow + w0 + w1 * trend + w2 * seasonal
        trend    = prbNow - prbOneHourAgo
        seasonal = profile[hour + 1] - profile[hour]

The regression is anchored on persistence: a steady load predicts itself
rather than regressing toward the daily mean. It learns the expected
next-hour change from three terms:
  * a drift (w0);
  * how much of the last hour's trend carries on (w1);
  * how much of the cell population's usual hour-to-hour change applies
    (w2). The `profile` is the mean PRB per hour of day, learned from the
    same history, so the model knows the morning ramp is coming.

TrainingLogic.py fits everything by ordinary least squares.

The recommendation applies the Wave 10 thresholds:
    LOCKED    both current and predicted PRB below the sleep threshold (5 %)
    UNLOCKED  predicted PRB above the wake threshold (15 %)
    NO_CHANGE otherwise — the 5–15 % hysteresis zone

The serialized artifact is plain JSON (EnergyModel.to_artifact), stored in
MLMR inside a .zip as `energy_model.json`. The model is pure Python on
purpose: a three-weight regression needs no numpy or ONNX runtime.
"""

import io
import json
import math
import zipfile
from dataclasses import asdict, dataclass, field

MODEL_TYPE = "EnergySavingPredictor"
ARTIFACT_MEMBER = "energy_model.json"
SLEEP_THRESHOLD = 5.0
WAKE_THRESHOLD = 15.0
HORIZON_MINUTES = 60


@dataclass
class EnergyModel:
    weights: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])  # [drift, trend gain, seasonal gain]; untrained = persistence
    profile: list[float] = field(default_factory=list)                     # mean PRB per hour of day (24); empty = no seasonality
    rmse: float = 0.0
    sleep_threshold: float = SLEEP_THRESHOLD
    wake_threshold: float = WAKE_THRESHOLD
    horizon_minutes: int = HORIZON_MINUTES
    trained_samples: int = 0
    model_type: str = MODEL_TYPE
    version: str = "1.0.0"

    def seasonal(self, hour: int | None) -> float:
        if hour is None or len(self.profile) != 24:
            return 0.0
        return self.profile[(hour + 1) % 24] - self.profile[hour % 24]

    def predict(self, prb_now: float, prb_hour_ago: float | None = None, hour: int | None = None) -> float:
        trend = prb_now - (prb_hour_ago if prb_hour_ago is not None else prb_now)
        w0, w1, w2 = self.weights
        return max(0.0, min(100.0, prb_now + w0 + w1 * trend + w2 * self.seasonal(hour)))

    def recommend(self, prb_now: float, future_prb: float) -> str:
        if future_prb > self.wake_threshold:
            return "UNLOCKED"
        if prb_now < self.sleep_threshold and future_prb < self.sleep_threshold:
            return "LOCKED"
        return "NO_CHANGE"

    def confidence(self) -> float:
        """How far to trust a prediction. It is 1 for a perfect fit and
        falls toward 0 as the training RMSE approaches the 10-point
        hysteresis band."""
        band = self.wake_threshold - self.sleep_threshold
        return round(max(0.0, 1.0 - self.rmse / band), 3)

    def infer(self, prb_now: float, prb_hour_ago: float | None = None, hour: int | None = None) -> dict:
        future = self.predict(prb_now, prb_hour_ago, hour)
        return {"futurePrb": round(future, 2), "recommendedState": self.recommend(prb_now, future),
                "confidence": self.confidence()}

    # ------------------------------------------------------------ artifact
    def to_artifact(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(ARTIFACT_MEMBER, json.dumps(asdict(self), indent=1, sort_keys=True))
        return buf.getvalue()

    @classmethod
    def from_artifact(cls, data: bytes) -> "EnergyModel":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return cls.from_dict(json.loads(z.read(ARTIFACT_MEMBER)))

    @classmethod
    def from_dict(cls, d: dict) -> "EnergyModel":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return asdict(self)


def solve_least_squares(rows: list[list[float]], targets: list[float]) -> list[float]:
    """Solves the normal equations (XᵀX)w = Xᵀy by Gaussian elimination
    with partial pivoting. A tiny ridge term means a degenerate series (a
    constant one, for example) still yields weights."""
    n = len(rows[0])
    a = [[sum(r[i] * r[j] for r in rows) + (1e-6 if i == j else 0.0) for j in range(n)] for i in range(n)]
    b = [sum(r[i] * t for r, t in zip(rows, targets)) for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot], b[col], b[pivot] = a[pivot], a[col], b[pivot], b[col]
        for r in range(col + 1, n):
            f = a[r][col] / a[col][col]
            a[r] = [x - f * y for x, y in zip(a[r], a[col])]
            b[r] -= f * b[col]
    w = [0.0] * n
    for r in range(n - 1, -1, -1):
        w[r] = (b[r] - sum(a[r][c] * w[c] for c in range(r + 1, n))) / a[r][r]
    return w


def rmse(errors: list[float]) -> float:
    return math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else 0.0
