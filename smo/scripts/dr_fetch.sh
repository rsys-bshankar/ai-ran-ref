#!/usr/bin/env bash
# Downloads one off-site backup set from the S3-compatible bucket and checks it against its manifest (PR-HA-6.2).
#
#   scripts/dr_fetch.sh [--set YYYYMMDDTHHMMSSZ] OUTDIR
#
#   (default)   the set latest.json names, the newest complete one. --set picks an older one (point-in-time by backup, not by WAL).
#   OUTDIR      created (mode 0700); receives smo.dump, gui-bff.db (when the set has it) and manifest.json.
#
# Each file's size and SHA-256 are compared with the manifest; a mismatch deletes nothing but exits 1 and says which file. The bucket
# variables (SMO_BACKUP_S3_BUCKET, SMO_BACKUP_S3_ENDPOINT, credentials) are in dr_s3.sh. Prints the set name on the last line of stdout.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dr_s3.sh
. "$here/dr_s3.sh"
set_name=""
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --set) set_name="${2:?--set needs a set name}"; shift ;;
    -h|--help) sed -n '2,11p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) out="$1" ;;
  esac
  shift
done
[ -n "$out" ] || { echo "usage: dr_fetch.sh [--set STAMP] OUTDIR" >&2; exit 2; }
smo_s3_init
umask 077
mkdir -p "$out"

if [ -z "$set_name" ]; then
  smo_s3_get latest.json "$out/latest.json"
  set_name="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["set"])' "$out/latest.json")"
  rm -f "$out/latest.json"
fi
smo_s3_get "$set_name/manifest.json" "$out/manifest.json"
python3 - "$out/manifest.json" <<'PY' | while read -r name; do smo_s3_get "$set_name/$name" "$out/$name"; done
import json, sys
for f in json.load(open(sys.argv[1]))["files"]:
    print(f["name"])
PY
python3 - "$out" <<'PY'
import hashlib, json, os, sys
out = sys.argv[1]
bad = []
for f in json.load(open(os.path.join(out, "manifest.json")))["files"]:
    path = os.path.join(out, f["name"])
    if not os.path.exists(path) or os.path.getsize(path) != f["bytes"]:
        bad.append(f"{f['name']}: missing or the wrong size")
        continue
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != f["sha256"]:
        bad.append(f"{f['name']}: SHA-256 differs from the manifest")
if bad:
    sys.exit("backup set failed its manifest check:\n  " + "\n  ".join(bad))
PY
echo "$set_name"
