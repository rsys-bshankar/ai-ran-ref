#!/usr/bin/env bash
# Pins the third-party jars of the Java SDK by SHA-256.
#
#   scripts/verify-dependencies.sh           # check: the jars Maven resolves match dependencies.sha256 (CI runs this)
#   scripts/verify-dependencies.sh --write   # rewrite dependencies.sha256 after a deliberate version change
#
# Maven has no lock file. The versions are fixed in pom.xml (no ranges, no SNAPSHOTs; the enforcer rule requirePluginVersions
# covers plugins); this adds the byte-level pin: every jar of the test classpath, by file name and hash, so a changed or
# substituted artifact fails the build even at an unchanged version. io.smo:* (this build's own modules) are not listed.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
cd "$here"
mvn_cmd="${MVN:-./mvnw}"
[ -x "$mvn_cmd" ] || mvn_cmd=mvn

out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

# -C: fail, not warn, when a checksum Maven Central publishes for a download does not match
"$mvn_cmd" -B -ntp -q -C -pl smo-sdk dependency:copy-dependencies -DincludeScope=test -DoutputDirectory="$out" >/dev/null

actual="$(cd "$out" && sha256sum -- *.jar | LC_ALL=C sort -k2)"

if [ "${1:-}" = "--write" ]; then
  printf '%s\n' "$actual" > dependencies.sha256
  echo "wrote dependencies.sha256 ($(wc -l < dependencies.sha256) jars)"
  exit 0
fi

if diff -u dependencies.sha256 <(printf '%s\n' "$actual"); then
  echo "dependencies.sha256: $(wc -l < dependencies.sha256) jars match"
else
  echo "the resolved jars differ from dependencies.sha256 (a version change is deliberate: run scripts/verify-dependencies.sh --write and review the diff)" >&2
  exit 1
fi
