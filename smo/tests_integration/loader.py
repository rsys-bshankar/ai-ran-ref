"""Loads multiple modules' FastAPI apps into ONE Python process for
cross-service integration testing.

Every one of the sixteen modules (plus the mock Near-RT RIC) uses the
identical top-level package name `app` — correct for how they actually
run (one module per Docker container, per the Dockerfile's MODULE build
arg, so no collision ever occurs in production). It's ONLY a problem for
a test process that wants several of them loaded at once, and without
`__init__.py` files each module's app/ directory is an implicit
namespace package, which would silently merge across sys.path entries if
more than one were on sys.path simultaneously — worse than an
ImportError, since it could load the WRONG module's code under the
right name. This loader avoids that entirely: only ever one module's
directory is on sys.path at a time, for the exact duration of that one
import.
"""

import importlib
import sys
from pathlib import Path
from types import ModuleType

SMO_ROOT = Path(__file__).resolve().parent.parent


def load_app_module(module_dir: str) -> ModuleType:
    """Returns the freshly-loaded `app.main` module for `module_dir`
    (e.g. "sme", "aimgf"). The returned object's `.app` attribute
    is that module's FastAPI instance — hold onto the RETURNED object;
    sys.modules['app.main'] itself gets overwritten by the next call.
    """
    full_dir = str(SMO_ROOT / module_dir)

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    sys.path.insert(0, full_dir)
    try:
        main = importlib.import_module("app.main")
    finally:
        sys.path.remove(full_dir)
    return main
