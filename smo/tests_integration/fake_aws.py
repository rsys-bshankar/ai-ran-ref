#!/usr/bin/env python3
"""A stand-in for the AWS CLI that keeps "buckets" in a directory (FAKE_AWS_ROOT), for tests_integration/test_dr_scripts.py.

Understands the three calls the disaster-recovery scripts make (`aws [--endpoint-url U] s3 cp|ls|rm ...`, flags `--only-show-errors`,
`--sse X` and `--recursive` accepted) and nothing else; any other call exits 2. Not a model of S3: the real round trip against MinIO is the CI job
`disaster-recovery` (.github/workflows/smo-dr.yml).
"""

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(os.environ["FAKE_AWS_ROOT"])


def _path(url: str) -> Path:
    assert url.startswith("s3://"), url
    return ROOT / url[len("s3://"):]


def main(argv: list[str]) -> int:
    positional: list[str] = []
    recursive = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--endpoint-url", "--sse"):
            i += 1
        elif arg == "--recursive":
            recursive = True
        elif arg.startswith("--"):
            pass
        else:
            positional.append(arg)
        i += 1
    if len(positional) < 3 or positional[0] != "s3":
        return 2
    verb, args = positional[1], positional[2:]
    if verb == "cp":
        src, dst = args
        if src.startswith("s3://"):
            if not _path(src).is_file():
                print(f"download failed: {src} does not exist", file=sys.stderr)
                return 1
            shutil.copyfile(_path(src), dst)
        else:
            _path(dst).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, _path(dst))
        return 0
    if verb == "ls":
        directory = _path(args[0])
        if not directory.is_dir():
            return 1
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                print(f"{'PRE':>31} {entry.name}/")
            else:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(entry.stat().st_mtime))
                print(f"{stamp} {entry.stat().st_size:>10} {entry.name}")
        return 0
    if verb == "rm" and recursive:
        shutil.rmtree(_path(args[0]), ignore_errors=True)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
