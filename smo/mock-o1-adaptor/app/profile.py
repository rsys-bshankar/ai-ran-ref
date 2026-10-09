"""SB-10: a vendor profile for the O1 stub.

A profile is a directory under `app/profiles/<name>/` (so it travels in the image) that says, in data, what one vendor's adaptor is like:

    profile.json            the vendor's name, the MnS services and transports it offers, its conformance mode, and the defaults of its classes
    yang/*.yang             the vendor's YANG (SB-10.1): what it models beyond or differently from the standard
    schema/<name>.json      the CM descriptor generated from that YANG by `scripts/ingest_yang_schema.py` (tests keep it equal to the YANG)
    README.md               the deviations from the standard models (SB-10.3), and what is not known
    conformance-report.md   the O1 conformance kit run against the stub in this profile (SB-10.4)

`MOCK_O1_PROFILE=<name>` makes the stub that vendor: `GET /capabilities` declares it, `get-config` of a class the profile has defaults for returns them, and a
transport the vendor does not speak answers 404 (a NETCONF-only vendor has no RESTCONF root). `MOCK_O1_VENDOR_NAME`, `MOCK_O1_SUPPORTED_SERVICES` and
`MOCK_O1_VENDOR_MODES` still win over the profile when set. Nothing changes without the variable.

`onboarding_body` is the other half: the body of `POST /ran-nf-oam/vendor-onboarding` that registers the same vendor at RAN NF OAM (the capability entry,
SB-10.2, and the descriptor), so the two sides cannot disagree about what the vendor is.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

PROFILES = Path(__file__).parent / "profiles"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MNS_SERVICES = {"PROV", "FM", "PM", "FILE", "STREAM", "SWM", "SUBSCRIPTION", "HEARTBEAT"}
VENDOR_MODES = {"O1_NETCONF", "O1_RESTCONF"}
CONFORMANCE_MODES = {"OWN", "SPEC", "COMBINED"}


class ProfileError(ValueError):
    """The profile does not exist or is not well formed; the message says which and why (it names the profile, never a path outside `PROFILES`)."""


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError(f"{path.name} cannot be read as JSON") from exc


def _check(profile: Any, name: str) -> dict:
    if not isinstance(profile, dict):
        raise ProfileError(f"profile {name}: profile.json is not an object")
    problems = []
    if profile.get("name") != name:
        problems.append("name must equal the directory name")
    if not isinstance(profile.get("vendorName"), str) or not profile["vendorName"]:
        problems.append("vendorName is required")
    services, modes = profile.get("supportedServices"), profile.get("supportedVendorModes")
    if not isinstance(services, list) or not services or not set(services) <= MNS_SERVICES:
        problems.append(f"supportedServices must be a non-empty list of {sorted(MNS_SERVICES)}")
    if not isinstance(modes, list) or not modes or not set(modes) <= VENDOR_MODES:
        problems.append(f"supportedVendorModes must be a non-empty list of {sorted(VENDOR_MODES)}")
    if profile.get("conformanceMode") not in CONFORMANCE_MODES:
        problems.append(f"conformanceMode must be one of {sorted(CONFORMANCE_MODES)}")
    defaults = profile.get("iocDefaults", {})
    if not isinstance(defaults, dict) or not all(isinstance(v, dict) and all(isinstance(x, str) for x in v.values()) for v in defaults.values()):
        problems.append("iocDefaults must map a class to its attributes, each value a string (as the stub keeps them)")
    schema = profile.get("schema")
    if profile.get("conformanceMode") in ("OWN", "COMBINED") and not (isinstance(schema, dict) and all(isinstance(schema.get(k), str) for k in ("schemaName", "revision", "descriptor"))):
        problems.append("conformanceMode OWN or COMBINED needs schema {schemaName, revision, descriptor}")
    if problems:
        raise ProfileError(f"profile {name}: " + "; ".join(problems))
    return profile


@lru_cache(maxsize=16)
def load(name: str) -> dict:
    """The profile `name`, checked. `name` is a directory name under `profiles/`, never a path."""
    if not NAME.match(name):
        raise ProfileError(f"profile name {name!r} is not a directory name (lower-case letters, digits and -)")
    directory = PROFILES / name
    if not (directory / "profile.json").is_file():
        raise ProfileError(f"no profile {name!r} (available: {', '.join(available()) or 'none'})")
    return _check(_read(directory / "profile.json"), name)


def available() -> list[str]:
    return sorted(p.name for p in PROFILES.iterdir() if (p / "profile.json").is_file()) if PROFILES.is_dir() else []


def descriptor(name: str) -> dict:
    """The CM descriptor file of the profile's schema."""
    profile = load(name)
    return _read(PROFILES / name / profile["schema"]["descriptor"])


def onboarding_body(name: str) -> dict:
    """The body of `POST /ran-nf-oam/vendor-onboarding` for this vendor: capability and (for OWN or COMBINED) the vendor's descriptor."""
    profile = load(name)
    body: dict[str, Any] = {"vendorName": profile["vendorName"], "supportedServices": profile["supportedServices"],
                            "supportedVendorModes": profile["supportedVendorModes"], "conformanceMode": profile["conformanceMode"]}
    if profile["conformanceMode"] in ("OWN", "COMBINED"):
        schema = profile["schema"]
        body["schemas"] = [{"schemaName": schema["schemaName"], "revision": schema["revision"], "type": "YANG",
                            "location": f"profile:{name}/{schema['descriptor']}", "descriptor": {"classes": descriptor(name)["classes"]}}]
    return body
