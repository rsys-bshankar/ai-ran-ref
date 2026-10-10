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
    """The version of a release tag (`smo-v1.2.3` or `smo-v1.2.3-rc.1` gives `1.2.3` / `1.2.3-rc.1`); raises `ValueError` for any other tag name."""
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
    """The body of the `## [<version>]` section of a Keep-a-Changelog file, up to the next `## [` heading or the first link-reference line; "" when the version has no section."""
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: )", changelog, re.M | re.S)
    return match.group(1).strip() if match else ""


def render(tag: str, section: str, subjects: list[str], previous: str | None, repo: str = "") -> str:
    """The release notes markdown: title, the CHANGELOG `section` (when there is one), the merged commit subjects since `previous`, and, when `repo` is known, the image-name and signature note.

        Pure: takes strings, returns one string ending in a newline.
    """
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
    """Runs `git <args>` in the SMO root and returns its standard output; raises `CalledProcessError` on a non-zero exit."""
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, cwd=SMO_ROOT).stdout


def main(argv: list[str]) -> int:
    """Prints the release notes for the tag in `argv[1]` (exit 2 and the usage text when the argument count is wrong).

        The previous tag is the newest `smo-v*` tag that sorts before it (a release candidate sorts before its final release); the subjects are the first-parent commits in
        that range, or the whole history up to the tag for the first release. The repository name for the images line comes from `remote.origin.url` and is left out when
        there is no remote. A tag that does not exist makes `git log` fail and the script raise.
    """
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
