# PgBouncer for the compose profile `pooler` (PR-DB-5.1). Alpine's package, run as the unprivileged user it creates.
FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8
RUN apk add --no-cache pgbouncer
COPY entrypoint.sh /entrypoint.sh
USER pgbouncer
EXPOSE 6432
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
