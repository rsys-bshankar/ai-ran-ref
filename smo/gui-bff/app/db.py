"""The BFF's own small store: GUI users, the audit log, and the BFF's SME
invoker credential.

Deliberately not in the SMO's shared Postgres schema (migrations/001_init.sql):
none of this is SMO domain data, and the BFF must keep working (login,
audit) even while the SMO stack itself is down. SQLite on a compose volume
by default; any SQLAlchemy URL works via GUI_DATABASE_URL.
"""

import datetime
import time

from sqlalchemy import Boolean, DateTime, Float, Integer, String, create_engine, delete, event, update
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
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


class GuiSetting(Base):
    """A value every instance of this database must agree on (PR-ST-5), kept in the database rather than
    in a process: today the session signing key when GUI_JWT_SECRET is not set."""
    __tablename__ = "gui_setting"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class LoginFailure(Base):
    """Failed-login counter per username (PR-ST-5), so every instance sharing this database counts the same
    failures. Keyed by the name as typed, not by user row: an unknown name is counted and locked exactly like
    a real one, so the lockout does not reveal which usernames exist."""
    __tablename__ = "gui_login_failure"

    username: Mapped[str] = mapped_column(String, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_failed_at: Mapped[float] = mapped_column(Float, nullable=False)   # unix seconds: start of the counting window


class RevokedSession(Base):
    """A session its owner ended with `POST /api/logout`: its token id (`jti`) until the token would have expired anyway. Shared by every instance, so a
    copied cookie or token stops working on all of them at once, not at the end of its time to live."""
    __tablename__ = "gui_revoked_session"

    jti: Mapped[str] = mapped_column(String, primary_key=True)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False, index=True)   # unix seconds: the token's `exp`; the row is useless after it


class Database:
    def __init__(self, url: str):
        kwargs: dict = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url in ("sqlite://", "sqlite:///:memory:"):
                kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False, future=True)
        # Several instances starting on one shared database each run this: check-then-create is not atomic,
        # so a loser of the race can see "already exists". It has only lost, so look again (PR-ST-5).
        for attempt in range(4):
            try:
                Base.metadata.create_all(self.engine)
                break
            except (OperationalError, ProgrammingError):
                if attempt == 3:
                    raise
                time.sleep(0.25)

    def session(self) -> Session:
        return self.sessions()

    # ------------------------------------------------------------ state shared by every instance (PR-ST-5)

    def shared_setting(self, key: str, default: str) -> str:
        """The stored value of `key`; the first caller to find none stores `default`, and every other
        instance (or a restart) gets that same value, whichever of them asked first."""
        for _ in range(2):
            with self.session() as s:
                row = s.get(GuiSetting, key)
                if row is not None:
                    return row.value
                s.add(GuiSetting(key=key, value=default))
                try:
                    s.commit()
                    return default
                except IntegrityError:   # another instance stored its value first: read that one
                    s.rollback()
        raise RuntimeError(f"could not store or read the setting {key!r}")

    def login_locked(self, username: str, max_failures: int, window_seconds: float, now: float) -> bool:
        with self.session() as s:
            row = s.get(LoginFailure, username)
            return row is not None and row.count >= max_failures and now - row.first_failed_at < window_seconds

    def record_login_failure(self, username: str, window_seconds: float, now: float) -> None:
        """Count one failure, atomically in SQL so concurrent instances never lose one: a counter whose window
        has passed restarts at 1, a live one is incremented, and a name with none gets a first row."""
        for _ in range(3):
            with self.session() as s:
                restarted = s.execute(update(LoginFailure).where(
                    LoginFailure.username == username, LoginFailure.first_failed_at <= now - window_seconds)
                    .values(count=1, first_failed_at=now))
                if restarted.rowcount == 1:
                    s.commit()
                    return
                counted = s.execute(update(LoginFailure).where(
                    LoginFailure.username == username, LoginFailure.first_failed_at > now - window_seconds)
                    .values(count=LoginFailure.count + 1))
                if counted.rowcount == 1:
                    s.commit()
                    return
                s.add(LoginFailure(username=username, count=1, first_failed_at=now))
                try:
                    s.commit()
                    return
                except IntegrityError:   # another instance added the first row at the same moment: count into it
                    s.rollback()
        raise RuntimeError(f"could not record the failed login for {username!r}")

    def revoke_session(self, jti: str, expires_at: float, now: float) -> None:
        """Record the end of one session (idempotent), and forget the ones whose tokens have expired by themselves."""
        with self.session() as s:
            s.execute(delete(RevokedSession).where(RevokedSession.expires_at <= now))
            if s.get(RevokedSession, jti) is None:
                s.add(RevokedSession(jti=jti, expires_at=expires_at))
            try:
                s.commit()
            except IntegrityError:    # another instance recorded the same logout at the same moment
                s.rollback()

    def session_revoked(self, jti: str) -> bool:
        with self.session() as s:
            return s.get(RevokedSession, jti) is not None

    def clear_login_failures(self, username: str) -> None:
        with self.session() as s:
            s.execute(delete(LoginFailure).where(LoginFailure.username == username))
            s.commit()

    def store_smo_credential(self, api_invoker_id: str, secret: str, stale_invoker_id: str | None = None) -> bool:
        """Store the BFF's SME invoker identity. With no `stale_invoker_id` this is a first registration: it
        stores only if none exists. With one, it replaces that identity only if it is still the stored one
        (compare-and-swap). False means another instance got there first; use `SmoCredential` as stored."""
        with self.session() as s:
            if stale_invoker_id is None:
                s.add(SmoCredential(id=1, api_invoker_id=api_invoker_id, onboarding_secret=secret))
                try:
                    s.commit()
                    return True
                except IntegrityError:
                    s.rollback()
                    return False
            done = s.execute(update(SmoCredential).where(SmoCredential.id == 1, SmoCredential.api_invoker_id == stale_invoker_id)
                             .values(api_invoker_id=api_invoker_id, onboarding_secret=secret))
            s.commit()
            return done.rowcount == 1

