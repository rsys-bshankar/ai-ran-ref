"""The BFF's own small store: GUI users, the audit log, and the BFF's SME
invoker credential.

Deliberately not in the SMO's shared Postgres schema (migrations/001_init.sql):
none of this is SMO domain data, and the BFF must keep working (login,
audit) even while the SMO stack itself is down. SQLite on a compose volume
by default; any SQLAlchemy URL works via GUI_DATABASE_URL.
"""

import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class Base(DeclarativeBase):
    pass


class GuiUser(Base):
    __tablename__ = "gui_user"

    username: Mapped[str] = mapped_column(String, primary_key=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Bumped on password change / deactivation: every session JWT carries
    # the version it was issued under, so old sessions stop working at once.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class AuditEntry(Base):
    """Append-only: nothing in the BFF updates or deletes a row, and the
    ORM guard below refuses it if anything ever tries.
    """
    __tablename__ = "gui_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    username: Mapped[str | None] = mapped_column(String)
    role: Mapped[str | None] = mapped_column(String)
    action: Mapped[str] = mapped_column(String, nullable=False)       # PROXY / DENIED / LOGIN / LOGIN_FAILED / USER_* ...
    method: Mapped[str | None] = mapped_column(String)
    path: Mapped[str | None] = mapped_column(String)
    status_code: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(String)


@event.listens_for(Session, "before_flush")
def _audit_log_is_append_only(session, flush_context, instances):
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, AuditEntry):
            raise PermissionError("gui_audit_log is append-only")


class SmoCredential(Base):
    """The BFF's own CAPIF invoker identity at SME (one row). Kept so a BFF
    restart reuses its invoker instead of onboarding a new one each boot.
    The onboarding secret is stored as issued: the BFF has to present it to
    SME's token endpoint, so it can't be hashed here the way SME hashes it.
    """
    __tablename__ = "gui_smo_credential"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_invoker_id: Mapped[str] = mapped_column(String, nullable=False)
    onboarding_secret: Mapped[str] = mapped_column(String, nullable=False)


class Database:
    def __init__(self, url: str):
        kwargs: dict = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url in ("sqlite://", "sqlite:///:memory:"):
                kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False, future=True)
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self.sessions()
