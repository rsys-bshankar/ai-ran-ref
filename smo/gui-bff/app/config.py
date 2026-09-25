"""GUI BFF settings — all from the environment, nothing secret in git.

GUI_JWT_SECRET / GUI_ADMIN_PASSWORD may be left unset for a throwaway demo:
the BFF then generates a random value at boot. A generated admin password is
written to GUI_INITIAL_PASSWORD_FILE (mode 0600), never logged; the log only
says where it is. A real deployment sets both.
"""

import os
import secrets
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    r1_url: str = field(default_factory=lambda: os.environ.get("R1_URL", "http://r1-termination:8000").rstrip("/"))
    # Optional override. By default the SME token endpoint is discovered the
    # same way an rApp discovers it: from R1 Termination's own /bootstrap.
    sme_url: str | None = field(default_factory=lambda: (os.environ.get("SME_URL") or "").rstrip("/") or None)
    database_url: str = field(default_factory=lambda: os.environ.get("GUI_DATABASE_URL", "sqlite:///./gui-bff.db"))
    jwt_secret: str = field(default_factory=lambda: os.environ.get("GUI_JWT_SECRET", ""))
    session_ttl_seconds: int = field(default_factory=lambda: int(os.environ.get("GUI_SESSION_TTL_SECONDS", str(8 * 3600))))
    # Secure cookies are the default. Browsers already treat http://localhost as
    # a secure context, so this only needs turning off for plain-http access by
    # a non-localhost hostname (e.g. a lab VM reached by IP).
    cookie_secure: bool = field(default_factory=lambda: _bool("GUI_COOKIE_SECURE", True))
    admin_password: str = field(default_factory=lambda: os.environ.get("GUI_ADMIN_PASSWORD", ""))
    # Where a generated admin password is written (mode 0600) when
    # GUI_ADMIN_PASSWORD is unset — never to the log.
    initial_password_file: str = field(default_factory=lambda: os.environ.get("GUI_INITIAL_PASSWORD_FILE", "./initial-admin-password"))
    operator_password: str = field(default_factory=lambda: os.environ.get("GUI_OPERATOR_PASSWORD", ""))
    viewer_password: str = field(default_factory=lambda: os.environ.get("GUI_VIEWER_PASSWORD", ""))
    health_timeout_seconds: float = field(default_factory=lambda: float(os.environ.get("GUI_HEALTH_TIMEOUT_SECONDS", "3")))
    upstream_timeout_seconds: float = field(default_factory=lambda: float(os.environ.get("GUI_UPSTREAM_TIMEOUT_SECONDS", "30")))
    jwt_secret_generated: bool = False

    def __post_init__(self) -> None:
        if not self.jwt_secret:
            self.jwt_secret = secrets.token_urlsafe(48)
            self.jwt_secret_generated = True


settings = Settings()
