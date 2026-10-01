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

from xml.sax.saxutils import quoteattr

import defusedxml.ElementTree as ET
import httpx
from defusedxml.common import DefusedXmlException

NETCONF_BASE_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
# Wave 10.1 (W10-19): one NETCONF/RESTCONF exchange is bounded at 30 s.
NETCONF_TIMEOUT_SECONDS = 30.0


class EditResult:
    """The outcome of one edit-config exchange. Truthy when applied, so
    existing `if send_edit_config(...)` callers keep working; `reason` says
    why not (Wave 10.1, W10-19): NETCONF_TIMEOUT / NETCONF_UNREACHABLE are
    transient and retryable, NETCONF_RPC_FAILED (the agent answered with
    <rpc-error> or an unusable reply) is not."""

    RETRYABLE = {"NETCONF_TIMEOUT", "NETCONF_UNREACHABLE"}

    def __init__(self, applied: bool, reason: str | None = None):
        self.applied, self.reason = applied, reason

    def __bool__(self) -> bool:
        return self.applied

    @property
    def retryable(self) -> bool:
        return self.reason in self.RETRYABLE


def _managed_object(target_ref: str, managed_function_ref: str | None, extra: str = "") -> str:
    function = f" function-ref={quoteattr(managed_function_ref)}" if managed_function_ref else ""
    return f"<managed-object ref={quoteattr(target_ref)}{function}{extra}"


def build_edit_config_rpc(message_id: str, target_ref: str, attribute_changes: dict, operation: str = "merge",
                          managed_function_ref: str | None = None) -> str:
    """`operation` is RFC 6241 section 7.2's real edit-config attribute
    (merge/replace/create/delete/remove), emitted on the target
    <managed-object> node itself — the node the operation applies to —
    rather than on <edit-config>, matching the RFC's real placement.
    """
    config_body = "".join(f"<{name}>{value}</{name}>" for name, value in attribute_changes.items())
    # Wave 10.1 (W10-17): `function-ref` addresses the managed function
    # (e.g. NRCellDU=101) inside the managed element, so per-cell changes
    # on one element no longer collapse onto the element itself.
    obj = _managed_object(target_ref, managed_function_ref, f' operation="{operation}">')
    return (
        f'<rpc message-id="{message_id}" xmlns="{NETCONF_BASE_NS}">'
        f"<edit-config><target><running/></target>"
        f"<config>{obj}{config_body}</managed-object></config>"
        f"</edit-config></rpc>"
    )


def build_get_config_rpc(message_id: str, target_ref: str, managed_function_ref: str | None = None) -> str:
    obj = _managed_object(target_ref, managed_function_ref, "/>")
    return (f'<rpc message-id="{message_id}" xmlns="{NETCONF_BASE_NS}">'
            f"<get-config><source><running/></source><filter>{obj}</filter></get-config></rpc>")


def _post(adaptor_uri: str, rpc: str) -> tuple[httpx.Response | None, str | None]:
    try:
        resp = httpx.post(adaptor_uri, content=rpc, headers={"Content-Type": "application/xml"},
                          timeout=NETCONF_TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        return None, "NETCONF_TIMEOUT"
    except httpx.HTTPError:
        return None, "NETCONF_UNREACHABLE"
    if resp.status_code in (408, 504):
        return None, "NETCONF_TIMEOUT"
    if resp.status_code >= 500:
        return None, "NETCONF_UNREACHABLE"
    if resp.status_code >= 300:
        return None, "NETCONF_RPC_FAILED"
    return resp, None


def _reply_root(resp: httpx.Response):
    try:
        # defusedxml (not stdlib ET) — the O1 Adaptor's reply is a response
        # from a southbound network endpoint, not a value this process
        # controls; reject entity-expansion / external-entity XML the same
        # way an unparseable reply is already rejected.
        root = ET.fromstring(resp.text)
    except (ET.ParseError, DefusedXmlException):
        return None
    return root if root.tag.rsplit("}", 1)[-1] == "rpc-reply" else None


def send_edit_config(adaptor_uri: str, target_ref: str, attribute_changes: dict, message_id: str, operation: str = "merge",
                     managed_function_ref: str | None = None) -> EditResult:
    """POSTs the edit-config RPC and reports whether the rpc-reply carried
    <ok/> rather than <rpc-error> (RFC 6241 section 4.2). Any transport
    failure, non-2xx response, or unparseable/unexpected reply counts as
    not applied — the caller records that as a rejected sub_change rather
    than raising, matching how every other per-change failure in
    write_configuration_changes is handled.
    """
    rpc = build_edit_config_rpc(message_id, target_ref, attribute_changes, operation, managed_function_ref)
    resp, reason = _post(adaptor_uri, rpc)
    if resp is None:
        return EditResult(False, reason)
    root = _reply_root(resp)
    if root is None or not any(child.tag.rsplit("}", 1)[-1] == "ok" for child in root):
        return EditResult(False, "NETCONF_RPC_FAILED")
    return EditResult(True)


def send_get_config(adaptor_uri: str, target_ref: str, message_id: str,
                    managed_function_ref: str | None = None) -> dict | None:
    """Wave 10.1 (W10-20): read-after-write — RFC 6241 <get-config> for one
    managed object; its attributes as a dict, or None if the read failed."""
    resp, _ = _post(adaptor_uri, build_get_config_rpc(message_id, target_ref, managed_function_ref))
    root = resp is not None and _reply_root(resp)
    if root is None or root is False:
        return None
    data = next((c for c in root if c.tag.rsplit("}", 1)[-1] == "data"), None)
    if data is None:
        return None
    obj = next((c for c in data if c.tag.rsplit("}", 1)[-1] == "managed-object"), None)
    return {} if obj is None else {child.tag.rsplit("}", 1)[-1]: child.text for child in obj}
