import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# All fourteen modules share ONE Postgres instance, partitioned by moduleScope
# per Requirements v0.1 section 3 — not one DB per module. Each module's models set
# module_scope on write; queries are expected to filter by it explicitly rather
# than relying on schema-level isolation, matching the pattern already used for
# SME/DME/Onboarding/rApp Mgmt in the HLD.
DATABASE_URL = os.environ.get(
    "SMO_DATABASE_URL", "postgresql+psycopg://smo:smo@postgres:5432/smo"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope() -> Session:
    """Provide a transactional scope for a single unit of work."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session():
    """FastAPI dependency — yields a session, always closes it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
