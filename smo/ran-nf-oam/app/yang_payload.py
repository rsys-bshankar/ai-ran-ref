"""Payloads for a NETCONF server that has a real YANG model (PR-SB-1.5).

The build's own adaptor (and its mock) understand `<managed-object ref=...>`; a real server understands only its models' data nodes.
An ssh adaptor URI may name one of the models below with `?model=<name>`: `ssh://netconf@host:830?model=smo-lab`. Without it the
`<managed-object>` shape is sent, as before. A profile says where a managed object lives in the model: the container, the list, the key
leaf, and which RDN of the managed function (or element) reference is the key. Attribute names are camelCase on the SMO side and
kebab-case in YANG (`administrativeState` is `administrative-state`).

Only the models in `PROFILES` are known; a model that is not is refused when the URI is parsed, so a typo never falls back to a shape
the server will not understand.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

from .ldn import leaf_id

NETCONF_BASE_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"


@dataclass(frozen=True)
class Profile:
    namespace: str
    container: str
    list_name: str
    key_leaf: str


PROFILES = {"smo-lab": Profile("urn:smo:lab", "lab", "cell", "id")}


def model_of(adaptor_uri: str) -> str | None:
    """The `model` named in the URI's query, or None. ValueError for an unknown model or more than one."""
    query = parse_qs(urlsplit(adaptor_uri).query, keep_blank_values=True)
    unknown = set(query) - {"model"}
    if unknown:
        raise ValueError(f"unknown adaptor URI option {sorted(unknown)[0]!r} (only model=)")
    values = query.get("model", [])
    if not values:
        return None
    if len(values) > 1 or values[0] not in PROFILES:
        raise ValueError(f"unknown model {values[-1]!r}; known: {', '.join(sorted(PROFILES))}")
    return values[0]


def to_yang_name(attribute: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", lambda m: "-" + m.group(1).lower(), attribute)


def to_attribute_name(leaf: str) -> str:
    head, *rest = leaf.split("-")
    return head + "".join(part.capitalize() for part in rest)


def _key(target_ref: str, managed_function_ref: str | None) -> str:
    return leaf_id(managed_function_ref or target_ref) or (managed_function_ref or target_ref)


def build_get_config_rpc(profile: Profile, message_id: str, target_ref: str, managed_function_ref: str | None = None) -> str:
    key = escape(_key(target_ref, managed_function_ref))
    return (f'<rpc message-id="{escape(message_id)}" xmlns="{NETCONF_BASE_NS}"><get-config><source><running/></source>'
            f'<filter type="subtree"><{profile.container} xmlns="{profile.namespace}"><{profile.list_name}>'
            f"<{profile.key_leaf}>{key}</{profile.key_leaf}></{profile.list_name}></{profile.container}></filter></get-config></rpc>")


def config_attributes(profile: Profile, root, target_ref: str, managed_function_ref: str | None = None) -> dict | None:
    """The leaves of the list entry the reference names, as SMO attribute names; None when the reply has no `<data>`, {} when
    the entry does not exist."""
    local = lambda node: node.tag.rsplit("}", 1)[-1]  # noqa: E731
    data = next((c for c in root if local(c) == "data"), None)
    if data is None:
        return None
    key = _key(target_ref, managed_function_ref)
    for entry in data.iter():
        if local(entry) != profile.list_name:
            continue
        leaves = {local(child): (child.text or "").strip() for child in entry}
        if leaves.get(profile.key_leaf) == key:
            return {to_attribute_name(name): value for name, value in leaves.items() if name != profile.key_leaf}
    return {}
