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
  GET  /api/modules/status      every module's health via R1, probed in parallel
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
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .config import Settings, settings as default_settings
from .db import AuditEntry, Database, GuiUser
from .rbac import MODULES, RULES, Role, User, decide
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


def _problem(status: int, title: str, detail: str | None = None) -> JSONResponse:
    body = {"title": title, "status": status}
    if detail:
        body["detail"] = detail
    return JSONResponse(status_code=status, content=body)


def seed_users(db: Database, cfg: Settings) -> None:
    """First boot only: creates admin (always) plus operator/viewer (when
    their passwords are set). Never touches an existing user table.
    """
    with db.session() as s:
        if s.scalar(select(GuiUser).limit(1)) is not None:
            return
        admin_password = cfg.admin_password
        if not admin_password:
            admin_password = secrets.token_urlsafe(12)
            _write_initial_password(cfg.initial_password_file, admin_password)
            log.warning("GUI_ADMIN_PASSWORD not set: seeded user 'admin' with a generated password, written to %s "
                        "(mode 0600). Sign in, change it under Admin > Users, then delete the file.",
                        cfg.initial_password_file)
        seeds = [("admin", admin_password, Role.ADMIN), ("operator", cfg.operator_password, Role.OPERATOR),
                 ("viewer", cfg.viewer_password, Role.VIEWER)]
        for username, password, role in seeds:
            if password:
                s.add(GuiUser(username=username, password_hash=hash_password(password), role=role))
        s.commit()


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
            log.warning("GUI_JWT_SECRET not set: using a random per-boot secret (sessions end on restart)")
        if app.state.gateway is None:
            app.state.gateway = R1Gateway(cfg.r1_url, app.state.db, sme_url=cfg.sme_url, timeout=cfg.upstream_timeout_seconds)
        yield
        await app.state.gateway.aclose()

    app = FastAPI(title="SMO Operator GUI BFF", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url="/api/openapi.json")
    app.state.db, app.state.gateway, app.state.cfg = db, gateway, cfg
    app.state.login_failures = {}

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
        token = issue_jwt({"sub": user.username, "ver": user.token_version, "csrf": csrf},
                          cfg.jwt_secret, cfg.session_ttl_seconds)
        return token, csrf

    def set_session_cookies(response: Response, token: str, csrf: str) -> None:
        common = {"secure": cfg.cookie_secure, "samesite": "strict", "max_age": cfg.session_ttl_seconds}
        response.set_cookie(SESSION_COOKIE, token, httponly=True, path="/api", **common)
        # Readable by the SPA on purpose: double-submit CSRF token, echoed back
        # as X-CSRF-Token and checked against the claim inside the session JWT.
        response.set_cookie(CSRF_COOKIE, csrf, httponly=False, path="/", **common)

    def check_credentials(username: str, password: str) -> GuiUser | JSONResponse:
        failures = app.state.login_failures
        count, first = failures.get(username, (0, 0.0))
        if count >= MAX_LOGIN_FAILURES and time.time() - first < LOCKOUT_SECONDS:
            audit("LOGIN_LOCKED", username=username)
            return _problem(429, "TOO_MANY_ATTEMPTS", "account temporarily locked after repeated failures")
        with app.state.db.session() as s:
            user = s.get(GuiUser, username)
        # Verify against a dummy hash for unknown users, so response time
        # doesn't reveal which usernames exist.
        ok = verify_password(password, user.password_hash if user else _DUMMY_HASH) and user is not None and user.active
        if not ok:
            if time.time() - first >= LOCKOUT_SECONDS:
                count, first = 0, time.time()
            failures[username] = (count + 1, first)
            audit("LOGIN_FAILED", username=username)
            return _problem(401, "INVALID_CREDENTIALS")
        failures.pop(username, None)
        return user

    def current_session(request: Request) -> Session:
        auth = request.headers.get("authorization", "")
        via_cookie = not auth.lower().startswith("bearer ")
        token = request.cookies.get(SESSION_COOKIE) if via_cookie else auth[7:].strip()
        claims = decode_jwt(token or "", cfg.jwt_secret)
        if claims is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        with app.state.db.session() as s:
            user = s.get(GuiUser, claims.get("sub"))
        if user is None or not user.active or user.token_version != claims.get("ver"):
            raise HTTPException(status_code=401, detail="session revoked")
        if via_cookie and request.method in UNSAFE_METHODS:
            sent = request.headers.get(CSRF_HEADER, "")
            if not sent or not hmac.compare_digest(sent, str(claims.get("csrf", ""))):
                raise HTTPException(status_code=403, detail="missing or invalid CSRF token")
        # Role always read from the user table, never from the token: a role
        # change or demotion applies on the very next request.
        return Session(user=User(user.username, Role(user.role)), csrf=claims.get("csrf"), via_cookie=via_cookie)

    def require_admin(session: Session = Depends(current_session)) -> Session:
        if session.user.role != Role.ADMIN:
            raise HTTPException(status_code=403, detail="requires role admin")
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
        claims = decode_jwt(request.cookies.get(SESSION_COOKIE, ""), cfg.jwt_secret)
        response.delete_cookie(SESSION_COOKIE, path="/api", secure=cfg.cookie_secure, samesite="strict")
        response.delete_cookie(CSRF_COOKIE, path="/", secure=cfg.cookie_secure, samesite="strict")
        if claims:
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

        async def probe(module: str) -> dict:
            started = time.perf_counter()
            try:
                if module == "r1-termination":
                    resp = await gw.r1_health(cfg.health_timeout_seconds)
                else:
                    resp = await gw.request("GET", f"/{module}/health", timeout=cfg.health_timeout_seconds)
                healthy, status_code, error = resp.status_code == 200, resp.status_code, None
            except SmoAuthError as exc:
                log.warning("health probe %s: SMO token unavailable: %s", module, exc)
                healthy, status_code, error = False, None, "auth: no SMO access token"
            except httpx.HTTPError as exc:
                log.warning("health probe %s failed: %r", module, exc)
                healthy, status_code, error = False, None, "unreachable"
            return {"module": module, "healthy": healthy, "latencyMs": round((time.perf_counter() - started) * 1000, 1),
                    "statusCode": status_code, "error": error}

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

        rule = decision.rule
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
            user = GuiUser(username=body.username, password_hash=hash_password(body.password), role=body.role)
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
            s.delete(user)
            s.commit()
        audit("USER_DELETED", session.user, detail=username)
        return Response(status_code=204)

    @app.get("/api/admin/audit")
    def list_audit(limit: int = 200, username: str | None = None, action: str | None = None,
                   session: Session = Depends(require_admin)):
        stmt = select(AuditEntry).order_by(AuditEntry.id.desc()).limit(min(max(limit, 1), 1000))
        if username:
            stmt = stmt.where(AuditEntry.username == username)
        if action:
            stmt = stmt.where(AuditEntry.action == action)
        with app.state.db.session() as s:
            return [{"id": e.id, "at": e.at.isoformat(), "username": e.username, "role": e.role, "action": e.action,
                     "method": e.method, "path": e.path, "statusCode": e.status_code, "detail": e.detail}
                    for e in s.scalars(stmt).all()]

    return app


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))

app = create_app()
