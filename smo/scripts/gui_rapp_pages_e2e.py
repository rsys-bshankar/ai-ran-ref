#!/usr/bin/env python3
"""Browser check of the pages that rApp packages declare (GUI-8.7, PR-GUI-8), against a running stack, in Chromium.

    GUI_E2E_OPERATOR_PASSWORD=... GUI_E2E_VIEWER_PASSWORD=... scripts/gui_rapp_pages_e2e.py --instances instances.json --calls-command "<prints GET /_calls of the stub>"
                                                                                           [--base-url http://localhost:3000] [--out gui-rapp-pages]

`instances.json` is what `scripts/gui_rapp_stub.py setup` printed: the instance of a package onboarded AFTER the GUI was built (so its page is drawn without a GUI rebuild) whose
declaration has buttons, and the instance of a package whose declaration is `readOnly`. The stub rApp (`gui_rapp_stub.py serve`) answers the operator API of both.

What it checks, with real clicks, as the operator and as the viewer:

  - the directory lists both instances, finds one by a search, marks both as declaring a page, and a row opens `/rapps/<instance>`;
  - the declared page draws its panels (key/value, table with a sparkline per row, the closed-loop buttons), the row drawer opens with its chart, JSON and a history table fetched for the open row;
  - the operator's click on a button and on a row action reaches the rApp (the stub's call log), the row action carries the signed-in user, and the table shows the result;
  - the viewer sees the same panels and NO change button (and is told why); calls made by hand with the viewer's session are refused `FORBIDDEN`, a route the declaration does not list is refused
    `UNDECLARED_ROUTE` for everyone (also another instance's id in the path), and a change on the read-only declaration is refused `RAPP_READ_ONLY` even for the operator;
  - the rApp's address is in no answer the browser got; a pin shows in the sidebar and survives a reload;
  - `axe-core` finds no serious or critical WCAG 2 A/AA violation on the page that `security/gui-axe-baseline.json` does not accept.

Exit 0 only when every check passes; each failure is printed as `FAIL: ...`. Screenshots are kept in `--out`.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

SMO = Path(__file__).resolve().parent.parent
BAD_API_STATUS = (401, 500, 501, 502, 503, 504)
OWN_API = ("/api/rapps", "/api/me/pins", "/api/smo/rapp-mgmt", "/api/smo/onboarding")      # what these pages ask for; every other page's calls are scripts/gui_e2e.py's to check
WRITABLE_NAME = "EnergySaving_rApp"
READ_ONLY_NAME = "EnergySaving_rApp_ReadOnly"


def _load(name: str):
    """Loads a sibling script (`scripts/<name>.py`) by file path and returns it as a module; the scripts directory is not a package, so it cannot be imported by name."""
    spec = importlib.util.spec_from_file_location(name, SMO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Check:
    """Collects the failed expectations of one run. A passed expectation is printed as `ok: ...`; a failed one is only recorded, so the run goes on and reports every problem at the end."""
    def __init__(self) -> None:
        self.problems: list[str] = []

    def expect(self, condition: bool, message: str) -> None:
        """Records `message` as a problem when `condition` is false, otherwise prints it as `ok: <message>`. Never raises."""
        if not condition:
            self.problems.append(message)
        else:
            print(f"ok: {message}")


def wait_for_count(page, selector: str, accept, seconds: float = 15.0) -> None:
    """Poll a locator's count from Python. `page.wait_for_function` evaluates a string in the page, which the GUI's Content-Security-Policy
    (no `unsafe-eval`) forbids; counting through the locator does not need the page to evaluate anything."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if accept(page.locator(selector).count()):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"{selector!r}: the count never became what the page needed (last {page.locator(selector).count()})")


def sign_in(page, base_url: str, user: str, password: str) -> None:
    """Signs in through the login form and waits for the dashboard's "Modules healthy" tile, which is what proves the session works."""
    page.goto(f"{base_url}/login")
    page.get_by_label("Username").fill(user)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.get_by_text("Modules healthy").wait_for()


def csrf_of(context) -> str:
    """The value of the `smo_csrf` cookie of a browser context, or "" when there is none (the GUI wants it echoed in `X-CSRF-Token` on every write)."""
    return {c["name"]: c["value"] for c in context.cookies()}.get("smo_csrf", "")


def by_hand(context, base_url: str, method: str, path: str, body: dict | None = None):
    """Calls `/api<path>` of the GUI with the context's session cookies and no page involved: a GET, or any other method with a JSON body and the CSRF header.

        Used to prove what the backend refuses whatever the page shows; returns Playwright's response without raising on a 4xx or 5xx.
    """
    headers = {"X-CSRF-Token": csrf_of(context)}
    url = f"{base_url}/api{path}"
    if method == "GET":
        return context.request.get(url)
    return context.request.fetch(url, method=method, data=json.dumps(body or {}), headers={**headers, "Content-Type": "application/json"})


def stub_calls(command: str) -> list[dict]:
    """Runs `command` in a shell and returns the stub rApp's call log (the JSON it prints). Exits the whole check with a message when the command fails or takes over 60 s.

        The command comes from the person running the check (`--calls-command`), which is why the `shell=True` is accepted.
    """
    done = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)      # noqa: S602 (a command the person running the check gave)
    if done.returncode:
        raise SystemExit(f"--calls-command failed: {done.stderr[-300:]}")
    return json.loads(done.stdout)


def open_page(page, base_url: str, instance: str, answers: list[str]) -> None:
    """Opens `/rapps/<instance>` and waits until the declared page has drawn ("Platform overview" is its first panel); then empties `answers`.

        The clearing means the caller's list of rApp-bound response URLs holds only what the page does afterwards, not what loading it fetched.
    """
    page.goto(f"{base_url}/rapps/{instance}")
    page.get_by_role("heading", name=re.compile("EnergySaving_rApp")).first.wait_for()
    page.get_by_text("Platform overview").wait_for()
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    answers.clear()


def operator_run(browser, base_url: str, user: str, password: str, ids: dict, calls_command: str, check: Check, out: Path, axe_source: str, accessibility: dict) -> None:
    """The operator's half of the check: the rApp directory, the declared page, the row drawer, the button and row-action clicks, what the browser was never told,
        the pin, and the read-only declaration. Failed expectations go to `check`, axe findings to `accessibility`; screenshots go to `out`.

        `calls_command` prints the stub's call log, which is how a click is proved to have reached the rApp with the signed-in user filled in. Backend answers in
        `BAD_API_STATUS` for the calls these pages make (`OWN_API`) are added to the problems at the end. The browser context is closed on a normal return only.
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(30_000)
    failures: list[str] = []
    bodies: list[str] = []
    page.on("response", lambda r: failures.append(f"{r.status} {r.request.method} {r.url.replace(base_url, '')}")
            if r.status in BAD_API_STATUS and any(part in r.url for part in OWN_API) else None)
    page.on("response", lambda r: bodies.append(r.url) if "/api/rapps" in r.url else None)
    sign_in(page, base_url, user, password)
    failures.clear()                                                          # the 401 of /api/me before the sign-in is the sign-in page asking who you are

    page.goto(f"{base_url}/rapps")
    page.get_by_text("rApp directory").wait_for()
    wait_for_count(page, "table tbody tr", lambda n: n >= 2)
    names = page.locator("table tbody tr td:first-child a").all_inner_texts()
    check.expect(any(n.startswith(READ_ONLY_NAME) for n in names) and any(n.startswith(WRITABLE_NAME) and READ_ONLY_NAME not in n for n in names),
                 f"the directory lists the package onboarded at run time and the read-only one ({names})")
    check.expect(page.get_by_text("declared", exact=True).count() >= 2, "the directory marks both rApps as declaring a page")
    page.get_by_label("Search rApps").fill("ReadOnly")
    wait_for_count(page, "table tbody tr td:first-child a", lambda n: n == 1)
    check.expect(page.locator("table tbody tr td:first-child a").all_inner_texts() == [READ_ONLY_NAME], "a search narrows the directory to the one rApp it names")
    page.screenshot(path=str(out / "directory-search.png"))
    page.get_by_label("Search rApps").fill("")
    page.get_by_role("link", name=re.compile(rf"^{WRITABLE_NAME}\b(?!_)")).first.click()
    page.wait_for_url(re.compile(r"/rapps/[0-9a-f-]{36}$"))
    check.expect(page.url.endswith(ids["writable"]), "a directory row opens /rapps/<instance>")

    open_page(page, base_url, ids["writable"], bodies)
    for title in ("Instance", "Closed loop", "Cells"):
        check.expect(page.get_by_text(title, exact=True).count() >= 1, f"the declared page draws the panel '{title}'")
    check.expect(page.get_by_text("gnb-du-e2e-01").count() >= 1, "the key/value panel shows what the rApp answered")
    rows = page.locator("table tbody tr")
    check.expect(rows.filter(has_text="101").count() == 1 and rows.filter(has_text="102").count() == 1, "the table shows both cells the rApp reported")
    check.expect(page.locator("table svg").count() >= 2, "a sparkline is drawn in each row")
    check.expect(page.get_by_role("button", name="Evaluate now").count() == 1 and page.get_by_role("button", name="Override: unlock").count() == 1
                 and page.get_by_role("button", name="Clear override").count() == 1, "the operator sees the buttons, and each row only the action its `when` allows")
    page.screenshot(path=str(out / "operator-page.png"), full_page=True)
    accessibility.update(_load("gui_e2e").axe_violations(page, axe_source, "/rapps/{instance}"))

    rows.filter(has_text="101").click()
    drawer = page.locator("[role=dialog], aside, .drawer").filter(has_text="Cell 101").first
    drawer.wait_for()
    check.expect(drawer.locator("svg").count() >= 1 and drawer.get_by_text("Latest execution (audit trail)").count() == 1, "the row drawer draws the chart and the JSON block")
    drawer.get_by_text("History").wait_for()
    drawer.locator("table tbody tr").first.wait_for()
    check.expect(drawer.locator("table tbody tr").count() == 3, "the drawer's history table is fetched for the open row (3 rows)")
    page.screenshot(path=str(out / "operator-drawer.png"), full_page=True)
    page.keyboard.press("Escape")
    if drawer.is_visible():
        drawer.get_by_role("button", name=re.compile("close", re.I)).first.click()

    page.get_by_role("button", name="Evaluate now").click()
    page.get_by_text("Closed-loop pass complete").wait_for()
    check.expect(True, "the 'Evaluate now' click is answered with the declared success text")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Override: unlock").click()
    page.get_by_text("Cell unlocked by operator").wait_for()
    page.get_by_role("button", name="Clear override").nth(1).wait_for()
    check.expect(page.get_by_role("button", name="Clear override").count() == 2 and page.get_by_role("button", name="Override: unlock").count() == 0,
                 "after the row action the table is read again and both cells show 'Clear override'")

    calls = stub_calls(calls_command)
    evaluate = [c for c in calls if c["method"] == "POST" and c["path"].endswith("/evaluate")]
    override = [c for c in calls if c["method"] == "POST" and c["path"].endswith("/cells/101/override")]
    check.expect(len(evaluate) == 1, "the rApp received the evaluate call")
    check.expect(len(override) == 1 and isinstance(override[0]["body"], dict) and override[0]["body"].get("operator") == user,
                 f"the rApp received the override with the signed-in user filled in ({override[0]['body'] if override else None})")

    check.expect(not any("8898" in u or "r1-termination" in u for u in bodies), "no URL of the browser's calls names the rApp's address")
    api = by_hand(context, base_url, "GET", f"/rapps/{ids['writable']}")
    check.expect(api.ok and "8898" not in api.text() and "operatorApiBase" not in api.text(), "the detail answer does not carry the rApp's address")
    undeclared = by_hand(context, base_url, "GET", f"/rapps/{ids['writable']}/operator/admin/secrets")
    check.expect(undeclared.status == 403 and "UNDECLARED_ROUTE" in undeclared.text(), f"a route the declaration does not list is refused ({undeclared.status})")
    other = by_hand(context, base_url, "GET", f"/rapps/{ids['writable']}/operator/instances/{ids['readOnly']}")
    check.expect(other.status == 403 and "UNDECLARED_ROUTE" in other.text(), f"another instance's id in the path is refused ({other.status})")
    changed = by_hand(context, base_url, "POST", f"/rapps/{ids['readOnly']}/operator/instances/{ids['readOnly']}/evaluate")
    check.expect(changed.status == 403 and "RAPP_READ_ONLY" in changed.text(), f"a change on the read-only declaration is refused for the operator ({changed.status})")

    if page.get_by_role("button", name=re.compile("Pinned")).count():              # a run on a stack that was used before: start from "not pinned"
        page.get_by_role("button", name=re.compile("Pinned")).first.click()
        page.get_by_role("button", name=re.compile("^☆ Pin")).first.wait_for()
    page.get_by_role("button", name=re.compile("^☆ Pin")).first.click()
    page.get_by_role("button", name=re.compile("Pinned")).first.wait_for()
    sidebar = page.locator("nav[aria-label='Main']")
    try:
        sidebar.get_by_text(WRITABLE_NAME).first.wait_for(timeout=10_000)
    except PlaywrightTimeout:
        pass
    check.expect(sidebar.get_by_text(WRITABLE_NAME).count() >= 1, "a pinned rApp is in the sidebar")
    page.reload()
    page.get_by_role("button", name=re.compile("Pinned")).first.wait_for()
    check.expect(page.locator("nav[aria-label='Main']").get_by_text(WRITABLE_NAME).count() >= 1, "the pin survives a reload")

    page.goto(f"{base_url}/rapps/{ids['readOnly']}")
    page.get_by_text("Platform overview").wait_for()
    page.get_by_text("gnb-du-e2e-01").first.wait_for()
    check.expect(page.get_by_role("button", name="Evaluate now").count() == 0, "the read-only declaration is drawn, without a change button")
    page.screenshot(path=str(out / "operator-readonly-page.png"), full_page=True)
    for failure in failures:
        check.problems.append(f"the backend answered {failure}")
    context.close()


def viewer_run(browser, base_url: str, password: str, ids: dict, check: Check, out: Path) -> None:
    """The viewer's half of the check: the same panels with no change button and a reason, a change made by hand refused `FORBIDDEN`, a read allowed, an undeclared route refused."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(30_000)
    sign_in(page, base_url, "viewer", password)
    open_page(page, base_url, ids["writable"], [])
    check.expect(page.get_by_text("gnb-du-e2e-01").count() >= 1 and page.locator("table tbody tr").filter(has_text="101").count() == 1, "the viewer sees the panels and the data")
    check.expect(page.get_by_role("button", name="Evaluate now").count() == 0 and page.get_by_role("button", name="Override: unlock").count() == 0
                 and page.get_by_role("button", name="Clear override").count() == 0, "the viewer sees no change button")
    check.expect(page.get_by_text(re.compile("Changing needs the operator role")).count() == 1, "the viewer is told why")
    page.screenshot(path=str(out / "viewer-page.png"), full_page=True)
    refused = by_hand(context, base_url, "POST", f"/rapps/{ids['writable']}/operator/instances/{ids['writable']}/evaluate")
    check.expect(refused.status == 403 and "FORBIDDEN" in refused.text(), f"a change made by hand with the viewer's session is refused ({refused.status})")
    read = by_hand(context, base_url, "GET", f"/rapps/{ids['writable']}/operator/instances/{ids['writable']}/dashboard?points=48")
    check.expect(read.ok and "cells" in read.text(), f"the viewer's session can read a declared route ({read.status})")
    undeclared = by_hand(context, base_url, "GET", f"/rapps/{ids['writable']}/operator/nothing")
    check.expect(undeclared.status == 403 and "UNDECLARED_ROUTE" in undeclared.text(), f"an undeclared route is refused for the viewer too ({undeclared.status})")
    context.close()


def run(args) -> int:
    """Runs the operator and viewer checks in one Chromium and returns the exit code (0 when no problem and no NEW accessibility finding).

        Needs `GUI_E2E_OPERATOR_PASSWORD` and `GUI_E2E_VIEWER_PASSWORD` in the environment (`main` checks). Accessibility findings are compared with the baseline
        through `dast_baseline.compare`; a STALE entry is counted but does not fail the run.
    """
    ids = json.loads(Path(args.instances).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    check = Check()
    accessibility: dict[str, str] = {}
    axe_source = Path(args.axe).read_text()
    baseline = {k: v for k, v in json.loads(Path(args.baseline).read_text()).items() if not k.startswith("_")}
    with sync_playwright() as pw:
        launch = {"executable_path": args.chromium} if args.chromium else {}
        if args.chromium_args:
            launch["args"] = args.chromium_args.split()
        browser = pw.chromium.launch(**launch)
        try:
            operator_run(browser, args.base_url.rstrip("/"), "operator", os.environ["GUI_E2E_OPERATOR_PASSWORD"], ids, args.calls_command, check, out, axe_source, accessibility)
            viewer_run(browser, args.base_url.rstrip("/"), os.environ["GUI_E2E_VIEWER_PASSWORD"], ids, check, out)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            check.problems.append(f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
        finally:
            browser.close()
    new, stale = _load("dast_baseline").compare(accessibility, baseline)
    print(f"accessibility: {len(accessibility)} serious or critical finding(s), {len(new)} new, {len(stale)} stale")
    check.problems += [f"NEW {key} ({detail})" for key, detail in sorted(new.items())]
    for problem in check.problems:
        print("FAIL:", problem, file=sys.stderr)
    return 1 if check.problems else 0


def main() -> int:
    """Command-line entry: exit 2 when a password variable is unset, otherwise `run`'s exit code."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://localhost:3000")
    ap.add_argument("--instances", required=True, help="the JSON that `gui_rapp_stub.py setup` printed")
    ap.add_argument("--calls-command", required=True, help="a shell command that prints the stub's GET /_calls")
    ap.add_argument("--out", default="gui-rapp-pages")
    ap.add_argument("--axe", default=str(SMO / "gui" / "node_modules" / "axe-core" / "axe.min.js"))
    ap.add_argument("--baseline", default=str(SMO / "security" / "gui-axe-baseline.json"))
    ap.add_argument("--chromium", default=os.environ.get("GUI_E2E_CHROMIUM"), help="path to a Chromium binary (default: Playwright's own)")
    ap.add_argument("--chromium-args", default=os.environ.get("GUI_E2E_CHROMIUM_ARGS", ""), help="extra Chromium arguments, space separated (a sandbox-less container needs --no-sandbox)")
    args = ap.parse_args()
    for name in ("GUI_E2E_OPERATOR_PASSWORD", "GUI_E2E_VIEWER_PASSWORD"):
        if not os.environ.get(name):
            print(f"{name} is not set", file=sys.stderr)
            return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
