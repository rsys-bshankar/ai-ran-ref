#!/usr/bin/env python3
"""Browser check of every page of the operator GUI against a running stack (PR-V-13): sign in as the admin, open each page, and check that

  - the page renders (a heading shows) and none of its calls to the backend for frontend answers 5xx or 401;
  - `axe-core` finds no serious or critical accessibility violation (WCAG 2 A and AA rules) that `security/gui-axe-baseline.json` does not accept;
  - the same holds for `THEME_PAGES` in both themes and with every accent colour (the redesign's preferences, BRIEF §4d): the theme and the accent are
    put on `<html>` as the console's ThemeProvider does, without saving anything to the user's preferences, and each finding is keyed
    `"<route> [<theme>/<accent>] <rule id>"`;
  - a screenshot is kept, so the artifact shows what each page looked like.

    GUI_E2E_PASSWORD=... scripts/gui_e2e.py [--base-url http://localhost:3000] [--out gui-e2e] [--axe gui/node_modules/axe-core/axe.min.js]

The accessibility baseline is `{"<route> <rule id>": "why it is accepted"}` (`scripts/dast_baseline.py` has the matching rules, wildcards included); a finding is reported as
`NEW <route> <rule id> (<what it says>, <how many elements>, <first selector>)`. Exit 0 only when every page passes. The page list is checked against the router
(`tests_integration/test_gui_e2e_routes.py`), so a page added to the GUI is not left out.
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

SMO = Path(__file__).resolve().parent.parent
PAGES = ["/", "/flows", "/rapps", "/safeguards", "/approvals", "/decisions", "/aiml", "/alarms", "/kpis", "/policy",
         "/topology", "/configuration", "/software", "/infrastructure", "/data", "/preferences", "/security", "/admin"]
# The pages checked again in every theme × accent: the ones with the most kinds of text-on-colour (tiles, badges, severity chips, buttons, links).
THEME_PAGES = ["/", "/alarms", "/approvals", "/preferences"]
THEMES = ["dark", "light"]
ACCENTS = ["volt", "blue", "teal", "amber", "radisys"]
LOGIN = "/login"
BAD_API_STATUS = (401, 500, 501, 502, 503, 504)
WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
BLOCKING_IMPACT = ("serious", "critical")


def _dast_baseline():
    """Loads `scripts/dast_baseline.py` by file path and returns it as a module (the scripts directory is not a package, so it cannot be imported by name)."""
    spec = importlib.util.spec_from_file_location("dast_baseline", SMO / "scripts" / "dast_baseline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def axe_violations(page, axe_source: str, route: str) -> dict[str, str]:
    """The serious and critical axe-core violations on the page now, as `{"<route> <rule id>": "<help text>, <n> element(s), first <selector>"}`.

        Injects axe into the page, runs only the WCAG 2 A/AA tags, and keeps only the impacts in `BLOCKING_IMPACT`; minor and moderate findings are ignored.
    """
    page.evaluate(axe_source)
    result = page.evaluate("axe.run(document, {runOnly: {type: 'tag', values: %s}})" % json.dumps(WCAG_TAGS))
    found = {}
    for violation in result["violations"]:
        if violation.get("impact") in BLOCKING_IMPACT:
            nodes = violation.get("nodes", [])
            first = (nodes[0].get("target") or ["?"])[0] if nodes else "?"
            found[f"{route} {violation['id']}"] = f"{violation.get('help', '')}, {len(nodes)} element(s), first {first}"
    return found


def slug(route: str) -> str:
    """The file-name stem for a route's screenshot: `home` for `/`, otherwise the path with `/` turned into `-`."""
    return "home" if route == "/" else route.strip("/").replace("/", "-")


def theme_violations(page, axe_source: str, route: str) -> dict[str, str]:
    """The serious and critical axe findings of the page open at `route` in every theme × accent, keyed `"<route> [<theme>/<accent>] <rule id>"`.

        Sets `data-theme` and `data-accent` on `<html>` directly (what the console's ThemeProvider does), so the user's saved preferences are untouched;
        restores the dark theme with the default accent afterwards.
    """
    found: dict[str, str] = {}
    for theme in THEMES:
        for accent in ACCENTS:
            page.evaluate("([t, a]) => { document.documentElement.dataset.theme = t; document.documentElement.dataset.accent = a; }", [theme, accent])
            page.wait_for_timeout(50)                           # let the style recalculation land before axe reads computed colours
            found.update(axe_violations(page, axe_source, f"{route} [{theme}/{accent}]"))
    page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; document.documentElement.dataset.accent = 'volt'; }")
    return found


def run(base_url: str, out: Path, user: str, password: str, axe_path: Path, baseline: dict[str, str], chromium: str | None) -> int:
    """Signs in once, opens every route in `PAGES`, and returns the exit code (0 only when nothing was reported).

        Per page it records backend answers in `BAD_API_STATUS` for URLs containing `/api/`, uncaught script errors, and axe findings; the findings are then compared
        with `baseline` through `dast_baseline.compare`, so only NEW ones fail and STALE ones are printed. A Playwright error ends the walk (the problem is recorded
        and a `failure.png` taken); the browser is always closed. Writes screenshots under `out`; prints one line per page and the problems to stderr.
    """
    out.mkdir(parents=True, exist_ok=True)
    axe_source = axe_path.read_text()
    dast = _dast_baseline()
    problems: list[str] = []
    accessibility: dict[str, str] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium) if chromium else pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(30_000)
        api_failures: list[str] = []
        page.on("response", lambda r: api_failures.append(f"{r.status} {r.request.method} {r.url.replace(base_url, '')}")
                if "/api/" in r.url and r.status in BAD_API_STATUS else None)
        page_errors: list[str] = []
        page.on("pageerror", lambda e: page_errors.append(str(e).splitlines()[0]))
        try:
            page.goto(f"{base_url}{LOGIN}")
            page.get_by_label("Username").wait_for()
            accessibility.update(axe_violations(page, axe_source, LOGIN))
            page.screenshot(path=str(out / "login.png"))
            page.get_by_label("Username").fill(user)
            page.get_by_label("Password").fill(password)
            page.get_by_role("button", name="Sign in").click()
            page.get_by_role("navigation", name="Main").wait_for()        # the sidebar: signed in
            for route in PAGES:
                before = len(api_failures), len(page_errors)
                page.goto(f"{base_url}{route}")
                page.get_by_role("heading").first.wait_for()
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass                                     # a page that polls never goes idle; the heading is what says it rendered
                page.screenshot(path=str(out / f"{slug(route)}.png"), full_page=True)
                accessibility.update(axe_violations(page, axe_source, route))
                if route in THEME_PAGES:
                    accessibility.update(theme_violations(page, axe_source, route))
                for failure in api_failures[before[0]:]:
                    problems.append(f"{route}: the backend answered {failure}")
                for error in page_errors[before[1]:]:
                    problems.append(f"{route}: script error: {error}")
                print(f"{route}: opened")
        except (PlaywrightTimeout, PlaywrightError) as exc:
            problems.append(f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
            page.screenshot(path=str(out / "failure.png"), full_page=True)
        finally:
            browser.close()
    new, stale = dast.compare(accessibility, baseline)
    print(f"accessibility: {len(accessibility)} serious or critical finding(s), {len(accessibility) - len(new)} accepted by the baseline, {len(new)} new, {len(stale)} stale")
    for key, detail in sorted(new.items()):
        problems.append(f"NEW {key} ({detail})")
    for key in stale:
        print(f"STALE {key}")
    for problem in problems:
        print("FAIL:", problem, file=sys.stderr)
    return 1 if problems else 0


def main() -> int:
    """Command-line entry: reads the options, takes the password from `GUI_E2E_PASSWORD` (exit 2 when it is unset) and returns `run`'s exit code.

        Baseline keys starting with `_` are comments in the JSON file and are dropped before the comparison.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://localhost:3000")
    ap.add_argument("--out", default="gui-e2e", help="directory for the screenshots")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--axe", default=str(SMO / "gui" / "node_modules" / "axe-core" / "axe.min.js"))
    ap.add_argument("--baseline", default=str(SMO / "security" / "gui-axe-baseline.json"))
    ap.add_argument("--chromium", default=os.environ.get("GUI_E2E_CHROMIUM"), help="path to a Chromium binary (default: Playwright's own)")
    args = ap.parse_args()
    password = os.environ.get("GUI_E2E_PASSWORD", "")
    if not password:
        print("GUI_E2E_PASSWORD is not set", file=sys.stderr)
        return 2
    baseline = {k: v for k, v in json.loads(Path(args.baseline).read_text()).items() if not k.startswith("_")}
    return run(args.base_url.rstrip("/"), Path(args.out), args.user, password, Path(args.axe), baseline, args.chromium)


if __name__ == "__main__":
    sys.exit(main())
