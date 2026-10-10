# GUI redesign ("Signal")

This folder is the design hand-off the console redesign was built from, kept with the code so a maintainer can check a page against its design:

| File | What it is |
| --- | --- |
| [`BRIEF.md`](BRIEF.md) | What to build: tokens and theme, shell, shared primitives, every page, the lifecycle-flow boards, preferences, the backend capabilities brought into the GUI, and the data gaps |
| [`SCALE.md`](SCALE.md) | The scale and latency checklist: design targets, what broke at scale, the patterns P1–P12, the per-page call budgets, the back-end asks |
| [`STRUCTURE.md`](STRUCTURE.md) | One folder per page, one file per section, the page README template, and the migration map |
| [`designs/*.dc.html`](designs/) | One static mockup per page (layout and copy; their numbers are sample data). `designs/console.css` is the stylesheet they share and the source of `src/styles.css` |

## How the code maps onto it

| Design | Code |
| --- | --- |
| Tokens, both themes, five accents, text size (BRIEF §1, §4d) | `src/styles.css` (`:root[data-theme]`, `:root[data-accent]`, `:root[data-size]`); fonts self-hosted from `@fontsource/*` (the CSP allows fonts from `'self'` only) |
| Shell (BRIEF §2) | `src/shell/`: `Layout`, `Sidebar` (grouped nav, SVG icons, summary badges, pinned rApps, user card), `TopBar` (breadcrumb, ⌘K jump search, notifications, preferences, help), `nav.ts` (the one navigation table) |
| Shared primitives (BRIEF §3) | `src/kit/` (one file each) and `src/components/ui.tsx` / `charts.tsx` (restyled, extended) |
| Preferences (BRIEF §4d) | `src/pages/preferences/`, `src/shell/ThemeProvider.tsx`, `src/data/preferences.ts`, `public/theme-boot.js` (no flash of the wrong theme); stored by the BFF (`GET`/`PUT /api/me/preferences`) |
| Server tables (SCALE P1) | `src/kit/ServerTable.tsx` + `Pager.tsx`: the backend pages and counts, "Showing 1–50 of N" |
| Summary counts (SCALE P2) | `src/data/summary.ts` over the BFF's `GET /api/summary/{page}` (true totals, 5 s shared cache) |
| Targeted invalidation (SCALE P8) | `src/data/keys.ts`, used by `useSmoAction` |
| Explicit states (SCALE P10) | `src/kit/states.tsx`, `src/kit/SectionBoundary.tsx` |
| One folder per page (STRUCTURE) | `src/pages/<page>/` with `index.tsx`, `sections/`, `data/queries.ts`, `README.md` (the page's maintenance sheet), `__tests__/` |

## What was not built, and why

The back-end asks of SCALE.md §5 and the data gaps of BRIEF §5 are recorded in `smo/OPEN_ITEMS.md` (`PR-GUI-9`). Until each is filled, the
widget that needs it is hidden or shows "—", and the page README's "Known limits" names it. In short: no SSE event stream (pages poll, hidden
tabs do not), no server-side cross-object search (the ⌘K box is a client-side jump list), no global scope picker, no async CSV export, no global
safeguard stop route, and no network health score, mean time to acknowledge, alarm correlation, per-rApp headline KPI, cell-state history,
training epoch/ETA, intent fulfilment percent, node utilisation, DME late detection, sign-in history or user last-active.
