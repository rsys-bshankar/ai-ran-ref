"""The O1 adaptor checks that need no RAN NF OAM: discovery (`GET /capabilities`, group DISC) and the two configuration-management transports RAN NF OAM uses
(NETCONF-shaped XML, group NETCONF; RESTCONF-shaped JSON, group RESTCONF).

Each check states in its title one thing RAN NF OAM depends on and raises `Fail("...")` with what the adaptor did instead. The wire forms mirror RAN NF OAM's clients
(`ran-nf-oam/app/netconf_client.py`: an `<rpc><edit-config>` or `<get-config>` posted as XML to the registered URI; `restconf_client.py`: RFC 8040 data resources under a
RESTCONF root). Every write is read back, because an adaptor that acknowledges a write and keeps nothing is the commonest real defect. The checks create managed objects
with references from `Context.new_ref` and never remove them. XML replies are parsed with `defusedxml`, because the adaptor under test is an untrusted peer.
`KNOWN_SERVICES` and `KNOWN_MODES` are the vocabulary of the vendor capability declaration and must match what RAN NF OAM accepts; the emitting checks (emit_checks.py,
imported at the bottom) reuse `KNOWN_SERVICES`. Design record: `HISTORY.md` PR-SB-9a.
"""

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
    """The adaptor's capability declaration as a dict, or `Fail` when `GET <capabilities path>` is not a 200 with a JSON object body."""
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
    """DISC-1: the declaration has a non-empty string `vendorName` and non-empty lists of strings `supportedServices` and `supportedVendorModes`.

    On success the declaration is stored in `ctx.declared`, which DISC-2, DISC-3 and the emitting groups read.
    """
    body = _capabilities(ctx)
    missing = [k for k in ("vendorName", "supportedServices", "supportedVendorModes") if k not in body]
    if missing:
        raise Fail(f"missing {', '.join(missing)}")
    if not isinstance(body["vendorName"], str) or not body["vendorName"]:
        raise Fail("vendorName is not a non-empty string")
    for key in ("supportedServices", "supportedVendorModes"):
        # Both lists must be non-empty: an adaptor that declares no service or no transport cannot be registered and used.
        if not isinstance(body[key], list) or not body[key] or not all(isinstance(v, str) for v in body[key]):
            raise Fail(f"{key} is not a non-empty list of strings")
    ctx.declared.update(body)


@check("DISC-2", "DISC", "every declared service and transport is one RAN NF OAM knows")
def disc_vocabulary(ctx: Context) -> None:
    """DISC-2: every declared service is in `KNOWN_SERVICES` and every declared transport in `KNOWN_MODES`; the failure lists the unknown ones and the known sets."""
    if not ctx.declared:
        disc_shape(ctx)
    unknown_services = sorted(set(ctx.declared["supportedServices"]) - KNOWN_SERVICES)
    unknown_modes = sorted(set(ctx.declared["supportedVendorModes"]) - KNOWN_MODES)
    if unknown_services or unknown_modes:
        raise Fail(f"unknown services {unknown_services}, unknown transports {unknown_modes} (known: {sorted(KNOWN_SERVICES)}, {sorted(KNOWN_MODES)})")


@check("DISC-3", "DISC", "a transport that is declared is one this run exercises (and the declaration covers the ones it does)")
def disc_modes_match_run(ctx: Context) -> None:
    """DISC-3: every transport this run exercises is one the adaptor declares in `supportedVendorModes`.

    The check is one-directional: a declared transport that the run does not exercise (for example `--protocol netconf` against an adaptor that declares both) is not an error.
    """
    if not ctx.declared:
        disc_shape(ctx)
    declared = {m.removeprefix("O1_").lower() for m in ctx.declared["supportedVendorModes"]}
    ran = ctx.protocols
    undeclared = sorted(ran - declared)
    if undeclared:
        raise Fail(f"the run exercises {undeclared} but the adaptor does not declare it in supportedVendorModes")


# ------------------------------------------------------------------------------------------------------------ NETCONF-shaped

def _rpc(message_id: str, inner: str) -> str:
    """Wrap `inner` in a NETCONF `<rpc>` with the given message-id in the base namespace."""
    return f'<rpc message-id="{message_id}" xmlns="{NS}">{inner}</rpc>'


def _edit(message_id: str, ref: str, attrs: dict, operation: str = "merge", function_ref: str | None = None) -> str:
    """An `<edit-config>` request that applies `operation` (merge or delete) to the managed object `ref`, or to its managed function `function_ref`, with `attrs` as child elements.

    Values are XML-escaped and the ref and function-ref are attribute-quoted with `quoteattr`, so a value such as `a<b & c>d` reaches the adaptor as data (NC-10 relies on it).
    """
    function = f" function-ref={quoteattr(function_ref)}" if function_ref else ""
    body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in attrs.items())
    return _rpc(message_id, f"<edit-config><target><running/></target><config><managed-object ref={quoteattr(ref)}{function} operation=\"{operation}\">{body}"
                            f"</managed-object></config></edit-config>")


def _get(message_id: str, ref: str, function_ref: str | None = None) -> str:
    """A `<get-config>` request for the managed object `ref` (or its function `function_ref`) from the running datastore."""
    function = f" function-ref={quoteattr(function_ref)}" if function_ref else ""
    return _rpc(message_id, f"<get-config><source><running/></source><filter><managed-object ref={quoteattr(ref)}{function}/></filter></get-config>")


def _post(ctx: Context, xml: str):
    """POST a NETCONF request body as `application/xml` to the adaptor's NETCONF path; the raw response."""
    return ctx.client.post(ctx.netconf_path, content=xml.encode(), headers={"Content-Type": "application/xml"})


def _parse_reply(resp):
    """Parse an adaptor's answer to a NETCONF request into `(root, error_tag)`; `error_tag` is None when the reply has no `<rpc-error>`, the error's `error-tag` (or "rpc-error") when it has.

    Fails the check when the status is 5xx, the body is not XML (or is XML that `defusedxml` refuses, such as one with entities) or its root is not `<rpc-reply>`. A 4xx with a
    proper `<rpc-reply><rpc-error>` is a valid refusal, not a failure here.
    """
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
    """POST `xml` and require an `<rpc-reply>` holding `<ok/>`; a refusal or a reply with neither `<ok/>` nor an error fails the check, naming `what` was being done."""
    root, error = _parse_reply(_post(ctx, xml))
    if error is not None:
        raise Fail(f"{what} was refused with {error}")
    if not any(local(c.tag) == "ok" for c in root):
        raise Fail(f"{what} was answered with neither <ok/> nor <rpc-error>")
    return root


def _read(ctx: Context, ref: str, function_ref: str | None = None) -> dict:
    """The attributes of a managed object (or function) as the adaptor's `get-config` reports them: `{element name: text}`. An object the adaptor holds nothing for reads as `{}`.

    Fails when the request is refused or the reply has no `<data>`.
    """
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
    """NC-2: attributes written with a merge are read back by get-config; an adaptor that answers `<ok/>` and keeps nothing fails here."""
    ref = ctx.new_ref()
    _ok(ctx, _edit("1", ref, {"administrativeState": "LOCKED", "userLabel": "conformance"}), "the write")
    got = _read(ctx, ref)
    if got.get("administrativeState") != "LOCKED" or got.get("userLabel") != "conformance":
        raise Fail(f"wrote administrativeState=LOCKED and userLabel=conformance, read back {got}")


@check("NC-3", "NETCONF", "a second merge keeps the attributes the first one wrote")
def nc_merge_semantics(ctx: Context) -> None:
    """NC-3: a second merge on the same object adds its attributes and keeps the ones the first wrote, so a merge is not a replace."""
    ref = ctx.new_ref()
    _ok(ctx, _edit("1", ref, {"userLabel": "one"}), "the first merge")
    _ok(ctx, _edit("2", ref, {"administrativeState": "LOCKED"}), "the second merge")
    got = _read(ctx, ref)
    if got.get("userLabel") != "one" or got.get("administrativeState") != "LOCKED":
        raise Fail(f"after two merges the object reads {got}")


@check("NC-4", "NETCONF", "a managed function is addressed apart from its element (function-ref)")
def nc_function_ref(ctx: Context) -> None:
    """NC-4: a write addressed to a managed function (function-ref) is stored on that function and does not change the element it belongs to."""
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
    """NC-5: after a `delete` operation the object no longer reads back the value it was given."""
    ref = ctx.new_ref()
    _ok(ctx, _edit("1", ref, {"userLabel": "gone-soon"}), "the write")
    _ok(ctx, _edit("2", ref, {}, operation="delete"), "the delete")
    if _read(ctx, ref).get("userLabel") == "gone-soon":
        raise Fail("after a delete the object still reads what it was given")


@check("NC-6", "NETCONF", "a merge that carries no attribute is refused with an <rpc-error>, not acknowledged")
def nc_empty_merge(ctx: Context) -> None:
    """NC-6: a merge that names no attribute is refused with an `<rpc-error>`; acknowledging it would let a malformed request pass as a change."""
    _, error = _parse_reply(_post(ctx, _edit("1", ctx.new_ref(), {})))
    if error is None:
        raise Fail("an empty merge was acknowledged with <ok/>")


@check("NC-7", "NETCONF", "a request with no managed-object ref is refused with an <rpc-error>")
def nc_missing_ref(ctx: Context) -> None:
    """NC-7: an edit-config whose `<managed-object>` has no `ref` is refused with an `<rpc-error>`."""
    xml = _rpc("1", "<edit-config><target><running/></target><config><managed-object operation=\"merge\"><userLabel>x</userLabel></managed-object></config></edit-config>")
    _, error = _parse_reply(_post(ctx, xml))
    if error is None:
        raise Fail("an edit-config without a ref was acknowledged")


@check("NC-8", "NETCONF", "a body that is not XML is answered, not crashed on (no 5xx)")
def nc_malformed(ctx: Context) -> None:
    """NC-8: a body that is not well-formed XML is answered below 500 (a crash is a defect), and a 200 answer to it must carry an error, not `<ok/>`."""
    resp = _post(ctx, "<rpc><edit-config>")
    if resp.status_code >= 500:
        raise Fail(f"HTTP {resp.status_code} for unparseable XML")
    if resp.status_code == 200:
        _, error = _parse_reply(resp)
        if error is None:
            raise Fail("unparseable XML was acknowledged with <ok/>")


@check("NC-9", "NETCONF", "the reply carries the request's message-id")
def nc_message_id(ctx: Context) -> None:
    """NC-9: the `<rpc-reply>` echoes the request's `message-id`, which RAN NF OAM's client uses to match an answer to a request."""
    root, _ = _parse_reply(_post(ctx, _edit("conf-msg-42", ctx.new_ref(), {"userLabel": "id"})))
    if root.attrib.get("message-id") != "conf-msg-42":
        raise Fail(f"message-id conf-msg-42 came back as {root.attrib.get('message-id')!r}")


@check("NC-10", "NETCONF", "values with XML special characters round-trip, escaped")
def nc_escaping(ctx: Context) -> None:
    """NC-10: a value holding `<`, `&` and `>` is stored and read back unchanged, so the adaptor escapes on write and read."""
    ref = ctx.new_ref()
    value = "a<b & c>d"
    _ok(ctx, _edit("1", ref, {"userLabel": value}), "the write")
    got = _read(ctx, ref).get("userLabel")
    if got != value:
        raise Fail(f"wrote {value!r}, read back {got!r}")


@check("NC-11", "NETCONF", "an XML entity in a request is not expanded (no entity expansion, no 5xx)")
def nc_no_entity_expansion(ctx: Context) -> None:
    """NC-11: a request that declares an XML entity is not expanded by the adaptor: no 5xx, no echo of the expansion, nothing stored with it.

    This is the classic XXE and entity-expansion probe; the entity text is a marker that cannot appear unless the adaptor's parser expanded the declaration.
    """
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
    """The RESTCONF resource path of a managed element (or of its managed function) under the configured root; the key is percent-encoded completely (`safe=''`)."""
    path = f"{ctx.restconf_root}/data/managed-element={quote(ref, safe='')}"
    if function_ref:
        path += f"/managed-function={quote(function_ref, safe='')}"
    return path


def _body(ref: str, attrs: dict, function_ref: str | None = None) -> dict:
    """The RFC 8040 list-entry body for a managed element, or for a managed function when `function_ref` is given: a one-entry list keyed by `ref` or `function-ref`."""
    if function_ref:
        return {"managed-function": [{"function-ref": function_ref, **attrs}]}
    return {"managed-element": [{"ref": ref, **attrs}]}


def _put(ctx: Context, ref: str, attrs: dict, function_ref: str | None = None):
    """PUT a managed element (or function) with `attrs`; the raw response. PUT replaces the resource."""
    return ctx.client.put(_res(ctx, ref, function_ref), json=_body(ref, attrs, function_ref), headers={"Content-Type": YANG_JSON})


def _patch(ctx: Context, ref: str, attrs: dict, function_ref: str | None = None, body: dict | None = None):
    """PATCH a managed element (or function) with `attrs`, or with the explicit `body` when given; the raw response. PATCH merges into the resource."""
    return ctx.client.patch(_res(ctx, ref, function_ref), json=body if body is not None else _body(ref, attrs, function_ref), headers={"Content-Type": YANG_JSON})


def _rc_read(ctx: Context, ref: str, function_ref: str | None = None) -> dict:
    """The attributes of a managed element (or function) read with GET as `{name: str(value)}`; fails when the answer is not 200 or its body is not a one-entry list of the expected kind."""
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
    """Fail the check, naming `what`, unless the response status is one of `statuses`."""
    if resp.status_code not in statuses:
        raise Fail(f"{what} answered {resp.status_code}, expected {' or '.join(map(str, statuses))}")


@check("RC-1", "RESTCONF", "/.well-known/host-meta names the RESTCONF root")
def rc_host_meta(ctx: Context) -> None:
    """RC-1: `/.well-known/host-meta` answers 200 and has a link with `rel=restconf`, which is how a RESTCONF client discovers the root (RFC 8040 section 3.1)."""
    resp = ctx.client.get("/.well-known/host-meta")
    _expect(resp, 200, what="GET /.well-known/host-meta")
    if 'rel="restconf"' not in resp.text and "rel='restconf'" not in resp.text:
        raise Fail("host-meta has no Link with rel=restconf")


@check("RC-2", "RESTCONF", "a PUT of a new object answers 201 and a PUT of an existing one answers 204")
def rc_put_statuses(ctx: Context) -> None:
    """RC-2: the first PUT of an object answers 201 and a PUT of an existing one 204; the check also accepts 200 for the second, which RFC 8040 does not use for PUT."""
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "one"}), 201, what="the first PUT")
    _expect(_put(ctx, ref, {"userLabel": "two"}), 204, 200, what="the second PUT")


@check("RC-3", "RESTCONF", "a GET reads back what a PUT wrote, as a list entry keyed by the object")
def rc_read_after_write(ctx: Context) -> None:
    """RC-3: a GET after a PUT returns the entry keyed by the object's `ref` with the attribute that was written."""
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"administrativeState": "LOCKED"}), 201, 204, 200, what="the PUT")
    got = _rc_read(ctx, ref)
    if got.get("ref") != ref or got.get("administrativeState") != "LOCKED":
        raise Fail(f"wrote ref={ref} administrativeState=LOCKED, read back {got}")


@check("RC-4", "RESTCONF", "a PATCH merges: it changes what it names and keeps the rest")
def rc_patch_merges(ctx: Context) -> None:
    """RC-4: a PATCH changes the attributes it names and keeps the others."""
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "kept"}), 201, 204, 200, what="the PUT")
    _expect(_patch(ctx, ref, {"administrativeState": "LOCKED"}), 204, 200, what="the PATCH")
    got = _rc_read(ctx, ref)
    if got.get("userLabel") != "kept" or got.get("administrativeState") != "LOCKED":
        raise Fail(f"after the PATCH the object reads {got}")


@check("RC-5", "RESTCONF", "a PUT replaces: attributes it does not name are gone")
def rc_put_replaces(ctx: Context) -> None:
    """RC-5: a second PUT replaces the resource, so an attribute the first PUT set and the second does not name is no longer there."""
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "first", "administrativeState": "LOCKED"}), 201, 204, 200, what="the first PUT")
    _expect(_put(ctx, ref, {"userLabel": "second"}), 204, 200, what="the second PUT")
    got = _rc_read(ctx, ref)
    if got.get("userLabel") != "second" or "administrativeState" in got and got["administrativeState"] == "LOCKED":
        raise Fail(f"after a replace the object reads {got}")


@check("RC-6", "RESTCONF", "a managed function is a resource below its element")
def rc_function_resource(ctx: Context) -> None:
    """RC-6: a managed function is a resource below its element and reads back with its `function-ref` and its own attributes."""
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "element"}), 201, 204, 200, what="the PUT of the element")
    _expect(_put(ctx, ref, {"administrativeState": "LOCKED"}, "NRCellDU=101"), 201, 204, 200, what="the PUT of the function")
    got = _rc_read(ctx, ref, "NRCellDU=101")
    if got.get("function-ref") != "NRCellDU=101" or got.get("administrativeState") != "LOCKED":
        raise Fail(f"the function reads {got}")


@check("RC-7", "RESTCONF", "a DELETE answers 204 and a second DELETE answers 404")
def rc_delete(ctx: Context) -> None:
    """RC-7: DELETE of an existing object answers 204 and a second DELETE answers 404."""
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "x"}), 201, 204, 200, what="the PUT")
    _expect(ctx.client.delete(_res(ctx, ref)), 204, what="the DELETE")
    _expect(ctx.client.delete(_res(ctx, ref)), 404, what="the second DELETE")


@check("RC-8", "RESTCONF", "a body whose key does not match the resource is refused with 400")
def rc_key_mismatch(ctx: Context) -> None:
    """RC-8: a PUT whose body is keyed to another object than the resource path is refused with 400."""
    ref = ctx.new_ref()
    resp = ctx.client.put(_res(ctx, ref), json=_body("someone-else", {"userLabel": "x"}), headers={"Content-Type": YANG_JSON})
    _expect(resp, 400, what="a PUT whose body names another object")


@check("RC-9", "RESTCONF", "a body that is not a list entry is refused with 400 and an ietf-restconf:errors body")
def rc_malformed(ctx: Context) -> None:
    """RC-9: a body that is not JSON is refused with 400 and an `ietf-restconf:errors` body whose first error has a non-empty `error-tag`."""
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
    """RC-10: a PATCH that names no attribute is refused with 400 instead of being acknowledged as a no-op."""
    ref = ctx.new_ref()
    _expect(_put(ctx, ref, {"userLabel": "x"}), 201, 204, 200, what="the PUT")
    _expect(_patch(ctx, ref, {}), 400, what="an empty PATCH")


# the emitting groups (FM, PM, SW, HB) register themselves here, after the CM groups
# Imported last because emit_checks imports KNOWN_SERVICES from this module; importing it registers the emitting checks after the CM ones, which fixes the report order.
from . import emit_checks  # noqa: E402,F401
