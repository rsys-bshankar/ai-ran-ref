#!/usr/bin/env python3
"""Browser check of the GUI's OIDC login (PR-SEC-6) against a running stack whose backend is configured for a real Keycloak (the realm in
gui-bff/tests/keycloak/smo-realm.json, which .github/workflows/smo-gui-e2e.yml starts): for each of an admin, an operator and a viewer of the identity
provider, in a real browser,

  - the sign-in page offers "Sign in with Keycloak", the click goes to Keycloak, and signing in there lands in the GUI with the role the group maps to
    (the sidebar shows Admin for the admin only);
  - the session is the GUI's own: `smo_session` HttpOnly, SameSite=Strict, Path=/api; the user is `oidc:<subject>`, created without a password;
  - the backend enforces the role (`/api/admin/users` is 403 below admin);
  - signing out ends the GUI session (the copied cookie is refused) and goes on to Keycloak's end-session page; after it the next click on the button asks
    for a password again.

Then: a user in no mapped group is sent back to the sign-in page with the "none of your groups" message and gets no session; a callback with a state
nobody issued is refused; and the audit log (read by the OIDC admin) holds the OIDC_LOGIN rows with the provider's subject and the refused attempt.

    scripts/gui_oidc_e2e.py [--base-url http://localhost:3000] [--provider-name Keycloak]

Exit 0 only when every check passes; each failure is printed as `FAIL: ...`. The accounts are the throwaway ones of the CI realm; the passwords can be
overridden with GUI_E2E_OIDC_<USER>_PASSWORD (ADMIN, OPERATOR, VIEWER, NOGROUP).
"""

import argparse
import os
import sys

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

ACCOUNTS = [("oidc-admin", "admin", "ci-oidc-admin-1"), ("oidc-operator", "operator", "ci-oidc-operator-1"), ("oidc-viewer", "viewer", "ci-oidc-viewer-1")]
NO_GROUP = ("oidc-nogroup", "ci-oidc-nogroup-1")
COMMON_PAGES = ["Dashboard", "Alarms"]


def password_for(user: str, default: str) -> str:
    return os.environ.get(f"GUI_E2E_OIDC_{user.removeprefix('oidc-').upper()}_PASSWORD", default)


def sidebar(page) -> list[str]:
    return [t.strip() for t in page.locator("nav[aria-label='Main'] a").all_inner_texts()]


def keycloak_login(page, user: str, password: str) -> None:
    page.locator("#username").fill(user)
    page.locator("#password").fill(password)
    page.locator("#kc-login").click()


def start_sign_in(page, base_url: str, provider: str) -> None:
    page.goto(f"{base_url}/login")
    page.get_by_role("link", name=f"Sign in with {provider}").click()


def check_role(browser, base_url: str, provider: str, user: str, role: str, password: str, problems: list[str]) -> None:
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(30_000)
    try:
        start_sign_in(page, base_url, provider)
        keycloak_login(page, user, password)
        page.get_by_text("Modules healthy").wait_for()
        if page.url.rstrip("/") != base_url:
            problems.append(f"{role}: after signing in the browser is at {page.url}")
        labels = " | ".join(sidebar(page))
        for label in COMMON_PAGES:
            if label not in labels:
                problems.append(f"{role}: the sidebar has no '{label}' ({labels})")
        if ("Admin" in labels) != (role == "admin"):
            problems.append(f"{role}: the sidebar {'shows' if 'Admin' in labels else 'hides'} Admin")

        cookies = {c["name"]: c for c in context.cookies()}
        session, csrf = cookies.get("smo_session"), cookies.get("smo_csrf")
        if not session or not csrf:
            problems.append(f"{role}: cookies after sign-in: {sorted(cookies)}")
            return
        if not session["httpOnly"] or session["sameSite"] != "Strict" or session["path"] != "/api":
            problems.append(f"{role}: smo_session is httpOnly={session['httpOnly']} sameSite={session['sameSite']} path={session['path']}")
        if "smo_oidc" in cookies:
            problems.append(f"{role}: the sign-in's binding cookie smo_oidc is still there after the callback")

        api = context.request
        me = api.get(f"{base_url}/api/me")
        body = me.json() if me.status == 200 else {}
        if me.status != 200 or not str(body.get("username", "")).startswith("oidc:") or body.get("role") != role:
            problems.append(f"{role}: /api/me answered {me.status} {body}")
        users = api.get(f"{base_url}/api/admin/users")
        if users.status != (200 if role == "admin" else 403):
            problems.append(f"{role}: GET /api/admin/users answered {users.status}")
        if role == "admin":
            listed = {u["username"]: u for u in users.json()}
            created = [u for name, u in listed.items() if name.startswith("oidc:")]
            if not created:
                problems.append("admin: no oidc:<subject> user row after the first sign-in")

        copied = {"Cookie": f"smo_session={session['value']}"}
        page.get_by_role("button", name="Sign out").click()
        # the GUI session is over; the browser goes on to Keycloak's end-session page (a confirmation, since no ID token hint is sent)
        page.wait_for_url(lambda url: not url.startswith(base_url), timeout=30_000)
        if page.locator("#kc-logout").count():
            page.locator("#kc-logout").click()
        page.wait_for_url(f"{base_url}/login*")
        if any(c["name"] == "smo_session" for c in context.cookies()):
            problems.append(f"{role}: smo_session is still in the browser after signing out")
        replay = browser.new_context().request.get(f"{base_url}/api/me", headers=copied)
        if replay.status != 401:
            problems.append(f"{role}: the copied session answered {replay.status} after signing out (401 expected)")
        page.get_by_role("link", name=f"Sign in with {provider}").click()
        page.locator("#username").wait_for()          # Keycloak's session ended too: it asks again
        print(f"{role}: sign-in through the provider, role, cookies and sign-out checked")
    except (PlaywrightTimeout, PlaywrightError) as exc:
        problems.append(f"{role}: {type(exc).__name__}: {str(exc).splitlines()[0]}")
    finally:
        context.close()


def check_no_group(browser, base_url: str, provider: str, problems: list[str]) -> None:
    user, password = NO_GROUP[0], password_for(*NO_GROUP)
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(30_000)
    try:
        start_sign_in(page, base_url, provider)
        keycloak_login(page, user, password)
        page.get_by_text("none of your groups").wait_for()
        if "oidc_error=no_role" not in page.url:
            problems.append(f"no group: the sign-in page was opened as {page.url}")
        if any(c["name"] == "smo_session" for c in context.cookies()):
            problems.append("no group: a session was issued to a user with no mapped group")
        if context.request.get(f"{base_url}/api/me").status != 401:
            problems.append("no group: /api/me did not answer 401")
        print("no group: refused with the explanation, no session")
    except (PlaywrightTimeout, PlaywrightError) as exc:
        problems.append(f"no group: {type(exc).__name__}: {str(exc).splitlines()[0]}")
    finally:
        context.close()


def check_forged_callback(browser, base_url: str, problems: list[str]) -> None:
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(f"{base_url}/api/oidc/callback?code=forged&state=nobody-issued-this")
        page.wait_for_url(f"{base_url}/login*")
        if "oidc_error=invalid_state" not in page.url:
            problems.append(f"forged callback: ended at {page.url} (oidc_error=invalid_state expected)")
        if any(c["name"] == "smo_session" for c in context.cookies()):
            problems.append("forged callback: a session was issued")
        print("forged callback: refused")
    except (PlaywrightTimeout, PlaywrightError) as exc:
        problems.append(f"forged callback: {type(exc).__name__}: {str(exc).splitlines()[0]}")
    finally:
        context.close()


def check_audit(browser, base_url: str, provider: str, problems: list[str]) -> None:
    user, role, default = ACCOUNTS[0]
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(30_000)
    try:
        start_sign_in(page, base_url, provider)
        keycloak_login(page, user, password_for(user, default))
        page.get_by_text("Modules healthy").wait_for()
        for action, expected in (("OIDC_LOGIN", "oidc:"), ("OIDC_LOGIN_FAILED", None)):
            resp = context.request.get(f"{base_url}/api/admin/audit?action={action}&limit=50")
            rows = resp.json().get("items", []) if resp.status == 200 else []
            if not rows:
                problems.append(f"audit: no {action} rows (status {resp.status})")
            elif expected and not all(str(r.get("username", "")).startswith(expected) for r in rows):
                problems.append(f"audit: a {action} row without the provider's subject: {rows[0]}")
        failed = context.request.get(f"{base_url}/api/admin/audit?action=OIDC_LOGIN_FAILED&limit=50").json().get("items", [])
        if not any(str(r.get("detail", "")).startswith("no_role") for r in failed) or not any(str(r.get("detail", "")).startswith("invalid_state") for r in failed):
            problems.append("audit: the no_role and invalid_state refusals are not both in the log")
        print("audit: OIDC_LOGIN rows carry the subject; the refusals are logged")
    except (PlaywrightTimeout, PlaywrightError) as exc:
        problems.append(f"audit: {type(exc).__name__}: {str(exc).splitlines()[0]}")
    finally:
        context.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://localhost:3000")
    ap.add_argument("--provider-name", default="Keycloak", help="GUI_OIDC_PROVIDER_NAME of the stack")
    ap.add_argument("--chromium", default=os.environ.get("GUI_E2E_CHROMIUM"), help="path to a Chromium binary (default: Playwright's own)")
    args = ap.parse_args()
    base_url = args.base_url.rstrip("/")
    problems: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium) if args.chromium else pw.chromium.launch()
        try:
            for user, role, default in ACCOUNTS:
                check_role(browser, base_url, args.provider_name, user, role, password_for(user, default), problems)
            check_no_group(browser, base_url, args.provider_name, problems)
            check_forged_callback(browser, base_url, problems)
            check_audit(browser, base_url, args.provider_name, problems)      # last: it reads what the checks above left in the log
        finally:
            browser.close()
    for problem in problems:
        print("FAIL:", problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
