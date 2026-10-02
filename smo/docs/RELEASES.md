# Releases

How the SMO is versioned and released (`PR-OPS-4.1`). The release history is `CHANGELOG.md`.

## Tag scheme

Semantic Versioning 2.0.0, with a **`smo-v`** prefix because this repository also holds the specification material outside `smo/`:

    smo-v<MAJOR>.<MINOR>.<PATCH>            smo-v0.1.0, smo-v1.4.2
    smo-v<MAJOR>.<MINOR>.<PATCH>-rc.<N>     release candidates, smo-v1.0.0-rc.1

One version for the whole platform: every module image, the shared library, the SDK and the Alembic history are released together.
Until `1.0.0`, `0.MINOR` is the breaking-change number and `0.x.PATCH` is for fixes.

## What each number means

| Bump | When |
|---|---|
| **MAJOR** | An incompatible change to an interface an operator or an rApp depends on: an R1 route, request or response field removed or changed in meaning; an rApp package (CSAR) manifest change old packages fail; a configuration variable removed or repurposed; a schema revision that cannot be applied while the previous release still runs, or has no supported upgrade path from the previous MAJOR's last release |
| **MINOR** | Backwards-compatible addition: a new route, field, module, configuration variable (with a default that keeps the old behaviour) or an additive schema revision |
| **PATCH** | A fix that changes none of the above: a bug, a security fix, a dependency bump, documentation |

A schema revision is additive when the previous release's code runs on the new schema (a new nullable column, a new table, a new index). Anything else needs
the MAJOR bump, or an expand/contract split across two MINOR releases (`PR-OPS-5`).

## Cutting a release

1. Everything for the release is merged to `main` and CI is green on it.
2. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, start a fresh `## [Unreleased]` above it, and update the compare links at the foot. Merge that as its own PR (`Release X.Y.Z`).
3. Tag the merge commit: `git tag -a smo-vX.Y.Z -m "SMO X.Y.Z" <sha> && git push origin smo-vX.Y.Z`. Tags are never moved or deleted; a bad release is superseded by the next PATCH.
4. The tag is the release. Building and publishing images by tag and digest (`OPS-4.2`), generated release notes (`OPS-4.3`) and the supported-versions table in `SECURITY.md` (`OPS-4.4`) follow in their own changes.

A person cuts the tag. It is not something an automated change does on its own.

## The first tag

`smo-v0.1.0` is proposed for the point at which the production-readiness slices recorded under `CHANGELOG.md`'s first section are in `main`. It has not been cut.
