#!/usr/bin/env python3
"""Browser check of sessions and roles of the operator GUI against a running stack (PR-V-13): for each of viewer, operator and admin, in a real browser,

  - sign in; the sidebar shows the pages the role may open (Admin only for the admin) and nothing is shown that the role cannot use;
  - the cookies are what the design says: `smo_session` HttpOnly, SameSite=Strict, Path=/api (not readable by a script), `smo_csrf` readable;
  - the backend enforces the role whatever the page shows: `/api/admin/users` is 403 below admin, a write without the CSRF header is 403;
  - sign out ends the session: the sidebar is gone, and the cookie copied before is refused (401) afterwards.

Then, for the viewer account: five wrong passwords lock it (429), and with `--expiry` the session, issued with a short lifetime (`GUI_SESSION_TTL_SECONDS`
on the stack), is refused once the lifetime has passed and the GUI falls back to the sign-in page.

    GUI_E2E_PASSWORD=... GUI_E2E_OPERATOR_PASSWORD=... GUI_E2E_VIEWER_PASSWORD=... scripts/gui_session_e2e.py [--base-url http://localhost:3000] [--expiry SECONDS [--only-expiry]]

Exit 0 only when every check passes; each failure is printed as `FAIL: ...`.
"""

import argparse
import os
import sys
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

ADMIN_ONLY_PAGE = "Admin"
COMMON_PAGES = ["Dashboard", "Alarms"]       # visible to every role (labels of the sidebar)
LOCKOUT_ATTEMPTS = 5


def sign_in(page, base_url: str, user: str, password: str) -> None:
    """Signs in through the login form and waits for the dashboard's "Modules healthy" tile."""
    page.goto(f"{base_url}/login")
    page.get_by_label("Username").fill(user)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.get_by_text("Modules healthy").wait_for()


def sidebar(page) -> list[str]:
    """The visible labels of the GUI's main navigation, stripped."""
    return [t.strip() for t in page.locator("nav[aria-label='Main'] a").all_inner_texts()]


def check_role(browser, base_url: str, user: str, password: str, role: str, problems: list[str]) -> None:
    """Signs in as one account in a fresh browser context and appends to `problems` anything that differs from the design for `role`.

        Checks the sidebar (Admin only for the admin), the cookies (`smo_session` HttpOnly, SameSite=Strict, Path=/api and unreadable by a script; `smo_csrf` readable), the
        backend (`/api/admin/users` 200 for admin and 403 below; a write without the CSRF header 403; with it 403 below admin and 201 for the admin, whose test user is deleted
        again) and the sign-out (sidebar gone, cookie gone, the copied cookie refused with 401). Returns early when the session cookies are missing. Playwright errors become problems.
    """
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(30_000)
    try:
        sign_in(page, base_url, user, password)
        labels = " | ".join(sidebar(page))
        for label in COMMON_PAGES:
            if label not in labels:
                problems.append(f"{role}: the sidebar has no '{label}' ({labels})")
        has_admin = ADMIN_ONLY_PAGE in labels
        if has_admin != (role == "admin"):
            problems.append(f"{role}: the sidebar {'shows' if has_admin else 'hides'} Admin")

        cookies = {c["name"]: c for c in context.cookies()}
        session, csrf = cookies.get("smo_session"), cookies.get("smo_csrf")
        if not session or not csrf:
            problems.append(f"{role}: cookies after sign-in: {sorted(cookies)}")
            return
        if not session["httpOnly"] or session["sameSite"] != "Strict" or session["path"] != "/api":
            problems.append(f"{role}: smo_session is httpOnly={session['httpOnly']} sameSite={session['sameSite']} path={session['path']}")
        if csrf["httpOnly"] or csrf["sameSite"] != "Strict":
            problems.append(f"{role}: smo_csrf is httpOnly={csrf['httpOnly']} sameSite={csrf['sameSite']}")
        if "smo_session" in page.evaluate("document.cookie"):
            problems.append(f"{role}: a script can read smo_session")

        api = context.request
        users = api.get(f"{base_url}/api/admin/users")
        if users.status != (200 if role == "admin" else 403):
            problems.append(f"{role}: GET /api/admin/users answered {users.status}")
        body = {"username": "e2e-never", "password": "never-created-1", "role": "viewer"}
        no_csrf = api.post(f"{base_url}/api/admin/users", data=body)
        if no_csrf.status != 403:
            problems.append(f"{role}: a write without the CSRF header answered {no_csrf.status}")
        with_csrf = api.post(f"{base_url}/api/admin/users", data=body, headers={"X-CSRF-Token": csrf["value"]})
        if role != "admin" and with_csrf.status != 403:
            problems.append(f"{role}: POST /api/admin/users with the CSRF header answered {with_csrf.status}")
        if role == "admin":
            if with_csrf.status != 201:
                problems.append(f"admin: creating a user answered {with_csrf.status}")
            else:
                api.delete(f"{base_url}/api/admin/users/e2e-never", headers={"X-CSRF-Token": csrf["value"]})

        copied = {"Cookie": f"smo_session={session['value']}"}
        page.get_by_role("button", name="Sign out").click()
        page.get_by_label("Username").wait_for()
        if sidebar(page):
            problems.append(f"{role}: the sidebar is still there after signing out")
        if any(c["name"] == "smo_session" for c in context.cookies()):
            problems.append(f"{role}: smo_session is still in the browser after signing out")
        replay = browser.new_context().request.get(f"{base_url}/api/me", headers=copied)
        if replay.status != 401:
            problems.append(f"{role}: the copied session answered {replay.status} after signing out (401 expected)")
        print(f"{role}: sign-in, cookies, role enforcement and sign-out checked")
    except (PlaywrightTimeout, PlaywrightError) as exc:
        problems.append(f"{role}: {type(exc).__name__}: {str(exc).splitlines()[0]}")
    finally:
        context.close()


def check_lockout(browser, base_url: str, user: str, problems: list[str]) -> None:
    """Sends five wrong passwords for `user` and then a sixth: the first five must answer 401 and the sixth 429.

        Side effect: the account stays locked on the stack for the lockout period, so `main` runs this after the role checks.
    """
    context = browser.new_context()
    statuses = [context.request.post(f"{base_url}/api/login", data={"username": user, "password": f"wrong-{i}-password"}).status
                for i in range(LOCKOUT_ATTEMPTS + 1)]
    if statuses[:LOCKOUT_ATTEMPTS] != [401] * LOCKOUT_ATTEMPTS or statuses[-1] != 429:
        problems.append(f"lockout: the answers to {LOCKOUT_ATTEMPTS + 1} wrong passwords were {statuses} (five 401 then 429 expected)")
    print(f"lockout: {statuses}")
    context.close()


def check_expiry(browser, base_url: str, user: str, password: str, seconds: int, problems: list[str]) -> None:
    """Signs in, waits `seconds` + 2 s (the stack's `GUI_SESSION_TTL_SECONDS` plus a margin), and checks that the copied session is refused with 401 and that the GUI falls back to the sign-in page."""
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(30_000)
    try:
        sign_in(page, base_url, user, password)
        if context.request.get(f"{base_url}/api/me").status != 200:
            problems.append("expiry: the new session was refused")
        token = next(c["value"] for c in context.cookies() if c["name"] == "smo_session")
        time.sleep(seconds + 2)
        late = browser.new_context().request.get(f"{base_url}/api/me", headers={"Cookie": f"smo_session={token}"})
        if late.status != 401:
            problems.append(f"expiry: the session answered {late.status} after {seconds + 2} s (401 expected)")
        page.goto(f"{base_url}/alarms")
        page.get_by_label("Username").wait_for()      # the sign-in page
        if "/login" not in page.url:
            problems.append(f"expiry: the GUI did not fall back to the sign-in page ({page.url})")
        print(f"expiry: refused after {seconds + 2} s, back on {page.url.replace(base_url, '')}")
    except (PlaywrightTimeout, PlaywrightError) as exc:
        problems.append(f"expiry: {type(exc).__name__}: {str(exc).splitlines()[0]}")
    finally:
        context.close()


def main() -> int:
    """Reads the three account passwords from the environment (exit 2 when one is unset), runs the role and lockout checks unless `--only-expiry`, and the expiry check
        (as the operator) when `--expiry` is given. Returns 1 if any problem was found, else 0.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://localhost:3000")
    ap.add_argument("--expiry", type=int, default=0, help="the stack's GUI_SESSION_TTL_SECONDS: also check that a session ends after that long")
    ap.add_argument("--only-expiry", action="store_true", help="with --expiry: run that check alone (the stack's sessions are too short for the others)")
    ap.add_argument("--chromium", default=os.environ.get("GUI_E2E_CHROMIUM"), help="path to a Chromium binary (default: Playwright's own)")
    args = ap.parse_args()
    accounts = [("viewer", "viewer", os.environ.get("GUI_E2E_VIEWER_PASSWORD", "")),
                ("operator", "operator", os.environ.get("GUI_E2E_OPERATOR_PASSWORD", "")),
                ("admin", "admin", os.environ.get("GUI_E2E_PASSWORD", ""))]
    missing = [f"GUI_E2E_{n}" for n, _, p in accounts if not p]
    if missing:
        print(f"not set: {', '.join(missing)}", file=sys.stderr)
        return 2
    base_url = args.base_url.rstrip("/")
    problems: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium) if args.chromium else pw.chromium.launch()
        try:
            if not args.only_expiry:
                for role, user, password in accounts:
                    check_role(browser, base_url, user, password, role, problems)
                check_lockout(browser, base_url, "viewer", problems)
            if args.expiry:
                check_expiry(browser, base_url, "operator", accounts[1][2], args.expiry, problems)
        finally:
            browser.close()
    for problem in problems:
        print("FAIL:", problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
