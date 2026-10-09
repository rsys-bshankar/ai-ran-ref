#!/usr/bin/env python3
"""Sign and verify an rApp package (PR-RAPP-1): for the developer who ships a CSAR and the operator who wants to check one.

    python scripts/csar_sign.py keygen --out my-publisher         # my-publisher.key.pem (keep secret) and my-publisher.pub (give to operators)
    python scripts/csar_sign.py sign my-rapp.csar --key my-publisher.key.pem [--out signed.csar]
    python scripts/csar_sign.py verify my-rapp.csar --trust DIR_OR_FILE
    python scripts/csar_sign.py digests my-rapp.csar              # what a package's files hash to, and whether it carries a signature

`sign` adds `TOSCA-Metadata/DIGESTS.sha256` (the sha-256 of every other file) and `TOSCA-Metadata/DIGESTS.sha256.sig` (an ed25519 signature over it) and writes
the result to `--out` (default: the same file). `verify` is exactly what Onboarding does when `ONBOARDING_TRUST_STORE` is set: the trust store is a PEM public
key file or a directory of `*.pub` / `*.pem`, and the publisher printed is the name of the key's file. Exit status: 0 verified (or keygen/sign done),
1 the package is not acceptable (the message says why), 2 a usage or file problem.

Needs `cryptography` (a locked dependency of the SMO's runtime). The file format and the rules are in `shared/smo_shared/csar_signing.py` and
`docs/RAPP_PACKAGING.md` "Signing". The demo key under `samples/demo-signing/` is public on purpose: never trust it for anything that matters.
"""

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))      # smo_shared, when it is not installed

from smo_shared import csar_signing as cs  # noqa: E402


def _fail(message: str, code: int = 2) -> int:
    """Prints `error: <message>` to stderr and returns the exit code (2, a usage or file problem, unless told otherwise)."""
    print(f"error: {message}", file=sys.stderr)
    return code


def _keygen(args: argparse.Namespace) -> int:
    """`keygen`: writes `<out>.key.pem` (mode 0600) and `<out>.pub` and prints the key id. Refuses, writing nothing, when either file already exists."""
    private_path, public_path = Path(f"{args.out}.key.pem"), Path(f"{args.out}.pub")
    for path in (private_path, public_path):
        if path.exists():
            return _fail(f"{path} exists already; remove it or choose another --out")
    private_pem, public_pem = cs.generate_keypair()
    private_path.write_bytes(private_pem)
    private_path.chmod(0o600)
    public_path.write_bytes(public_pem)
    print(f"wrote {private_path} (private: keep it in a secret store) and {public_path} (public: hand it to the operators' trust store; the publisher is named {public_path.stem})")
    print(f"key id {cs.key_id(cs.load_public_key(public_pem))}")
    return 0


def _read(path: str) -> bytes | None:
    """The bytes of a file, or None after printing an error when it cannot be read; the caller returns exit code 2."""
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        _fail(f"{path}: {exc.strerror or type(exc).__name__}")
        return None


def _sign(args: argparse.Namespace) -> int:
    """`sign`: adds the digest list and its ed25519 signature to the package and writes it to `--out` (default: over the input file).

        Exit 2 when a file cannot be read, the key is not usable, or the package is rejected by `sign_csar` (not a valid zip, for example).
    """
    data, key_bytes = _read(args.package), _read(args.key)
    if data is None or key_bytes is None:
        return 2
    try:
        key = cs.load_private_key(key_bytes)
        signed = cs.sign_csar(data, key)
    except ValueError as exc:
        return _fail(f"{args.key}: {exc}")
    except (cs.SignatureError, zipfile.BadZipFile) as exc:
        return _fail(f"{args.package}: {exc}")
    out = Path(args.out or args.package)
    out.write_bytes(signed)
    print(f"signed {out} with key {cs.key_id(key.public_key())}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    """`verify`: checks each package against the trust store and prints `OK` or `REJECTED` with the error code per package.

        Returns 0 when all verify, 1 when any is rejected, and 2 when the trust store cannot be loaded or any package cannot be read (2 takes precedence over 1).
    """
    try:
        trust = cs.load_trust_store(args.trust)
    except cs.TrustStoreError as exc:
        return _fail(str(exc))
    status = 0
    for package in args.packages:
        data = _read(package)
        if data is None:
            status = 2
            continue
        try:
            result = cs.verify_csar(data, trust)
        except cs.SignatureError as exc:
            print(f"REJECTED {package}: {exc} [{exc.code}]")
            status = max(status, 1)
        else:
            print(f"OK {package}: signed by {result.publisher} (key {result.key_id[:16]}...), {result.files} files match their digests")
    return status


def _digests(args: argparse.Namespace) -> int:
    """`digests`: prints the sha-256 and name of every file in the package except the signing files, then whether the package is signed (yes, no, or incomplete)."""
    data = _read(args.package)
    if data is None:
        return 2
    try:
        with zipfile.ZipFile(args.package) as z:
            names = [i.filename for i in z.infolist() if not i.is_dir()]
            for name in sorted(n for n in names if n not in cs.SIGNING_FILES):
                print(f"{hashlib.sha256(z.read(name)).hexdigest()}  {name}")
    except zipfile.BadZipFile:
        return _fail(f"{args.package} is not a zip file")
    print("signed: " + ("yes (digest list and signature present)" if set(cs.SIGNING_FILES) <= set(names) else "no" if not cs.is_signed(names) else "incomplete"))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command-line entry for `keygen`, `sign`, `verify` and `digests`; returns the command's exit status (0 ok, 1 package not acceptable, 2 usage or file problem)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen", help="make an ed25519 key pair")
    keygen.add_argument("--out", required=True, help="path prefix: writes <out>.key.pem and <out>.pub")
    sign = commands.add_parser("sign", help="add the digest list and a signature to a package")
    sign.add_argument("package")
    sign.add_argument("--key", required=True, help="the private key (PEM, or an ed25519-seed line)")
    sign.add_argument("--out", help="write here instead of over the package")
    verify = commands.add_parser("verify", help="check packages against a trust store, as Onboarding does")
    verify.add_argument("packages", nargs="+")
    verify.add_argument("--trust", required=True, help="a PEM public key file, or a directory of *.pub / *.pem (the file name is the publisher)")
    digests = commands.add_parser("digests", help="print the digest of every file of a package")
    digests.add_argument("package")
    args = parser.parse_args(argv)
    return {"keygen": _keygen, "sign": _sign, "verify": _verify, "digests": _digests}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
