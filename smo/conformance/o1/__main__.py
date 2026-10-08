"""python -m conformance.o1 --adaptor URL: run the checks against one adaptor and write a report."""

import argparse
import sys
from pathlib import Path

import httpx

from . import checks  # noqa: F401  (registers the checks)
from .kit import FAIL, Context, REGISTRY, run, summary, to_json, to_markdown


def protocols_from(declared_modes: list[str], choice: str) -> set[str]:
    if choice == "both":
        return {"netconf", "restconf"}
    if choice in ("netconf", "restconf"):
        return {choice}
    return {m.removeprefix("O1_").lower() for m in declared_modes} & {"netconf", "restconf"}        # auto: what the adaptor says it speaks


def main(argv: list[str] | None = None, client: httpx.Client | None = None, oam_client: httpx.Client | None = None,
         emit_client: httpx.Client | None = None) -> int:
    """The clients are injectable (tests run the kit in process); by default they are built from --adaptor, --oam-url and --emit-url."""
    ap = argparse.ArgumentParser(prog="python -m conformance.o1", description=__doc__.splitlines()[0])
    ap.add_argument("--adaptor", required=True, help="the adaptor's origin, e.g. http://adaptor:8000 (the registered adaptor_uri without its path)")
    ap.add_argument("--protocol", choices=["auto", "netconf", "restconf", "both"], default="auto", help="auto: the transports the adaptor declares at /capabilities")
    ap.add_argument("--netconf-path", default="/edit-config")
    ap.add_argument("--restconf-root", default="/restconf")
    ap.add_argument("--capabilities-path", default="/capabilities")
    ap.add_argument("--emit-url", help="the adaptor's trigger API origin for the FM, PM, SW and HB groups (default: --adaptor)")
    ap.add_argument("--emit-prefix", default="/emit", help="the path the trigger routes are under: <emit-url><prefix>/alarm, /pm-report, /pm-file, /heartbeat, /software-phase")
    ap.add_argument("--oam-url", help="RAN NF OAM's origin, e.g. http://ran-nf-oam:8000: the FM, PM, SW and HB groups prepare an element there and read back what arrived (without it they are skipped)")
    ap.add_argument("--element", metavar="REF", help="a managed element already registered at RAN NF OAM (with its O1 adaptor endpoint) to use; default: the kit registers a new one per run")
    ap.add_argument("--header", action="append", default=[], metavar="NAME:VALUE", help="a header to send with every request (an adaptor behind a gateway)")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--only", action="append", default=[], help="a check id (NC-2) or a group (DISC, NETCONF, RESTCONF, FM, PM, SW, HB); repeatable")
    ap.add_argument("--out", help="write <out>.json and <out>.md")
    ap.add_argument("--list", action="store_true", help="list the checks and exit")
    args = ap.parse_args(argv)
    if args.list:
        for item in REGISTRY:
            print(f"{item.id:9} {item.group:9} {item.title}")
        return 0
    headers = dict(h.split(":", 1) for h in args.header if ":" in h)
    sent = {k.strip(): v.strip() for k, v in headers.items()}
    http = client or httpx.Client(base_url=args.adaptor.rstrip("/"), timeout=args.timeout, headers=sent)
    oam = oam_client or (httpx.Client(base_url=args.oam_url.rstrip("/"), timeout=args.timeout) if args.oam_url else None)   # RAN NF OAM is not behind the adaptor's gateway
    emit = emit_client or (httpx.Client(base_url=args.emit_url.rstrip("/"), timeout=args.timeout, headers=sent) if args.emit_url else None)
    ctx = Context(http, set(), netconf_path=args.netconf_path, restconf_root=args.restconf_root, capabilities_path=args.capabilities_path,
                  oam=oam, emit=emit, emit_prefix=args.emit_prefix, element=args.element)
    try:
        declared = http.get(args.capabilities_path)
        modes = declared.json().get("supportedVendorModes", []) if declared.status_code == 200 else []
    except (httpx.HTTPError, ValueError, AttributeError):
        modes = []
    ctx.protocols = protocols_from(modes, args.protocol)
    results = run(ctx, set(args.only) or None)
    text = to_markdown(args.adaptor, ctx.protocols, results, args.oam_url)
    print(text)
    if args.out:
        Path(args.out + ".json").write_text(to_json(args.adaptor, ctx.protocols, results, args.oam_url), encoding="utf-8")
        Path(args.out + ".md").write_text(text, encoding="utf-8")
    return 1 if summary(results)[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
