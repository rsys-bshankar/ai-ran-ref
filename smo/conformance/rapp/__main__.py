"""python -m conformance.rapp package|runtime: run the rApp conformance checks and write a report.

The command line of the rApp conformance pack. `package` checks .csar files offline; `runtime` takes a package through its life on a running stack, reaching the modules through the R1 gateway (`--r1`) or by name
(`--direct`). It builds the contexts of kit.py, runs the registered checks and prints and optionally writes the report. Exit status: 0 when no check failed (warnings and
skips do not fail), 1 when a check failed, 2 when the command could not run (bad options, no .csar file, an unreadable file or trust store: nothing was checked).
The first line of this docstring is also the `--help` description, so edit it as user-facing text. Importing `package_checks` and `runtime_checks` is what registers the checks. Described in `conformance/rapp/README.md`.
"""

import argparse
import sys
from pathlib import Path

import httpx

# Both modules are imported for their side effect: loading them fills the check registry.
from . import package_checks, runtime_checks  # noqa: F401  (registers the checks)
from .kit import FAIL, PACKAGE, REGISTRY, RUNTIME, PackageContext, Result, RuntimeContext, run, summary, to_json, to_markdown


class _Services(dict):
    """A dict of module name to an `httpx.Client` for that module, built on first use, so a run opens a client only for the modules its checks call."""

    def __init__(self, make):
        super().__init__()
        self._make = make

    def __missing__(self, name):
        self[name] = self._make(name)
        return self[name]


def _headers(values: list[str]) -> dict[str, str]:
    """Parse `NAME:VALUE` strings into a header dict; an entry with an empty value (or no colon) is dropped."""
    return {k.strip(): v.strip() for k, _, v in (h.partition(":") for h in values) if v}


def _package_files(paths: list[str]) -> list[Path]:
    """The .csar files to check: each argument that is a directory contributes its `*.csar` files in name order, any other argument is taken as a file."""
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        files += sorted(path.glob("*.csar")) if path.is_dir() else [path]
    return files


def _write(out: str | None, title: str, results: list[Result], intro: str, extra: dict) -> str:
    """Render the Markdown report and, when `out` is given, write `<out>.json` and `<out>.md`; returns the Markdown to print."""
    text = to_markdown(title, results, intro)
    if out:
        Path(out + ".json").write_text(to_json(title, results, extra), encoding="utf-8")
        Path(out + ".md").write_text(text, encoding="utf-8")
    return text


def run_package(args: argparse.Namespace) -> int:
    """Run the offline checks on each package file and return the exit status (0, 1 or 2).

    Returns 2, before any check, when the trust store cannot be loaded, no .csar file is found or a file cannot be read, so that a usage mistake is never reported as a pass.
    """
    from smo_shared import csar_signing

    trust = None
    if args.trust:
        try:
            trust = csar_signing.load_trust_store(args.trust)
        except csar_signing.TrustStoreError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    files = _package_files(args.packages)
    if not files:
        print("error: no .csar file to check", file=sys.stderr)
        return 2
    results: list[Result] = []
    for path in files:
        try:
            data = path.read_bytes()
        except OSError as exc:
            print(f"error: {path}: {exc.strerror or type(exc).__name__}", file=sys.stderr)
            return 2
        results += run(PackageContext(path, data, trust, require_signed=args.require_signed), set(args.only) or None)
    intro = (f"Signatures checked against {args.trust}." if trust else "No trust store given: signatures are not checked.") + (" Signed packages are required." if args.require_signed else "")
    print(_write(args.out, ", ".join(p.name for p in files), results, intro, {"kind": PACKAGE, "packages": [str(p) for p in files], "trustStore": args.trust}))
    return 1 if summary(results)[FAIL] else 0


def run_runtime(args: argparse.Namespace, services=None) -> int:
    """Run the runtime checks against a stack and return the exit status.

    `services` is injectable (the tests pass the in-process mesh's clients); otherwise exactly one of `--r1` and `--direct` is required (2 if neither or both). The run removes
    what it created before it returns (kit.run's clean-up), also after a failed check.
    """
    headers = _headers(args.header)
    if services is None:
        if args.direct and args.r1:
            print("error: give --r1 or --direct, not both", file=sys.stderr)
            return 2
        if not args.direct and not args.r1:
            print("error: give --r1 URL (through the gateway; add --header 'Authorization: Bearer ...') or --direct (the modules by name, e.g. on the compose network)", file=sys.stderr)
            return 2
        if args.direct:
            services = _Services(lambda name: httpx.Client(base_url=args.direct_template.format(service=name), timeout=args.timeout, headers=headers))
        else:
            services = _Services(lambda name: httpx.Client(base_url=f"{args.r1.rstrip('/')}/{name}", timeout=args.timeout, headers=headers))
    ctx = RuntimeContext(services, args.package_url, autonomy_mode=args.autonomy_mode, keep=args.keep)
    results = run(ctx, set(args.only) or None)
    where = args.r1 or (args.direct_template if args.direct else "the given services")
    print(_write(args.out, args.package_url, results, f"Platform: {where}.", {"kind": RUNTIME, "platform": where, "packageUrl": args.package_url, "runId": ctx.run_id}))
    return 1 if summary(results)[FAIL] else 0


def main(argv: list[str] | None = None, services=None) -> int:
    """Parse the command line and dispatch to `run_package` or `run_runtime`; `--list` prints the check registry and returns 0; no sub-command prints the usage and returns 2."""
    ap = argparse.ArgumentParser(prog="python -m conformance.rapp", description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="list the checks and exit")
    commands = ap.add_subparsers(dest="command")
    pk = commands.add_parser("package", help="offline: check package files against Onboarding's validation")
    pk.add_argument("packages", nargs="+", help=".csar files, or directories holding them")
    pk.add_argument("--trust", metavar="PATH", help="a PEM public key file or a directory of <publisher>.pub files: check the signature against it, as ONBOARDING_TRUST_STORE does")
    pk.add_argument("--require-signed", action="store_true", help="an unsigned package fails (ONBOARDING_REQUIRE_SIGNED_PACKAGES); needs --trust")
    pk.add_argument("--only", action="append", default=[], help="a check id (PK-4) or a group (STRUCTURE, MANIFEST, HYGIENE, SIGNATURE, ONBOARDING); repeatable")
    pk.add_argument("--out", help="write <out>.json and <out>.md")
    rt = commands.add_parser("runtime", help="against a running stack: onboard, register, heartbeat, R1 usage, terminate")
    rt.add_argument("--package-url", required=True, help="where Onboarding can fetch the .csar from (it is Onboarding that fetches it, so the address must be reachable from there)")
    rt.add_argument("--r1", metavar="URL", help="the R1 gateway's origin; the routes are /onboarding/... and /rapp-mgmt/...")
    rt.add_argument("--header", action="append", default=[], metavar="NAME:VALUE", help="a header for every request, e.g. 'Authorization: Bearer <token>' for the gateway")
    rt.add_argument("--direct", action="store_true", help="talk to the modules themselves (no gateway, no token): a lab or CI network where http://onboarding:8000 answers")
    rt.add_argument("--direct-template", default="http://{service}:8000", help="with --direct: the address of a module, {service} being onboarding or rapp-mgmt")
    rt.add_argument("--autonomy-mode", choices=["SHADOW", "ASSIST", "AUTONOMOUS"], default="SHADOW")
    rt.add_argument("--keep", action="store_true", help="leave the package onboarded (the instance is always removed): a second run of the same package then fails RT-1, being a duplicate")
    rt.add_argument("--timeout", type=float, default=30.0)
    rt.add_argument("--only", action="append", default=[], help="a check id (RT-4) or a group (ONBOARD, REGISTER, HEARTBEAT, R1-USAGE, TERMINATE); repeatable")
    rt.add_argument("--out", help="write <out>.json and <out>.md")
    args = ap.parse_args(argv)
    if args.list:
        for item in REGISTRY:
            print(f"{item.id:6} {item.kind:8} {item.group:11} {item.title}")
        return 0
    if args.command == "package":
        if args.require_signed and not args.trust:
            print("error: --require-signed needs --trust", file=sys.stderr)
            return 2
        return run_package(args)
    if args.command == "runtime":
        return run_runtime(args, services)
    ap.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
