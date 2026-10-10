/** The console frame moved to `shell/` in the redesign (STRUCTURE.md §5). This file keeps the old import path working for the pages and tests
 * that still import `Layout`, `NAV` and `PinnedRapps` from here. */
export { Layout } from "../shell/Layout";
export { PinnedRapps } from "../shell/Sidebar";
export { NAV, NAV_GROUPS } from "../shell/nav";
