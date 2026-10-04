"""Release tooling stays in step with the repository (PR-OPS-4.2/4.3/4.4)."""

import importlib.util
import re
from pathlib import Path

import pytest

SMO_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SMO_ROOT.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SMO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_images = _load("release_images")
release_notes = _load("release_notes")


def test_every_compose_build_is_published_once_and_names_are_unique():
    listed = release_images.images()
    names = [i["name"] for i in listed]
    assert len(names) == len(set(names)), "two builds would push the same image name"
    assert {"r1-termination", "ran-nf-oam", "gui", "energy-saving-rapp"} <= set(names)
    assert "migrate" not in names, "migrate reuses the r1-termination build"


def test_the_release_workflow_takes_its_image_list_from_compose_and_pushes_nothing_unsigned():
    text = (REPO_ROOT / ".github" / "workflows" / "release-images.yml").read_text()
    assert "release_images.py matrix" in text
    assert re.search(r"^\s+tags: \[\"smo-v\*\"\]", text, re.M)
    assert "cosign" in text and "--provenance" in text and "id-token: write" in text
    assert "--driver docker-container" in text, "provenance and SBOM attestations are not supported by the default docker driver"


def test_previous_tag_skips_release_candidates_of_later_versions_and_unrelated_tags():
    tags = ["smo-v0.1.0", "smo-v0.2.0-rc.1", "smo-v0.2.0", "smo-v0.10.0", "other", "smo-v0.9.1"]
    assert release_notes.previous_tag(tags, "smo-v0.1.0") is None
    assert release_notes.previous_tag(tags, "smo-v0.2.0") == "smo-v0.2.0-rc.1"
    assert release_notes.previous_tag(tags, "smo-v0.10.0") == "smo-v0.9.1"


def test_a_tag_that_is_not_a_release_is_refused():
    with pytest.raises(ValueError):
        release_notes.version_of("v1.0.0")


def test_notes_carry_the_changelog_section_and_the_merged_titles():
    changelog = "## [Unreleased]\n\n## [0.1.0] - 2026-10-03\n\nFirst.\n\n### Added\n- thing\n\n[Unreleased]: x\n"
    section = release_notes.changelog_section(changelog, "0.1.0")
    assert section.startswith("First.") and "- thing" in section and "Unreleased" not in section
    notes = release_notes.render("smo-v0.1.0", section, ["Add thing (#1)"], None, "o/r")
    assert "# SMO 0.1.0" in notes and "- Add thing (#1)" in notes and "ghcr.io/o/r/smo-<module>:0.1.0" in notes


def test_the_supported_versions_in_security_md_are_released_versions():
    security = (REPO_ROOT / "SECURITY.md").read_text()
    changelog = (SMO_ROOT / "CHANGELOG.md").read_text()
    released = set(re.findall(r"^## \[(\d+\.\d+\.\d+(?:-rc\.\d+)?)\]", changelog, re.M))
    table = security.split("## Supported versions", 1)[1].split("## ", 1)[0]
    for line in table.splitlines():
        match = re.match(r"\| `?(\d+\.\d+)\.x`? \|", line)
        if match:
            minor = match.group(1)
            assert any(v.startswith(minor + ".") for v in released), f"SECURITY.md lists {minor}.x, which has no release"


def test_the_release_workflow_builds_from_the_tag_but_takes_its_tooling_from_its_own_commit():
    """A tag cut before the tooling existed (smo-v0.1.0) must still be publishable: only the image builds check out the tag."""
    text = (REPO_ROOT / ".github" / "workflows" / "release-images.yml").read_text()
    jobs = text.split("\njobs:\n", 1)[1]
    plan, image, notes = (jobs.split("\n  image:\n")[0], jobs.split("\n  image:\n")[1].split("\n  notes:\n")[0], jobs.split("\n  notes:\n")[1])
    assert "refs/tags/" not in plan.replace("refs/tags/$TAG", "") and "fetch-tags: true" in plan
    assert "ref: refs/tags/${{ env.TAG }}" in image
    assert "ref: refs/tags/" not in notes and "fetch-tags: true" in notes
