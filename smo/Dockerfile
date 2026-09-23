# One Dockerfile for all fourteen services — they share one stack
# (Python 3.11 + FastAPI + SQLAlchemy), so a per-service Dockerfile would
# just be fourteen near-identical copies. MODULE picks which service's
# app/ directory gets copied in and run.
FROM python:3.11-slim

ARG MODULE
ENV MODULE=${MODULE}

WORKDIR /srv

COPY shared /srv/shared
RUN pip install --no-cache-dir -e /srv/shared

COPY ${MODULE}/pyproject.toml* /srv/module/
COPY ${MODULE}/app /srv/module/app
WORKDIR /srv/module
RUN pip install --no-cache-dir fastapi uvicorn[standard] sqlalchemy psycopg[binary] httpx pydantic

ENV PYTHONPATH=/srv/module
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
