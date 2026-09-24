"""Mock O1 Adaptor — the isolated NETCONF-shaped test double RAN NF OAM's
own CM write path needs to prove a real HTTP round trip, the same role
mock-near-rt-ric already plays for A1 Related.

RAN NF OAM LLD section 5.1's PATCH step (ran-nf-oam/app/netconf_client.py)
dispatches a real RFC 6241 <edit-config> RPC — as XML over plain HTTP, not
a real SSH/NETCONF transport, matching this build's all-HTTP-JSON
pragmatism everywhere else (e.g. R1Client) — to a ManagedElement's
registered adaptor_uri. Before this module existed, nothing in this
build's own docker-compose topology ever answered that URL for real: the
real O-RAN-SC reference (sim-o1-interface's ntsim-ng) is a full
YANG-model-validated NETCONF/SSH network simulator, out of proportion
with this build's single-Python/FastAPI-stack consolidation (the same
"ADOPT repos stay pattern references only" boundary already documented
elsewhere, OPEN_ITEMS.md section 2). This is the honest, minimal
substitute: just enough real NETCONF-shaped XML parsing to close the loop
RAN NF OAM's own dispatch client was already built to reach, mirroring
mock-near-rt-ric's own "give the real caller something real to call, not
a full protocol implementation" scope.
"""

import xml.etree.ElementTree as ET

from fastapi import FastAPI, Request, Response

app = FastAPI(title="Mock O1 Adaptor (NETCONF test double)")

NETCONF_BASE_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"

_applied_changes: dict[str, dict] = {}  # managed-object ref -> last applied attribute_changes, for real test assertions


@app.post("/edit-config")
async def edit_config(request: Request) -> Response:
    """Mirrors netconf_client.py's own build_edit_config_rpc/send_edit_config
    exactly: parses the real <rpc><edit-config>...</edit-config></rpc>
    request, replies <rpc-reply><ok/></rpc-reply> (RFC 6241 section 4.2) on
    success. REJECTED (a real <rpc-error>, same as a real NETCONF agent
    refusing a request) when the managed-object ref is missing or its
    config body is empty — the same "empty payload is a real, testable
    rejection trigger" pattern mock-near-rt-ric's own create_policy
    already established for an empty policyObject.
    """
    body = await request.body()
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return _reply(message_id="0", ok=False, error_tag="malformed-message")

    message_id = root.attrib.get("message-id", "0")
    managed_object = root.find(f".//{{{NETCONF_BASE_NS}}}managed-object")
    ref = managed_object.attrib.get("ref") if managed_object is not None else None
    # child.tag carries the inherited default namespace (build_edit_config_rpc
    # declares xmlns once, on the <rpc> root — every descendant, including
    # each attribute-change element, inherits it) — strip it back to the
    # plain attribute name, the same rsplit("}", 1)[-1] send_edit_config's
    # own reply-parsing already uses for the same reason.
    attribute_changes = {
        child.tag.rsplit("}", 1)[-1]: child.text for child in managed_object
    } if managed_object is not None else {}

    if not ref or not attribute_changes:
        return _reply(message_id, ok=False, error_tag="invalid-value")

    _applied_changes[ref] = attribute_changes
    return _reply(message_id, ok=True)


@app.get("/edit-config/{managed_object_ref}")
def query_last_applied(managed_object_ref: str):
    """Not part of the real NETCONF RPC surface — a test-only introspection
    route (matching this build's own `_policies`-dict-backed test doubles
    elsewhere) so a real integration test can assert what was actually
    applied, not just that the call returned 200.
    """
    return {"managedObjectRef": managed_object_ref, "attributeChanges": _applied_changes.get(managed_object_ref)}


def _reply(message_id: str, *, ok: bool, error_tag: str | None = None) -> Response:
    if ok:
        body = f'<rpc-reply message-id="{message_id}" xmlns="{NETCONF_BASE_NS}"><ok/></rpc-reply>'
    else:
        body = (
            f'<rpc-reply message-id="{message_id}" xmlns="{NETCONF_BASE_NS}">'
            f"<rpc-error><error-type>application</error-type><error-tag>{error_tag}</error-tag>"
            f"<error-severity>error</error-severity></rpc-error></rpc-reply>"
        )
    return Response(content=body, media_type="application/xml")
