"""NETCONF-shaped CM write dispatch — RAN NF OAM LLD section 5.1's PATCH
step, previously elided behind a comment that recorded every sub_change as
APPLIED without ever dispatching anything. Protocol choice (NETCONF over
RESTCONF) confirmed against HISTORY.md's "CM cache sync method" item.

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

    def __init__(self, applied: bool, reason: str | None = None, detail: str | None = None):
        self.applied, self.reason, self.detail = applied, reason, detail

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
    return parse_reply(resp.text)


def parse_reply(text: str):
    """The `<rpc-reply>` element of a reply body, or None when it is not one."""
    try:
        # defusedxml (not stdlib ET) — the O1 Adaptor's reply is a response
        # from a southbound network endpoint, not a value this process
        # controls; reject entity-expansion / external-entity XML the same
        # way an unparseable reply is already rejected.
        root = ET.fromstring(text)
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
    return edit_outcome(_reply_root(resp))


# RFC 6241 appendix A's error-tag values, in a few words each. An unlisted tag is reported as it came.
RPC_ERROR_TAGS = {
    "in-use": "the data is locked or in use", "invalid-value": "a value is not acceptable", "too-big": "the request or a response is too big",
    "missing-attribute": "an attribute is missing", "bad-attribute": "an attribute value is not correct", "unknown-attribute": "an attribute is unknown",
    "missing-element": "an element is missing", "bad-element": "an element value is not correct", "unknown-element": "an element is unknown",
    "unknown-namespace": "a namespace is unknown", "access-denied": "access denied", "lock-denied": "the lock is held by another session",
    "resource-denied": "the server is out of resources", "rollback-failed": "the rollback failed", "data-exists": "the data already exists",
    "data-missing": "the data does not exist", "operation-not-supported": "the operation is not supported",
    "operation-failed": "the operation failed", "malformed-message": "the message is malformed",
}


def rpc_error_detail(root) -> str | None:
    """One line describing the first `<rpc-error>` of a reply (RFC 6241 section 4.3): `invalid-value (the value is not acceptable) at
    /lab/cell/tx-power: out of range`; None when the reply has no `<rpc-error>`. Bounded in length: it is stored and shown."""
    local = lambda node: node.tag.rsplit("}", 1)[-1]  # noqa: E731
    error = next((c for c in root if local(c) == "rpc-error"), None) if root is not None else None
    if error is None:
        return None
    fields = {local(child): (child.text or "").strip() for child in error.iter() if child is not error}
    tag = fields.get("error-tag", "")
    text = tag + (f" ({RPC_ERROR_TAGS[tag]})" if tag in RPC_ERROR_TAGS else "") if tag else "rpc-error"
    where = fields.get("error-path") or fields.get("bad-element")
    if where:
        text += f" at {where}"
    if fields.get("error-message"):
        text += f": {fields['error-message']}"
    return text[:300]


def edit_outcome(root) -> EditResult:
    """Applied when the `<rpc-reply>` carries `<ok/>`; anything else is a non-retryable NETCONF_RPC_FAILED, with the server's
    `<rpc-error>` (if it sent one) as the detail (PR-SB-1.7)."""
    if root is None or not any(child.tag.rsplit("}", 1)[-1] == "ok" for child in root):
        return EditResult(False, "NETCONF_RPC_FAILED", rpc_error_detail(root))
    return EditResult(True)


def send_get_config(adaptor_uri: str, target_ref: str, message_id: str,
                    managed_function_ref: str | None = None) -> dict | None:
    """Wave 10.1 (W10-20): read-after-write — RFC 6241 <get-config> for one
    managed object; its attributes as a dict, or None if the read failed."""
    resp, _ = _post(adaptor_uri, build_get_config_rpc(message_id, target_ref, managed_function_ref))
    root = resp is not None and _reply_root(resp)
    if root is None or root is False:
        return None
    return config_attributes(root)


def config_attributes(root) -> dict | None:
    """The attributes of the one `<managed-object>` in a `<get-config>` reply's `<data>`, or None without a `<data>`."""
    data = next((c for c in root if c.tag.rsplit("}", 1)[-1] == "data"), None)
    if data is None:
        return None
    obj = next((c for c in data if c.tag.rsplit("}", 1)[-1] == "managed-object"), None)
    return {} if obj is None else {child.tag.rsplit("}", 1)[-1]: child.text for child in obj}
