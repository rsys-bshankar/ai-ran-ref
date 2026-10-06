"""SMO Operator GUI — Backend-for-Frontend.

The only API the browser ever talks to. It holds GUI users and roles, issues
the session JWT, checks every call against the RBAC table (rbac.py), and
forwards allowed calls to R1 Termination with the BFF's own SME-issued
OAuth2 token (smo_client.py). The browser never calls R1 or any module port
directly: that would need CORS on every module and bypass the role checks.

Routes (all under /api, which nginx forwards here unchanged):
  POST /api/login               username/password -> httpOnly session cookie
  POST /api/token               OAuth2 password grant -> Bearer JWT (scripts/CLI)
  POST /api/logout
  GET  /api/me                  current user, role, CSRF token
  POST /api/me/password
  GET  /api/permissions         the RBAC table, so the SPA gates on the same rules
  GET  /api/modules/status      every module's health, readiness and build version via R1, probed in parallel
  *    /api/smo/{module}/...    RBAC-checked proxy to R1 Termination
  /api/admin/users[...]         user + role CRUD (admin)
  GET  /api/admin/audit         the append-only audit log (admin)
"""

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, cast

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from .config import Settings, settings as default_settings
from .db import AuditEntry, Database, GuiUser, LoginFailure
from .rbac import MODULES, RULES, Role, Rule, User, decide
from .security import decode_jwt, hash_password, issue_jwt, verify_password
from .smo_client import R1Gateway, SmoAuthError

log = logging.getLogger("smo-gui-bff")

SESSION_COOKIE = "smo_session"
CSRF_COOKIE = "smo_csrf"
CSRF_HEADER = "x-csrf-token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

MAX_LOGIN_FAILURES = 5
LOCKOUT_SECONDS = 300
MIN_PASSWORD_LENGTH = 8
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,31}$")

# RFC 7230 section 6.1 hop-by-hop headers, plus headers the BFF must own
# itself: lengths/encodings are recomputed (httpx has already decoded the
# body), and neither the browser's GUI credentials nor an upstream
# Set-Cookie may ever cross the proxy. The same lesson R1 Termination's own
# proxy learned with Host.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
              "trailers", "transfer-encoding", "upgrade"}
_NEVER_FORWARD_RESPONSE = HOP_BY_HOP | {"content-length", "content-encoding", "set-cookie", "server", "date"}
_FORWARD_REQUEST = {"content-type", "accept"}

# Everything the BFF itself serves is JSON: nothing may frame, sniff or run it.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

# Display order of the health grid: R1 first (every other probe goes through it).
STATUS_MODULES = ["r1-termination", *MODULES]

# Wave 3 (cross-cutting standardization) — Pagination. Every SMO backend
# module shares shared/smo_shared/pagination.py's paginate() for this same
# {items, total, limit, offset} shape; gui-bff can't import it — its own
# CI job (.github/workflows/smo-tests.yml's "Operator GUI BFF tests")
# deliberately never installs smo_shared, unlike every backend module's
# job, so gui-bff keeps its own small local conventions instead (same
# reason it already has its own _problem()/_problem_exception() rather
# than smo_shared.errors). One route here needs it, so it's inlined
# rather than requiring smo_shared just for this.
class _PageSize(int):
    """`limit` that also remembers whether `?total=false` asked to skip the count (smo_shared.pagination.PageSize, kept local for the reason above)."""

    with_total: bool = True

    def __new__(cls, limit: int, with_total: bool = True):
        self = super().__new__(cls, limit)
        self.with_total = with_total
        return self


def _page_limit(limit: int = Query(100, ge=1, le=500, description="Max rows to return (1-500)."),
                total: bool = Query(True, description="`false` skips the count of the whole result: the response then has no `total` and "
                                    "a `hasMore` flag instead. Default `true`.")) -> _PageSize:
    return _PageSize(limit, total)


PageLimit: Any = Depends(_page_limit)
PageOffset = Query(0, ge=0, description="Rows to skip before the first one returned.")


def _paginate(db, stmt, limit: int, offset: int) -> dict:
    # `db` is a real sqlalchemy.orm.Session (db.py's own Database.session()) —
    # left untyped here since this module's own `Session` name (below) is a
    # different, unrelated RBAC dataclass.
    n = int(limit)
    if getattr(limit, "with_total", True):
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = db.scalars(stmt.limit(n).offset(offset)).all()
        return {"items": rows, "total": total, "limit": n, "offset": offset}
    rows = db.scalars(stmt.limit(n + 1).offset(offset)).all()
    return {"items": rows[:n], "limit": n, "offset": offset, "hasMore": len(rows) > n}


def _problem_body(status: int, title: str, detail: str | None = None) -> dict:
    body = {"title": title, "status": status}
    if detail:
        body["detail"] = detail
    return body


def _problem(status: int, title: str, detail: str | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=_problem_body(status, title, detail))


def _problem_exception(status: int, title: str, detail: str | None = None) -> HTTPException:
    """Wave 3 (cross-cutting standardization) — Error Schema: the same
    {title, status, detail} shape as _problem() above, for the four spots
    that must `raise` rather than `return` (FastAPI dependencies, which
    resolve to their return value rather than short-circuiting the
    response) — current_session/require_admin below.
    """
    return HTTPException(status_code=status, detail=_problem_body(status, title, detail))


def seed_users(db: Database, cfg: Settings) -> None:
    """First boot only: creates admin (always) plus operator/viewer (when
    their passwords are set). Never touches an existing user table.
    """
    with db.session() as s:
        if s.scalar(select(GuiUser).limit(1)) is not None:
            return
        admin_password = cfg.admin_password
        wrote_password_file = False
        if not admin_password:
            admin_password = secrets.token_urlsafe(12)
            _write_initial_password(cfg.initial_password_file, admin_password)
            wrote_password_file = True
            log.warning("GUI_ADMIN_PASSWORD not set: seeded user 'admin' with a generated password, written to %s "
                        "(mode 0600). Sign in, change it under Admin > Users, then delete the file.",
                        cfg.initial_password_file)
        seeds = [("admin", admin_password, Role.ADMIN), ("operator", cfg.operator_password, Role.OPERATOR),
                 ("viewer", cfg.viewer_password, Role.VIEWER)]
        for username, password, role in seeds:
            if password:
                s.add(GuiUser(username=username, password_hash=hash_password(password), role=role))
        try:
            s.commit()
        except IntegrityError:
            # Another instance seeded the same database a moment earlier (PR-ST-5): its users stand, and the
            # password this instance generated would not match them, so it must not leave its file behind.
            s.rollback()
            if wrote_password_file:
                with suppress(OSError):
                    os.remove(cfg.initial_password_file)
            log.info("another instance seeded the GUI users first; keeping its users")


def _write_initial_password(path: str, password: str) -> None:
    """Owner-only file on the BFF's own volume. The log and stdout never
    carry the password (CodeQL py/clear-text-logging-sensitive-data).
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(password + "\n")
    os.chmod(path, 0o600)   # O_CREAT's mode doesn't apply to an existing file


@dataclass
class Session:
    user: User
    csrf: str | None
    via_cookie: bool


def create_app(cfg: Settings = default_settings, db: Database | None = None, gateway: R1Gateway | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.db is None:
            app.state.db = Database(cfg.database_url)
        seed_users(app.state.db, cfg)
        if cfg.jwt_secret_generated:
            # Not a per-process random value: every instance of this database must sign with the same key.
            cfg.jwt_secret = app.state.db.shared_setting("jwt_secret", cfg.jwt_secret)
            log.warning("GUI_JWT_SECRET not set: using the session signing key stored in the BFF database "
                        "(shared by every instance of this database; set GUI_JWT_SECRET to manage it yourself)")
        if app.state.gateway is None:
            app.state.gateway = R1Gateway(cfg.r1_url, app.state.db, sme_url=cfg.sme_url, timeout=cfg.upstream_timeout_seconds)
        yield
        await app.state.gateway.aclose()

    app = FastAPI(title="SMO Operator GUI BFF", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url="/api/openapi.json")
    app.state.db, app.state.gateway, app.state.cfg = db, gateway, cfg

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    # ------------------------------------------------------------ helpers

    def audit(action: str, user: User | None = None, *, username: str | None = None, method: str | None = None,
              path: str | None = None, status_code: int | None = None, detail: str | None = None) -> None:
        with app.state.db.session() as s:
            s.add(AuditEntry(username=user.username if user else username, role=user.role if user else None,
                             action=action, method=method, path=path, status_code=status_code, detail=detail))
            s.commit()

    def issue_session(user: GuiUser) -> tuple[str, str]:
        csrf = secrets.token_urlsafe(24)
        token = issue_jwt({"sub": user.username, "ver": user.token_version, "csrf": csrf, "jti": secrets.token_urlsafe(16)},
                          cfg.jwt_secret, cfg.session_ttl_seconds)
        return token, csrf

    def set_session_cookies(response: Response, token: str, csrf: str) -> None:
        response.set_cookie(SESSION_COOKIE, token, httponly=True, path="/api", secure=cfg.cookie_secure, samesite="strict", max_age=cfg.session_ttl_seconds)
        # Readable by the SPA on purpose: double-submit CSRF token, echoed back
        # as X-CSRF-Token and checked against the claim inside the session JWT.
        response.set_cookie(CSRF_COOKIE, csrf, httponly=False, path="/", secure=cfg.cookie_secure, samesite="strict", max_age=cfg.session_ttl_seconds)

    def check_credentials(username: str, password: str) -> GuiUser | JSONResponse:
        if app.state.db.login_locked(username, MAX_LOGIN_FAILURES, LOCKOUT_SECONDS, time.time()):
            audit("LOGIN_LOCKED", username=username)
            return _problem(429, "TOO_MANY_ATTEMPTS", "account temporarily locked after repeated failures")
        with app.state.db.session() as s:
            user = s.get(GuiUser, username)
        # Verify against a dummy hash for unknown users, so response time
        # doesn't reveal which usernames exist.
        ok = verify_password(password, user.password_hash if user else _DUMMY_HASH) and user is not None and user.active
        if not ok:
            app.state.db.record_login_failure(username, LOCKOUT_SECONDS, time.time())
            audit("LOGIN_FAILED", username=username)
            return _problem(401, "INVALID_CREDENTIALS")
        app.state.db.clear_login_failures(username)
        return user

    def current_session(request: Request) -> Session:
        auth = request.headers.get("authorization", "")
        via_cookie = not auth.lower().startswith("bearer ")
        token = request.cookies.get(SESSION_COOKIE) if via_cookie else auth[7:].strip()
        claims = decode_jwt(token or "", cfg.jwt_secret)
        if claims is None:
            raise _problem_exception(401, "UNAUTHENTICATED", "not authenticated")
        with app.state.db.session() as s:
            user = s.get(GuiUser, claims.get("sub"))
        if user is None or not user.active or user.token_version != claims.get("ver"):
            raise _problem_exception(401, "SESSION_REVOKED", "session revoked")
        if claims.get("jti") and app.state.db.session_revoked(claims["jti"]):      # ended by its owner (logout)
            raise _problem_exception(401, "SESSION_REVOKED", "session revoked")
        if via_cookie and request.method in UNSAFE_METHODS:
            sent = request.headers.get(CSRF_HEADER, "")
            if not sent or not hmac.compare_digest(sent, str(claims.get("csrf", ""))):
                raise _problem_exception(403, "CSRF_TOKEN_INVALID", "missing or invalid CSRF token")
        # Role always read from the user table, never from the token: a role
        # change or demotion applies on the very next request.
        return Session(user=User(user.username, Role(user.role)), csrf=claims.get("csrf"), via_cookie=via_cookie)

    def require_admin(session: Session = Depends(current_session)) -> Session:
        if session.user.role != Role.ADMIN:
            raise _problem_exception(403, "FORBIDDEN", "requires role admin")
        return session

    # ------------------------------------------------------------ auth

    class LoginRequest(BaseModel):
        username: str
        password: str

    @app.post("/api/login")
    def login(body: LoginRequest, response: Response):
        user = check_credentials(body.username, body.password)
        if isinstance(user, JSONResponse):
            return user
        token, csrf = issue_session(user)
        set_session_cookies(response, token, csrf)
        audit("LOGIN", User(user.username, Role(user.role)))
        return {"username": user.username, "role": user.role, "csrfToken": csrf}

    @app.post("/api/token")
    def oauth2_password_grant(grant_type: str = Form(...), username: str = Form(...), password: str = Form(...)):
        """RFC 6749 section 4.3 resource-owner password grant, for scripts
        and CLI use: the same users and roles as the GUI, sent as
        `Authorization: Bearer`. No CSRF check applies to Bearer calls
        (a browser never attaches them on its own).
        """
        if grant_type != "password":
            return JSONResponse(status_code=400, content={"error": "unsupported_grant_type"})
        user = check_credentials(username, password)
        if isinstance(user, JSONResponse):
            return JSONResponse(status_code=400 if user.status_code == 401 else user.status_code,
                                content={"error": "invalid_grant"})
        token, _ = issue_session(user)
        audit("TOKEN", User(user.username, Role(user.role)))
        return {"access_token": token, "token_type": "Bearer", "expires_in": cfg.session_ttl_seconds}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.cookies.get(SESSION_COOKIE, "")
        claims = decode_jwt(token, cfg.jwt_secret)
        response.delete_cookie(SESSION_COOKIE, path="/api", secure=cfg.cookie_secure, samesite="strict")
        response.delete_cookie(CSRF_COOKIE, path="/", secure=cfg.cookie_secure, samesite="strict")
        if claims:
            if claims.get("jti"):
                # the cookie is cleared in the browser, but a copy of it (or the token) would stay good until it expires: the session itself is ended here
                app.state.db.revoke_session(claims["jti"], claims["exp"], time.time())
            audit("LOGOUT", username=claims.get("sub"))
        return {"status": "logged out"}

    @app.get("/api/me")
    def me(session: Session = Depends(current_session)):
        return {"username": session.user.username, "role": session.user.role, "csrfToken": session.csrf}

    class ChangePasswordRequest(BaseModel):
        currentPassword: str
        newPassword: str = Field(min_length=MIN_PASSWORD_LENGTH)

    @app.post("/api/me/password")
    def change_own_password(body: ChangePasswordRequest, response: Response, session: Session = Depends(current_session)):
        with app.state.db.session() as s:
            user = s.get(GuiUser, session.user.username)
            if not verify_password(body.currentPassword, user.password_hash):
                return _problem(400, "INVALID_CREDENTIALS", "current password is wrong")
            user.password_hash = hash_password(body.newPassword)
            user.token_version += 1
            s.commit()
            token, csrf = issue_session(user)
        if session.via_cookie:
            set_session_cookies(response, token, csrf)
        audit("PASSWORD_CHANGED", session.user)
        return {"status": "password changed", "csrfToken": csrf}

    @app.get("/api/permissions")
    def permissions(session: Session = Depends(current_session)):
        """The RBAC table itself — the SPA evaluates the same rules, first
        match wins, to decide which actions to show. Display only: the
        proxy below re-checks every call.
        """
        return {"role": session.user.role, "rules": [
            {"method": r.method, "pattern": r.pattern.pattern, "role": r.role, "queryMatch": r.query_match}
            for r in RULES
        ]}

    # ------------------------------------------------------------ health

    @app.get("/api/modules/status")
    async def modules_status(session: Session = Depends(current_session)):
        gw: R1Gateway = app.state.gateway

        async def get(module: str, route: str) -> httpx.Response:
            # R1's own routes are public and answered by the gateway; a module's are reached through R1's token-gated proxy
            if module == "r1-termination":
                return await gw.r1_get(route, cfg.health_timeout_seconds)
            return await gw.request("GET", f"/{module}{route}", timeout=cfg.health_timeout_seconds)

        async def optional_json(module: str, route: str) -> tuple[int | None, dict]:
            """(status, body) of a route a module may not have (an older build has no /version): never an error."""
            try:
                resp = await get(module, route)
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                return resp.status_code, body if isinstance(body, dict) else {}
            except (SmoAuthError, httpx.HTTPError, ValueError):
                return None, {}

        async def probe(module: str) -> dict:
            started = time.perf_counter()
            try:
                resp = await get(module, "/health")
                healthy, status_code, error = resp.status_code == 200, resp.status_code, None
            except SmoAuthError as exc:
                log.warning("health probe %s: SMO token unavailable: %s", module, exc)
                healthy, status_code, error = False, None, "auth: no SMO access token"
            except httpx.HTTPError as exc:
                log.warning("health probe %s failed: %r", module, exc)
                healthy, status_code, error = False, None, "unreachable"
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            if healthy:     # a module that is not live is not asked again
                (ready_status, _), (version_status, version) = await asyncio.gather(
                    optional_json(module, "/ready"), optional_json(module, "/version"))
            else:
                ready_status, version_status, version = None, None, {}
            known = version_status == 200
            return {"module": module, "healthy": healthy, "latencyMs": latency_ms,
                    "statusCode": status_code, "error": error,
                    # PR-OBS-8.2: readiness (null when the module did not answer /ready with 200 or 503) and the build it runs (null when
                    # it has no /version, e.g. an older release during a rolling upgrade)
                    "ready": ready_status == 200 if ready_status in (200, 503) else None,
                    "version": version.get("version") if known else None,
                    "buildSha": version.get("buildSha") if known else None,
                    "builtAt": version.get("builtAt") if known else None}

        results = await asyncio.gather(*(probe(m) for m in STATUS_MODULES))
        return {"checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "modules": list(results)}

    # ------------------------------------------------------------ proxy

    @app.api_route("/api/smo/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(full_path: str, request: Request, session: Session = Depends(current_session)):
        path = "/" + full_path
        query: dict[str, list[str]] = {}
        for k, v in request.query_params.multi_items():
            query.setdefault(k, []).append(v)
        decision = decide(request.method, path, query, session.user.role)
        mutating = request.method in UNSAFE_METHODS
        if not decision.allowed:
            detail = f"requires role {decision.required_role}" if decision.required_role else "not exposed through the GUI"
            audit("DENIED", session.user, method=request.method, path=path, status_code=403, detail=detail)
            return _problem(403, "FORBIDDEN", detail)

        rule = cast(Rule, decision.rule)         # allowed means a rule matched
        params = [(k, v) for k, v in request.query_params.multi_items()]
        if rule.query_overrides:
            forced = rule.query_overrides(session.user)
            params = [(k, v) for k, v in params if k not in forced] + list(forced.items())
        body = await request.body()
        if rule.json_overrides:
            try:
                payload = await request.json()
            except ValueError:
                return _problem(400, "INVALID_BODY", "expected a JSON object")
            if not isinstance(payload, dict):
                return _problem(400, "INVALID_BODY", "expected a JSON object")
            body = json.dumps({**payload, **rule.json_overrides(session.user)}).encode()

        headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARD_REQUEST}
        try:
            upstream = await app.state.gateway.request(request.method, path, params=params, content=body or None, headers=headers)
        except SmoAuthError as exc:
            if mutating:
                audit("PROXY", session.user, method=request.method, path=path, status_code=502, detail=f"auth: {exc}")
            log.warning("proxy %s %s: SMO token unavailable: %s", request.method, path, exc)
            return _problem(502, "SMO_AUTH_FAILED", "the BFF could not obtain an SMO access token from SME")
        except httpx.HTTPError as exc:
            if mutating:
                audit("PROXY", session.user, method=request.method, path=path, status_code=502, detail=exc.__class__.__name__)
            log.warning("proxy %s %s: R1 Termination unreachable: %r", request.method, path, exc)
            return _problem(502, "R1_UNREACHABLE", "R1 Termination did not answer")

        if mutating:
            query_text = "&".join(f"{k}={v}" for k, v in params)
            audit("PROXY", session.user, method=request.method, path=path + (f"?{query_text}" if query_text else ""),
                  status_code=upstream.status_code)
        out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _NEVER_FORWARD_RESPONSE}
        return Response(content=upstream.content, status_code=upstream.status_code, headers=out_headers)

    # ------------------------------------------------------------ admin

    class CreateUserRequest(BaseModel):
        username: str
        password: str = Field(min_length=MIN_PASSWORD_LENGTH)
        role: Role

    class UpdateUserRequest(BaseModel):
        role: Role | None = None
        active: bool | None = None
        password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH)

    def _user_view(u: GuiUser) -> dict:
        return {"username": u.username, "role": u.role, "active": u.active, "createdAt": u.created_at.isoformat()}

    def _active_admins(s) -> int:
        return len(s.scalars(select(GuiUser).where(GuiUser.role == Role.ADMIN, GuiUser.active.is_(True))).all())

    @app.get("/api/admin/users")
    def list_users(session: Session = Depends(require_admin)):
        with app.state.db.session() as s:
            return [_user_view(u) for u in s.scalars(select(GuiUser).order_by(GuiUser.username)).all()]

    @app.post("/api/admin/users", status_code=201)
    def create_user(body: CreateUserRequest, session: Session = Depends(require_admin)):
        if not USERNAME_RE.match(body.username):
            return _problem(400, "INVALID_USERNAME", "2-32 chars: lowercase letter first, then a-z 0-9 _ . -")
        with app.state.db.session() as s:
            if s.get(GuiUser, body.username) is not None:
                return _problem(409, "USER_EXISTS")
            # A random starting token version, not 0: a session token carries the version it was issued under, so a user deleted
            # and created again under the same name (STD-4.3) must not make the old person's unexpired token valid again.
            user = GuiUser(username=body.username, password_hash=hash_password(body.password), role=body.role,
                           token_version=secrets.randbits(30))
            s.add(user)
            s.commit()
            view = _user_view(user)
        audit("USER_CREATED", session.user, detail=f"{body.username} role={body.role}")
        return view

    @app.patch("/api/admin/users/{username}")
    def update_user(username: str, body: UpdateUserRequest, session: Session = Depends(require_admin)):
        with app.state.db.session() as s:
            user = s.get(GuiUser, username)
            if user is None:
                return _problem(404, "NO_SUCH_USER")
            demoting = (body.role is not None and body.role != Role.ADMIN) or body.active is False
            if user.role == Role.ADMIN and demoting and _active_admins(s) <= 1:
                return _problem(409, "LAST_ADMIN", "at least one active admin must remain")
            changes = []
            if body.role is not None and body.role != user.role:
                changes.append(f"role {user.role}->{body.role}")
                user.role = body.role
            if body.active is not None and body.active != user.active:
                changes.append("activated" if body.active else "deactivated")
                user.active = body.active
                user.token_version += 1
            if body.password is not None:
                changes.append("password reset")
                user.password_hash = hash_password(body.password)
                user.token_version += 1
            s.commit()
            view = _user_view(user)
        audit("USER_UPDATED", session.user, detail=f"{username}: {', '.join(changes) or 'no change'}")
        return view

    @app.delete("/api/admin/users/{username}", status_code=204)
    def delete_user(username: str, session: Session = Depends(require_admin)):
        if username == session.user.username:
            return _problem(409, "CANNOT_DELETE_SELF")
        with app.state.db.session() as s:
            user = s.get(GuiUser, username)
            if user is None:
                return Response(status_code=204)
            if user.role == Role.ADMIN and user.active and _active_admins(s) <= 1:
                return _problem(409, "LAST_ADMIN", "at least one active admin must remain")
            # One transaction: the account and the failed-login counter kept under its name go together (STD-4.3). There is no
            # session row to remove: a session is a signed token, and with its user gone every token naming it is refused.
            # The audit rows that name the user stay (docs/PRIVACY.md), and so do the module tables that record `smo-gui:<name>`.
            s.execute(delete(LoginFailure).where(LoginFailure.username == username))
            s.delete(user)
            s.commit()
        audit("USER_DELETED", session.user, detail=username)
        return Response(status_code=204)

    @app.get("/api/admin/audit")
    def list_audit(limit: int = PageLimit, offset: int = PageOffset, username: str | None = None, action: str | None = None,
                    session: Session = Depends(require_admin)):
        stmt = select(AuditEntry).order_by(AuditEntry.id.desc())
        if username:
            stmt = stmt.where(AuditEntry.username == username)
        if action:
            stmt = stmt.where(AuditEntry.action == action)
        with app.state.db.session() as s:
            page = _paginate(s, stmt, limit, offset)
            return {**page, "items": [{"id": e.id, "at": e.at.isoformat(), "username": e.username, "role": e.role, "action": e.action,
                     "method": e.method, "path": e.path, "statusCode": e.status_code, "detail": e.detail}
                    for e in page["items"]]}

    return app


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))

app = create_app()
