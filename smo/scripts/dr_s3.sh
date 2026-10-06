# Sourced by dr_backup.sh, dr_fetch.sh and dr_drill.sh (PR-HA-6): the off-site store, an S3-compatible bucket (AWS S3, MinIO, Ceph, ...).
#
#   SMO_BACKUP_S3_BUCKET         required. The bucket; credentials are the AWS CLI's own (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, a profile, or the
#                                instance or pod role).
#   SMO_BACKUP_S3_PREFIX         key prefix inside the bucket (default: smo).
#   SMO_BACKUP_S3_ENDPOINT       endpoint URL for a store that is not AWS (MinIO: http://minio:9000); empty for AWS.
#   SMO_BACKUP_S3_SSE            server-side encryption sent with each upload: AES256 or aws:kms (default: none; the bucket's default applies).
#   SMO_BACKUP_AWS               the AWS CLI binary (default: aws).
#
# Layout:  s3://BUCKET/PREFIX/<UTC yyyymmddThhmmssZ>/{smo.dump,gui-bff.db,manifest.json}   one directory ("set") per backup
#          s3://BUCKET/PREFIX/latest.json                                                  a copy of the newest set's manifest, written last
smo_s3_init() {
  : "${SMO_BACKUP_S3_BUCKET:?SMO_BACKUP_S3_BUCKET is not set (the bucket the backups go to)}"
  S3_PREFIX="${SMO_BACKUP_S3_PREFIX:-smo}"
  S3_PREFIX="${S3_PREFIX#/}"; S3_PREFIX="${S3_PREFIX%/}"
  S3_ROOT="s3://${SMO_BACKUP_S3_BUCKET}/${S3_PREFIX}"
  s3_args=()
  [ -z "${SMO_BACKUP_S3_ENDPOINT:-}" ] || s3_args+=(--endpoint-url "$SMO_BACKUP_S3_ENDPOINT")
  AWS_BIN="${SMO_BACKUP_AWS:-aws}"
  command -v "$AWS_BIN" > /dev/null || { echo "the AWS CLI ($AWS_BIN) is not installed" >&2; return 2; }
}
smo_s3() { "$AWS_BIN" ${s3_args[@]+"${s3_args[@]}"} "$@"; }
# upload FILE KEY (relative to the prefix)
smo_s3_put() {
  local sse=()
  [ -z "${SMO_BACKUP_S3_SSE:-}" ] || sse=(--sse "$SMO_BACKUP_S3_SSE")
  smo_s3 s3 cp --only-show-errors ${sse[@]+"${sse[@]}"} "$1" "$S3_ROOT/$2"
}
# download KEY FILE
smo_s3_get() { smo_s3 s3 cp --only-show-errors "$S3_ROOT/$1" "$2"; }
# the set directories, oldest first, one UTC stamp per line
smo_s3_sets() {
  smo_s3 s3 ls "$S3_ROOT/" | sed -n 's|^ *PRE \([0-9]\{8\}T[0-9]\{6\}Z\)/$|\1|p' | sort
}
smo_s3_rm_set() { smo_s3 s3 rm --only-show-errors --recursive "$S3_ROOT/$1/"; }
