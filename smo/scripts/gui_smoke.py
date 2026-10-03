#!/usr/bin/env python3
"""Headless smoke check of the operator GUI (PR-OPS-10.3): sign in, look at the module-health dashboard and one more screen, and
save a screenshot of each. Run by the deploy gate (.github/workflows/deploy-on-main.yml) against the compose stack on the GUI port.

    GUI_SMOKE_PASSWORD=... python scripts/gui_smoke.py [--base-url http://localhost:3000] [--out gui-smoke] [--user admin]

Exit 0 only when the login screen renders, the sign-in works and the dashboard reports every module healthy. The screenshots are
written even when a check fails, so the artifact shows what the page looked like.
"""

import argparse
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

HEALTH_WAIT_MS = 60_000          # the dashboard polls; the stack may have only just come up


def run(base_url: str, out: Path, user: str, password: str, require_healthy: bool, chromium: str | None) -> int:
    out.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium) if chromium else pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(20_000)
        try:
            page.goto(f"{base_url}/login")
            page.get_by_text("Operator Console").wait_for()
            page.screenshot(path=str(out / "01-login.png"))

            page.get_by_label("Username").fill(user)
            page.get_by_label("Password").fill(password)
            page.get_by_role("button", name="Sign in").click()
            page.get_by_text("Modules healthy").wait_for()                   # the dashboard's stat tile: sign-in worked

            skip_equal = "false" if require_healthy else "true"          # the label is upper-cased by CSS, hence /i
            try:
                page.wait_for_function(
                    """() => { const m = document.body.innerText.match(/Modules healthy\\s*(\\d+)\\/(\\d+)/i);
                               return m && Number(m[2]) > 0 && (%s || m[1] === m[2]); }""" % skip_equal,
                    timeout=HEALTH_WAIT_MS)
            except PlaywrightTimeout:
                failures.append("the dashboard did not report every module healthy within %d s" % (HEALTH_WAIT_MS // 1000))
            tile = re.search(r"Modules healthy\s*(\d+)/(\d+)", page.inner_text("body"), re.I)
            print("modules healthy: %s" % (f"{tile.group(1)}/{tile.group(2)}" if tile else "not shown"))
            page.screenshot(path=str(out / "02-module-status.png"), full_page=True)

            page.goto(f"{base_url}/alarms")
            page.get_by_role("heading").first.wait_for()
            page.screenshot(path=str(out / "03-alarms.png"), full_page=True)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            failures.append(f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
            page.screenshot(path=str(out / "99-failure.png"), full_page=True)
        finally:
            browser.close()
    for failure in failures:
        print("FAIL:", failure, file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://localhost:3000")
    ap.add_argument("--out", default="gui-smoke", help="directory for the screenshots")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--allow-unhealthy", action="store_true", help="do not require every module healthy (for a GUI run without the stack)")
    ap.add_argument("--chromium", default=os.environ.get("GUI_SMOKE_CHROMIUM"), help="path to a Chromium binary (default: Playwright's own)")
    args = ap.parse_args()
    password = os.environ.get("GUI_SMOKE_PASSWORD", "")
    if not password:
        print("GUI_SMOKE_PASSWORD is not set", file=sys.stderr)
        return 2
    return run(args.base_url.rstrip("/"), Path(args.out), args.user, password, not args.allow_unhealthy, args.chromium)


if __name__ == "__main__":
    sys.exit(main())
