#!/usr/bin/env python3
"""Release notes for a tag (PR-OPS-4.3): the CHANGELOG section plus the merged PR titles since the previous tag.

    python scripts/release_notes.py smo-v0.1.0 > notes.md

Run from a checkout that has the tags. Squash-merged PR titles end in `(#123)`, which GitHub renders as a link.
"""

import re
import subprocess
import sys
from pathlib import Path

SMO_ROOT = Path(__file__).resolve().parent.parent
TAG = re.compile(r"^smo-v(\d+)\.(\d+)\.(\d+)(-rc\.(\d+))?$")


def version_of(tag: str) -> str:
    if not TAG.match(tag):
        raise ValueError(f"not a release tag: {tag!r}")
    return tag.removeprefix("smo-v")


def _key(tag: str) -> tuple:
    major, minor, patch, _, rc = TAG.match(tag).groups()
    # a release candidate sorts before its final release
    return int(major), int(minor), int(patch), 1 if rc is None else 0, int(rc or 0)


def previous_tag(tags: list[str], tag: str) -> str | None:
    """The newest release tag older than `tag`, or None for the first release."""
    older = [t for t in tags if TAG.match(t) and _key(t) < _key(tag)]
    return max(older, key=_key) if older else None


def changelog_section(changelog: str, version: str) -> str:
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: )", changelog, re.M | re.S)
    return match.group(1).strip() if match else ""


def render(tag: str, section: str, subjects: list[str], previous: str | None, repo: str = "") -> str:
    out = [f"# SMO {version_of(tag)}", ""]
    out += [section, ""] if section else []
    out += ["## Merged since " + (previous or "the beginning"), ""]
    out += [f"- {s}" for s in subjects] or ["- (nothing)"]
    if repo:
        out += ["", "## Images", "",
                f"`ghcr.io/{repo}/smo-<module>:{version_of(tag)}`, signed with cosign (keyless) and carrying build provenance; "
                "the verification command is in `docs/RELEASES.md`."]
    return "\n".join(out) + "\n"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, cwd=SMO_ROOT).stdout


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    tag = argv[1]
    previous = previous_tag(_git("tag", "--list", "smo-v*").split(), tag)
    span = f"{previous}..{tag}" if previous else tag
    subjects = [s for s in _git("log", "--first-parent", "--pretty=format:%s", span).splitlines() if s]
    repo = ""
    try:
        repo = _git("config", "--get", "remote.origin.url").strip().removesuffix(".git").split("github.com")[-1].strip(":/")
    except subprocess.CalledProcessError:
        pass
    print(render(tag, changelog_section((SMO_ROOT / "CHANGELOG.md").read_text(), version_of(tag)), subjects, previous,
                 repo.lower()), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
