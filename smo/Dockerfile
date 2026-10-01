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

ENV PYTHONPATH=/srv/module:/srv/sdk
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
