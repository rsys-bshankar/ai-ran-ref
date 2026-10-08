"""The checks. Each one states, in its title, a thing RAN NF OAM depends on; `Fail("...")` says what the adaptor did instead."""

from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

import defusedxml.ElementTree as ET
from defusedxml.common import DefusedXmlException

from .kit import Context, Fail, check

NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
KNOWN_SERVICES = {"PROV", "FM", "PM", "FILE", "STREAM", "SWM", "SUBSCRIPTION", "HEARTBEAT"}
KNOWN_MODES = {"O1_NETCONF", "O1_RESTCONF"}
YANG_JSON = "application/yang-data+json"


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ------------------------------------------------------------------------------------------------------------ discovery

def _capabilities(ctx: Context) -> dict:
    resp = ctx.client.get(ctx.capabilities_path)
    if resp.status_code != 200:
        raise Fail(f"GET {ctx.capabilities_path} answered {resp.status_code}, not 200")
    try:
        body = resp.json()
    except ValueError:
        raise Fail("the capability declaration is not JSON") from None
    if not isinstance(body, dict):
        raise Fail("the capability declaration is not a JSON object")
    return body


@check("DISC-1", "DISC", "GET /capabilities answers a JSON object with vendorName, supportedServices and supportedVendorModes")
def disc_shape(ctx: Context) -> None:
    body = _capabilities(ctx)
    missing = [k for k in ("vendorName", "supportedServices", "supportedVendorModes") if k not in body]
    if missing:
        raise Fail(f"missing {', '.join(missing)}")
    if not isinstance(body["vendorName"], str) or not body["vendorName"]:
        raise Fail("vendorName is not a non-empty string")
    for key in ("supportedServices", "supportedVendorModes"):
        if not isinstance(body[key], list) or not body[key] or not all(isinstance(v, str) for v in body[key]):
            raise Fail(f"{key} is not a non-empty list of strings")
    ctx.declared.update(body)


@check("DISC-2", "DISC", "every declared service and transport is one RAN NF OAM knows")
def disc_vocabulary(ctx: Context) -> None:
    if not ctx.declared:
        disc_shape(ctx)
    unknown_services = sorted(set(ctx.declared["supportedServices"]) - KNOWN_SERVICES)
    unknown_modes = sorted(set(ctx.declared["supportedVendorModes"]) - KNOWN_MODES)
    if unknown_services or unknown_modes:
        raise Fail(f"unknown services {unknown_services}, unknown transports {unknown_modes} (known: {sorted(KNOWN_SERVICES)}, {sorted(KNOWN_MODES)})")


@check("DISC-3", "DISC", "a transport that is declared is one this run exercises (and the declaration covers the ones it does)")
def disc_modes_match_run(ctx: Context) -> None:
    if not ctx.declared:
        disc_shape(ctx)
    declared = {m.removeprefix("O1_").lower() for m in ctx.declared["supportedVendorModes"]}
    ran = ctx.protocols
    undeclared = sorted(ran - declared)
    if undeclared:
        raise Fail(f"the run exercises {undeclared} but the adaptor does not declare it in supportedVendorModes")


# ------------------------------------------------------------------------------------------------------------ NETCONF-shaped

def _rpc(message_id: str, inner: str) -> str:
    return f'<rpc message-id="{message_id}" xmlns="{NS}">{inner}</rpc>'


def _edit(message_id: str, ref: str, attrs: dict, operation: str = "merge", function_ref: str | None = None) -> str:
    function = f" function-ref={quoteattr(function_ref)}" if function_ref else ""
    body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in attrs.items())
    return _rpc(message_id, f"<edit-config><target><running/></target><config><managed-object ref={quoteattr(ref)}{function} operation=\"{operation}\">{body}"
                            f"</managed-object></config></edit-config>")


def _get(message_id: str, ref: str, function_ref: str | None = None) -> str:
    function = f" function-ref={quoteattr(function_ref)}" if function_ref else ""
    return _rpc(message_id, f"<get-config><source><running/></source><filter><managed-object ref={quoteattr(ref)}{function}/></filter></get-config>")


def _post(ctx: Context, xml: str):
    return ctx.client.post(ctx.netconf_path, content=xml.encode(), headers={"Content-Type": "application/xml"})


def _parse_reply(resp):
    """(root, error_tag or None). Fails the check when the answer is not an rpc-reply at all."""
    if resp.status_code >= 500:
        raise Fail(f"HTTP {resp.status_code} where an rpc-reply was expected")
    try:
        root = ET.fromstring(resp.content)
    except (ET.ParseError, DefusedXmlException):
        raise Fail(f"HTTP {resp.status_code}, and the body is not XML") from None
    if local(root.tag) != "rpc-reply":
        raise Fail(f"the answer's root is <{local(root.tag)}>, not <rpc-reply>")
    error = next((c for c in root if local(c.tag) == "rpc-error"), None)
    if error is None:
        return root, None
    tag = next((c.text for c in error if local(c.tag) == "error-tag"), None)
    return root, tag or "rpc-error"


def _ok(ctx: Context, xml: str, what: str):
    root, error = _parse_reply(_post(ctx, xml))
    if error is not None:
        raise Fail(f"{what} was refused with {error}")
    if not any(local(c.tag) == "ok" for c in root):
        raise Fail(f"{what} was answered with neither <ok/> nor <rpc-error>")
    return root


def _read(ctx: Context, ref: str, function_ref: str | None = None) -> dict:
    root, error = _parse_reply(_post(ctx, _get("read", ref, function_ref)))
    if error is not None:
        raise Fail(f"get-config was refused with {error}")
    data = next((c for c in root if local(c.tag) == "data"), None)
    if data is None:
        raise Fail("the get-config reply has no <data>")
    obj = next((c for c in data if local(c.tag) == "managed-object"), None)
    if obj is None:
        return {}
    return {local(c.tag): (c.text or "") for c in obj}


@check("NC-1", "NETCONF", "an edit-config (merge) of a managed object is acknowledged with <ok/>")
def nc_merge_ok(ctx: Context) -> None:
    _ok(ctx, _edit("1", ctx.new_ref(), {"administrativeState": "LOCKED"}), "the merge")


@check("NC-2", "NETCONF", "what an edit-config wrote is what get-config reads back (a write that is acknowledged and not applied is the commonest lie)")
def nc_read_after_write(ctx: Context) -> None:
    ref = ctx.new_ref()
    _ok(ctx, _edit("1", ref, {"administrativeState": "LOCKED", "userLabel": "conformance"}), "the write")
    got = _read(ctx, ref)
    if got.get("administrativeState") != "LOCKED" or got.get("userLabel") != "conformance":
        raise Fail(f"wrote administrativeState=LOCKED and userLabel=conformance, read back {got}")


@check("NC-3", "NETCONF", "a second merge keeps the attributes the first one wrote")
def nc_merge_semantics(ctx: Context) -> None:
    ref = ctx.new_ref()
    _ok(ctx, _edit("1", ref, {"userLabel": "one"}), "the first merge")
    _ok(ctx, _edit("2", ref, {"administrativeState": "LOCKED"}), "the second merge")
    got = _read(ctx, ref)
    if got.get("userLabel") != "one" or got.get("administrativeState") != "LOCKED":
        raise Fail(f"after two merges the object reads {got}")


@check("NC-4", "NETCONF", "a managed function is addressed apart from its element (function-ref)")
def nc_function_ref(ctx: Context) -> None:
    ref = ctx.new_ref()
    _ok(ctx, _edit("1", ref, {"administrativeState": "LOCKED"}, function_ref="NRCellDU=101"), "the write to the function")
    function = _read(ctx, ref, "NRCellDU=101")
    element = _read(ctx, ref)
    if function.get("administrativeState") != "LOCKED":
        raise Fail(f"the function reads {function}")
    if element.get("administrativeState") == "LOCKED":
        raise Fail("a write to NRCellDU=101 changed the element itself")


@check("NC-5", "NETCONF", "a delete removes what was written")
def nc_delete(ctx: Context) -> None:
    ref = ctx.new_ref()
    _ok(ctx, _edit("1", ref, {"userLabel": "gone-soon"}), "the write")
    _ok(ctx, _edit("2", ref, {}, operation="delete"), "the delete")
    if _read(ctx, ref).get("userLabel") == "gone-soon":
        raise Fail("after a delete the object still reads what it was given")


@check("NC-6", "NETCONF", "a merge that carries no attribute is refused with an <rpc-error>, not acknowledged")
def nc_empty_merge(ctx: Context) -> None:
    _, error = _parse_reply(_post(ctx, _edit("1", ctx.new_ref(), {})))
    if error is None:
        raise Fail("an empty merge was acknowledged with <ok/>")


@check("NC-7", "NETCONF", "a request with no managed-object ref is refused with an <rpc-error>")
def nc_missing_ref(ctx: Context) -> None:
    xml = _rpc("1", "<edit-config><target><running/></target><config><managed-object operation=\"merge\"><userLabel>x</userLabel></managed-object></config></edit-config>")
    _, error = _parse_reply(_post(ctx, xml))
    if error is None:
        raise Fail("an edit-config without a ref was acknowledged")


@check("NC-8", "NETCONF", "a body that is not XML is answered, not crashed on (no 5xx)")
def nc_malformed(ctx: Context) -> None:
    resp = _post(ctx, "<rpc><edit-config>")
    if resp.status_code >= 500:
        raise Fail(f"HTTP {resp.status_code} for unparseable XML")
    if resp.status_code == 200:
        _, error = _parse_reply(resp)
        if error is None:
            raise Fail("unparseable XML was acknowledged with <ok/>")


@check("NC-9", "NETCONF", "the reply carries the request's message-id")
def nc_message_id(ctx: Context) -> None:
    root, _ = _parse_reply(_post(ctx, _edit("conf-msg-42", ctx.new_ref(), {"userLabel": "id"})))
    if root.attrib.get("message-id") != "conf-msg-42":
        raise Fail(f"message-id conf-msg-42 came back as {root.attrib.get('message-id')!r}")


@check("NC-10", "NETCONF", "values with XML special characters round-trip, escaped")
def nc_escaping(ctx: Context) -> None:
    ref = ctx.new_ref()
    value = "a<b & c>d"
    _ok(ctx, _edit("1", ref, {"userLabel": value}), "the write")
    got = _read(ctx, ref).get("userLabel")
    if got != value:
        raise Fail(f"wrote {value!r}, read back {got!r}")


@check("NC-11", "NETCONF", "an XML entity in a request is not expanded (no entity expansion, no 5xx)")
def nc_no_entity_expansion(ctx: Context) -> None:
    bomb = ('<?xml version="1.0"?><!DOCTYPE rpc [<!ENTITY x "EXPANDED-BY-THE-ADAPTOR">]>'
            f'<rpc message-id="1" xmlns="{NS}"><edit-config><target><running/></target><config>'
            '<managed-object ref="entity-probe" operation="merge"><userLabel>&x;</userLabel></managed-object></config></edit-config></rpc>')
    resp = _post(ctx, bomb)
    if resp.status_code >= 500:
        raise Fail(f"HTTP {resp.status_code} for a request with an entity")
    if "EXPANDED-BY-THE-ADAPTOR" in resp.text:
        raise Fail("the entity was expanded and echoed")
    if _read(ctx, "entity-probe").get("userLabel") == "EXPANDED-BY-THE-ADAPTOR":
        raise Fail("the entity was expanded and stored")


# ------------------------------------------------------------------------------------------------------------ RESTCONF-shaped

def _res(ctx: Context, ref: str, function_ref: str | None = None) -> str:
    path = f"{ctx.restconf_root}/data/managed-element={quote(ref, safe='')}"
    if function_ref:
        path += f"/managed-function={quote(function_ref, safe='')}"
    return path


def _body(ref: str, attrs: dict, function_ref: str | None = None) -> dict:
    if function_ref:
        return {"managed-function": [{"function-ref": function_ref, **attrs}]}
    return {"managed-element": [{"ref": ref, **attrs}]}


def _put(ctx: Context, ref: str, attrs: dict, function_ref: str | None = None):
    return ctx.client.put(_res(ctx, ref, function_ref), json=_body(ref, attrs, function_ref), headers={"Content-Type": YANG_JSON})


def _patch(ctx: Context, ref: str, attrs: dict, function_ref: str | None = None, body: dict | None = None):
    return ctx.client.patch(_res(ctx, ref, function_ref), json=body if body is not None else _body(ref, attrs, function_ref), headers={"Content-Type": YANG_JSON})


def _rc_read(ctx: Context, ref: str, function_ref: str | None = None) -> dict:
    resp = ctx.client.get(_res(ctx, ref, function_ref), headers={"Accept": YANG_JSON})
    if resp.status_code != 200:
        raise Fail(f"GET answered {resp.status_code}, not 200")
    try:
        body = resp.json()
        entry = body["managed-function" if function_ref else "managed-element"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        raise Fail("the GET body is not a one-entry managed-element / managed-function list") from None
    return {k: str(v) for k, v in entry.items()}


def _expect(resp, *statuses: int, what: str) -> None:
    if resp.status_code not in statuses:
        raise Fail(f"{what} answered {resp.status_code}, expected {' or '.join(map(str, statuses))}")


@check("RC-1", "RESTCONF", "/.well-known/host-meta names the RESTCONF root")
def rc_host_meta(ctx: Context) -> None:
    resp = ctx.client.get("/.well-known/host-meta")
    _expect(resp, 200, what="GET /.well-known/host-meta")
    if 'rel="restconf"' not in resp.text and "rel='restconf'" not in resp.text:
        raise Fail("host-meta has no Link with rel=restconf")


@check("RC-2", "RESTCONF", "a PUT of a new object answers 201 and a PUT of an existing one answers 204")
def rc_put_statuses(ctx: Context) -> None:
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "one"}), 201, what="the first PUT")
    _expect(_put(ctx, ref, {"userLabel": "two"}), 204, 200, what="the second PUT")


@check("RC-3", "RESTCONF", "a GET reads back what a PUT wrote, as a list entry keyed by the object")
def rc_read_after_write(ctx: Context) -> None:
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"administrativeState": "LOCKED"}), 201, 204, 200, what="the PUT")
    got = _rc_read(ctx, ref)
    if got.get("ref") != ref or got.get("administrativeState") != "LOCKED":
        raise Fail(f"wrote ref={ref} administrativeState=LOCKED, read back {got}")


@check("RC-4", "RESTCONF", "a PATCH merges: it changes what it names and keeps the rest")
def rc_patch_merges(ctx: Context) -> None:
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "kept"}), 201, 204, 200, what="the PUT")
    _expect(_patch(ctx, ref, {"administrativeState": "LOCKED"}), 204, 200, what="the PATCH")
    got = _rc_read(ctx, ref)
    if got.get("userLabel") != "kept" or got.get("administrativeState") != "LOCKED":
        raise Fail(f"after the PATCH the object reads {got}")


@check("RC-5", "RESTCONF", "a PUT replaces: attributes it does not name are gone")
def rc_put_replaces(ctx: Context) -> None:
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "first", "administrativeState": "LOCKED"}), 201, 204, 200, what="the first PUT")
    _expect(_put(ctx, ref, {"userLabel": "second"}), 204, 200, what="the second PUT")
    got = _rc_read(ctx, ref)
    if got.get("userLabel") != "second" or "administrativeState" in got and got["administrativeState"] == "LOCKED":
        raise Fail(f"after a replace the object reads {got}")


@check("RC-6", "RESTCONF", "a managed function is a resource below its element")
def rc_function_resource(ctx: Context) -> None:
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "element"}), 201, 204, 200, what="the PUT of the element")
    _expect(_put(ctx, ref, {"administrativeState": "LOCKED"}, "NRCellDU=101"), 201, 204, 200, what="the PUT of the function")
    got = _rc_read(ctx, ref, "NRCellDU=101")
    if got.get("function-ref") != "NRCellDU=101" or got.get("administrativeState") != "LOCKED":
        raise Fail(f"the function reads {got}")


@check("RC-7", "RESTCONF", "a DELETE answers 204 and a second DELETE answers 404")
def rc_delete(ctx: Context) -> None:
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "x"}), 201, 204, 200, what="the PUT")
    _expect(ctx.client.delete(_res(ctx, ref)), 204, what="the DELETE")
    _expect(ctx.client.delete(_res(ctx, ref)), 404, what="the second DELETE")


@check("RC-8", "RESTCONF", "a body whose key does not match the resource is refused with 400")
def rc_key_mismatch(ctx: Context) -> None:
    ref = ctx.new_ref()
    resp = ctx.client.put(_res(ctx, ref), json=_body("someone-else", {"userLabel": "x"}), headers={"Content-Type": YANG_JSON})
    _expect(resp, 400, what="a PUT whose body names another object")


@check("RC-9", "RESTCONF", "a body that is not a list entry is refused with 400 and an ietf-restconf:errors body")
def rc_malformed(ctx: Context) -> None:
    resp = ctx.client.put(_res(ctx, ctx.new_ref()), content=b"not json", headers={"Content-Type": YANG_JSON})
    _expect(resp, 400, what="a PUT of a body that is not JSON")
    try:
        errors = resp.json()["ietf-restconf:errors"]["error"]
        tag = errors[0]["error-tag"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise Fail("the refusal has no ietf-restconf:errors body with an error-tag") from None
    if not tag:
        raise Fail("the refusal's error-tag is empty")


@check("RC-10", "RESTCONF", "a PATCH that names no attribute is refused with 400")
def rc_empty_patch(ctx: Context) -> None:
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "x"}), 201, 204, 200, what="the PUT")
    _expect(_patch(ctx, ref, {}), 400, what="an empty PATCH")


# the emitting groups (FM, PM, SW, HB) register themselves here, after the CM groups
from . import emit_checks  # noqa: E402,F401
