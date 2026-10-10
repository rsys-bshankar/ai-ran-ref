"""CHANGELOG.md and docs/RELEASES.md stay well-formed (PR-OPS-4.1): an Unreleased section, semver headings, newest first, links."""

import re
from pathlib import Path

SMO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = (SMO_ROOT / "CHANGELOG.md").read_text()
SEMVER = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-rc\.\d+)?"


def _released() -> list[tuple[int, ...]]:
    return [tuple(int(x) for x in m.group(1).split("-")[0].split("."))
            for m in re.finditer(rf"^## \[({SEMVER})\] - \d{{4}}-\d{{2}}-\d{{2}}$", CHANGELOG, re.M)]


def test_the_changelog_starts_with_an_unreleased_section():
    """The first section of the CHANGELOG is `Unreleased`."""
    headings = re.findall(r"^## \[(.+?)\]", CHANGELOG, re.M)
    assert headings and headings[0] == "Unreleased"


def test_every_other_section_is_a_semver_release_with_a_date_newest_first():
    """Every other section is a dated `X.Y.Z` release heading and the releases are newest first."""
    headings = re.findall(r"^## \[(.+?)\].*$", CHANGELOG, re.M)[1:]
    released = _released()
    assert len(released) == len(headings), f"a section heading is not '## [X.Y.Z] - YYYY-MM-DD': {headings}"
    assert released == sorted(released, reverse=True), "releases must be newest first"


def test_each_section_has_a_compare_link_at_the_foot():
    """Every section heading has a link reference at the foot of the file."""
    headings = re.findall(r"^## \[(.+?)\]", CHANGELOG, re.M)
    for heading in headings:
        assert re.search(rf"^\[{re.escape(heading)}\]: https://", CHANGELOG, re.M), f"no link for [{heading}]"


def test_the_tag_scheme_names_the_prefix_the_release_doc_uses():
    """docs/RELEASES.md names the `smo-v<MAJOR>.<MINOR>.<PATCH>` tag scheme and the CHANGELOG has its Unreleased heading."""
    releases = (SMO_ROOT / "docs" / "RELEASES.md").read_text()
    assert "smo-v<MAJOR>.<MINOR>.<PATCH>" in releases
    assert re.search(r"^## \[Unreleased\]", CHANGELOG, re.M)
