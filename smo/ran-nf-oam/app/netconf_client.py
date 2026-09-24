"""NETCONF-shaped CM write dispatch — RAN NF OAM LLD section 5.1's PATCH
step, previously elided behind a comment that recorded every sub_change as
APPLIED without ever dispatching anything. Protocol choice (NETCONF over
RESTCONF) confirmed against OPEN_ITEMS.md's "CM cache sync method" item.

Phase 1: an RFC 6241 <edit-config> request/response shape, sent as XML over
plain HTTP to the O1 Adaptor's adaptor_uri — not a real SSH/ncclient
transport, matching this build's all-HTTP-JSON pragmatism everywhere else
(e.g. R1Client). Scoped to this CM-write path only; the separate
cm_schema_cache fetch path is untouched.
"""

import defusedxml.ElementTree as ET
import httpx
from defusedxml.common import DefusedXmlException

NETCONF_BASE_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"


def build_edit_config_rpc(message_id: str, target_ref: str, attribute_changes: dict) -> str:
    config_body = "".join(f"<{name}>{value}</{name}>" for name, value in attribute_changes.items())
    return (
        f'<rpc message-id="{message_id}" xmlns="{NETCONF_BASE_NS}">'
        f"<edit-config><target><running/></target>"
        f'<config><managed-object ref="{target_ref}">{config_body}</managed-object></config>'
        f"</edit-config></rpc>"
    )


def send_edit_config(adaptor_uri: str, target_ref: str, attribute_changes: dict, message_id: str) -> bool:
    """POSTs the edit-config RPC and reports whether the rpc-reply carried
    <ok/> rather than <rpc-error> (RFC 6241 section 4.2). Any transport
    failure, non-2xx response, or unparseable/unexpected reply counts as
    not applied — the caller records that as a rejected sub_change rather
    than raising, matching how every other per-change failure in
    write_configuration_changes is handled.
    """
    rpc = build_edit_config_rpc(message_id, target_ref, attribute_changes)
    try:
        resp = httpx.post(adaptor_uri, content=rpc, headers={"Content-Type": "application/xml"}, timeout=10.0)
    except httpx.HTTPError:
        return False
    if resp.status_code >= 300:
        return False
    try:
        # defusedxml (not stdlib ET) — the O1 Adaptor's reply is a response
        # from a southbound network endpoint, not a value this process
        # controls; reject entity-expansion / external-entity XML the same
        # way an unparseable reply is already rejected.
        root = ET.fromstring(resp.text)
    except (ET.ParseError, DefusedXmlException):
        return False
    if root.tag.rsplit("}", 1)[-1] != "rpc-reply":
        return False
    return any(child.tag.rsplit("}", 1)[-1] == "ok" for child in root)
