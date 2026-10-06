# The off-site backup job for the compose profile `backup` (PR-HA-6.2): the Postgres 18 client tools (the same image as the database, so
# pg_dump is never older than the server), the AWS CLI for the S3-compatible bucket, bash and python3 for scripts/dr_backup.sh (mounted, not copied,
# so the script the repository has is the script that runs). Runs as the GUI backend's uid so it can read the SQLite file on volume gui_bff_data.
FROM postgres:18-alpine@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873
RUN apk add --no-cache aws-cli bash python3 \
 && mkdir /staging && chown 10001:10001 /staging
USER 10001:10001
ENTRYPOINT []
