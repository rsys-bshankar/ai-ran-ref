# Preferences

Route: `/preferences`    Design: handoff `Preferences.dc.html` (BRIEF §4d)

## Sections

| id | file | what it shows | API | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| preferences.theme | sections/ThemePicker.tsx | dark / light / system | — (draft) | — | 0 |
| preferences.accent | sections/AccentPicker.tsx | five accents | — (draft) | — | 0 |
| preferences.size | sections/TextSize.tsx | s / m / l / xl root text size | — (draft) | — | 0 |
| preferences.defaults | sections/DisplayDefaults.tsx | start page, rows per page, time zone, clock, motion, sound | — (draft) | — | 0 |
| preferences.preview | sections/LivePreview.tsx | sample tiles, chips, buttons | — | — | 0 |

The page reads the saved preferences from `shell/ThemeProvider.tsx` (`GET /api/me/preferences`, one call per session, shared by every page) and
writes them with `PUT /api/me/preferences` (gui-bff/app/preferences.py).

## Known limits

- The alarm sound preference is stored, but no page plays a sound yet: it needs the pushed alarm stream (SCALE.md P7).
- "Network local" time zone from the mockup is not offered: no backend serves the network's time zone.

## Troubleshooting

- A change shows here but not on other pages: it is a preview until **Save preferences**.
- The first paint after sign-in is in the old theme: the browser copy (`localStorage["smo.prefs"]`) was cleared; the server copy applies a moment later.
- Save answers 422: a value outside its set (gui-bff validates every field).
