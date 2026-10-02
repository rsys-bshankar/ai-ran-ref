# One Dockerfile for all sixteen services — they share one stack
# (Python + FastAPI + SQLAlchemy), so a per-service Dockerfile would
# just be fourteen near-identical copies. MODULE picks which service's
# app/ directory gets copied in and run.
FROM python:3.14-slim@sha256:0741d101873c12ab927e6f8653feb8862b9bd58771177acb1b885b95141f91b4

ARG MODULE
ENV MODULE=${MODULE}

WORKDIR /srv

# Third-party packages come from the hashed, pinned lock (requirements/); the
# shared library itself is installed without re-resolving them.
COPY requirements/runtime.txt /srv/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /srv/requirements.txt

COPY shared /srv/shared
RUN pip install --no-cache-dir --no-deps -e /srv/shared
# The AI Runtime SDK (smo_sdk) — what a sample rApp (MODULE=samples/...)
# builds on; the SMO modules themselves never import it.
COPY sdk /srv/sdk

COPY ${MODULE}/pyproject.toml* /srv/module/
COPY ${MODULE}/app /srv/module/app
WORKDIR /srv/module

# Run as an unprivileged user (PR-SEC-13.1). The code under /srv is root-owned and read-only to it; the only
# places a service writes are the two directories below, which compose mounts volumes on (a new named volume
# takes its ownership from the image, so the user can write to it): /data (the GUI BFF's database and
# first-run admin password) and /srv/packages (Onboarding's package store). A volume created by an earlier,
# root-run stack stays root-owned: recreate it (`docker compose down -v`, which discards demo data).
RUN groupadd --system --gid 10001 smo \
 && useradd --system --uid 10001 --gid smo --no-create-home --shell /usr/sbin/nologin smo \
 && mkdir -p /data /srv/packages \
 && chown smo:smo /data /srv/packages
USER 10001:10001

ENV PYTHONPATH=/srv/module:/srv/sdk
EXPOSE 8000

# Worker processes per container, and how long a stopping container keeps finishing the requests it already
# accepted (PR-ST-6). On SIGTERM uvicorn stops accepting connections, lets in-flight requests complete, and
# exits; anything still running after UVICORN_GRACEFUL_SHUTDOWN_SECONDS is cut. docker-compose.yml gives every
# service a stop_grace_period longer than that, so docker's own SIGKILL never lands first. Workers hold no
# shared state (docs/ARCHITECTURE.md, Process state and scale-out), so a service may raise UVICORN_WORKERS; the
# two mock services keep their state in memory and must stay at 1. `exec` makes uvicorn PID 1, so it receives
# the stop signal itself. `--no-access-log` turns off uvicorn's plain-text access line: smo_shared/logconfig.py logs
# one structured line per request instead (PR-OBS-1).
ENV UVICORN_WORKERS=1 \
    UVICORN_GRACEFUL_SHUTDOWN_SECONDS=20
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS} --timeout-graceful-shutdown ${UVICORN_GRACEFUL_SHUTDOWN_SECONDS} --no-access-log"]
