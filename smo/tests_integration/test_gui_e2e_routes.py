"""The page list of scripts/gui_e2e.py is the GUI's router (PR-V-13): a page added to the GUI must be added to the browser check, and one removed must go."""

import importlib.util
import re
from pathlib import Path

SMO = Path(__file__).resolve().parent.parent


def _routes_of_the_router() -> set[str]:
    """The page paths the GUI's router declares (read from `gui/src/main.tsx`), plus the index page and without the login page, which the browser
    check covers on its own.
    """
    source = (SMO / "gui" / "src" / "main.tsx").read_text()
    routes = {"/" + path for path in re.findall(r'<Route path="([a-z][a-z0-9-]*)"', source)}
    routes.add("/")                                    # the index route (the dashboard)
    routes.discard("/login")                           # signed out, checked on its own
    return routes


def test_every_page_of_the_router_is_in_the_browser_check():
    """The browser check's page list is exactly the router's pages, with no duplicate, so a page added to the GUI is added to the check and a
    removed one is dropped. Playwright is stubbed because only the list is needed.
    """
    import sys
    sys.modules.setdefault("playwright", type(sys)("playwright"))        # the script imports Playwright at the top; the page list needs none of it
    for name in ("playwright.sync_api",):
        module = type(sys)(name)
        module.Error = module.TimeoutError = Exception
        module.sync_playwright = None
        sys.modules.setdefault(name, module)
    spec = importlib.util.spec_from_file_location("gui_e2e", SMO / "scripts" / "gui_e2e.py")
    gui_e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui_e2e)
    assert set(gui_e2e.PAGES) == _routes_of_the_router()
    assert len(gui_e2e.PAGES) == len(set(gui_e2e.PAGES))
