"""AI Runtime SDK — Wave 1 of the AI Platform Service Decomposition
(docs/architecture/AI_PLATFORM_BASELINE.md). A thin rApp-facing client
layer over R1 Termination, wrapping smo_shared.r1_client.R1Client so an
rApp author calls e.g. `sdk.models.register_model(...)` instead of
hand-building an HTTP request against `/mlmr/models` (golden rule 6:
this SDK is a thin client over the same R1 path every other cross-module
call already uses, not a second one).

No existing caller has been migrated to use this yet — every module in
this build still calls R1Client directly, exactly as before. This is
new, additive capability for rApp authors; wiring existing callers over
to it is future work, tracked separately from Wave 1's structural split.
"""

from smo_shared.r1_client import R1Client

from ._common import SdkError
from .analytics import AnalyticsClient
from .data import DataClient
from .intent import IntentClient
from .lifecycle import LifecycleClient
from .models import ModelsClient
from .platform import PlatformClient

__all__ = ["AiRuntimeSdk", "SdkError", "DataClient", "AnalyticsClient", "ModelsClient", "LifecycleClient", "IntentClient", "PlatformClient"]


class AiRuntimeSdk:
    """The six sdk.* namespaces from the layered architecture diagram,
    one client each, sharing one R1Client (and so one cached SME token —
    see r1_client.py's own module docstring on per-process token caching).
    """

    def __init__(self, r1: R1Client | None = None):
        r1 = r1 or R1Client()
        self.data = DataClient(r1)
        self.analytics = AnalyticsClient(r1)
        self.models = ModelsClient(r1)
        self.lifecycle = LifecycleClient(r1)
        self.intent = IntentClient(r1)
        self.platform = PlatformClient(r1)
