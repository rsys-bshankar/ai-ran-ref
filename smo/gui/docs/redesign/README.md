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
| Shell (BRIEF §2) | `src/shell/`: `Layout`, `Sidebar` (grouped nav, SVG icons, summary badges, pinned rApps, user card), `TopBar` (breadcrumb, ⌘K search over pages and `/api/search`, live chip, notifications, preferences, help), `LiveEvents` (pushed summaries), `nav.ts` (the one navigation table) |
| Shared primitives (BRIEF §3) | `src/kit/` (one file each) and `src/components/ui.tsx` / `charts.tsx` (restyled, extended) |
| Preferences (BRIEF §4d) | `src/pages/preferences/`, `src/shell/ThemeProvider.tsx`, `src/data/preferences.ts`, `public/theme-boot.js` (no flash of the wrong theme); stored by the BFF (`GET`/`PUT /api/me/preferences`) |
| Server tables (SCALE P1) | `src/kit/ServerTable.tsx` + `Pager.tsx`: the backend pages and counts, "Showing 1–50 of N"; `src/kit/KeysetTable.tsx` for cursor-paged routes (alarms) |
| Summary counts (SCALE P2) | `src/data/summary.ts` over the BFF's `GET /api/summary/{page}` (true totals, 5 s shared cache) |
| Pushed updates (SCALE P7) | `src/data/events.ts` (rules) and `src/shell/LiveEvents.tsx` (one `EventSource` per tab on `GET /api/events`): pushed summaries land in the summary cache and refetch the affected lists |
| Targeted invalidation (SCALE P8) | `src/data/keys.ts`, used by `useSmoAction` |
| Explicit states (SCALE P10) | `src/kit/states.tsx`, `src/kit/SectionBoundary.tsx` |
| One folder per page (STRUCTURE) | `src/pages/<page>/` with `index.tsx`, `sections/`, `data/queries.ts`, `README.md` (the page's maintenance sheet), `__tests__/` |

## What was not built, and why

The back-end asks of SCALE.md §5 and the data gaps of BRIEF §5 are recorded in `smo/OPEN_ITEMS.md` (`PR-GUI-9`). PR-GUI-9 filled most of them, and
the console uses them: the summary counts are pushed over Server-Sent Events (`src/data/events.ts`, `src/shell/LiveEvents.tsx`; the top bar says
"Live · pushed"), the ⌘K box searches elements, rApps, alarms, models and decisions on the server, and the pages show the network health score,
health map and worst elements, mean time to acknowledge, alarm correlation and group counts, the one-call global stop, per-rApp headline KPIs,
training epoch / ETA, intent fulfilment percent and conflict counts, DME late detection, sign-in history, recovery-code slots and user last-active,
and export the decision records and the audit log as CSV. What is still missing: a global scope picker, async (job-based) exports, node
utilisation (FOCOM collects no CPU or memory measurement), per-cell rApp state history (the rApp's own data), and a server "attention" aggregate for
the Dashboard. Until each is filled, the widget that needs it is hidden or shows "—", and the page README's "Known limits" names it.
