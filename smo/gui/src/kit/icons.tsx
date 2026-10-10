/** The console's inline stroke icons (24 px view box, 1.7 stroke), drawn from the path data of the redesign's sidebar and top bar
 * (handoff `Sidebar.dc.html`, `Topbar.dc.html`). They replace the Unicode glyphs the pre-redesign sidebar used, which rendered differently per
 * font. Decorative by default (`aria-hidden`); a button that holds only an icon carries its own `aria-label`. */

/** The path data of each icon, by name. */
export const ICONS = {
  dashboard: "M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z",
  flows: "M4 7h11M11 3l4 4-4 4M20 17H9M13 13l-4 4 4 4",
  rapps: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM16.5 13v7M13 16.5h7",
  aiml: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5",
  intents: "M12 3v18M8 21h8M5 7h14M5 7l-2.5 6h5zM19 7l-2.5 6h5z",
  approvals: "M9 11l3 3 8-8M20 12v7a1 1 0 01-1 1H5a1 1 0 01-1-1V5a1 1 0 011-1h10",
  decisions: "M6 3h12v18H6zM9 7h6M9 11h6M9 15h3",
  safeguards: "M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6zM9 12l2 2 4-4",
  alarms: "M6 16v-5a6 6 0 0112 0v5l2 2H4zM10 21h4",
  kpis: "M3 17l5-6 4 3 5-7 4 4M3 21h18",
  infra: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
  data: "M7 4v16M4 17l3 3 3-3M17 20V4M14 7l3-3 3 3",
  security: "M6 11h12v9H6zM9 11V8a3 3 0 016 0v3",
  admin: "M12 9a3 3 0 100 6 3 3 0 000-6zM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9L7 7M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1",
  topology: "M5 5h4v4H5zM15 5h4v4h-4zM10 15h4v4h-4zM7 9l4 6M17 9l-4 6M9 7h6",
  config: "M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4",
  software: "M12 3v12M7 10l5 5 5-5M5 21h14",
  prefs: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M14 4v4M8 10v4M16 16v4",
  search: "M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4-4",
  bell: "M6 16v-5a6 6 0 0112 0v5l2 2H4zM10 21h4",
  moon: "M12 3a9 9 0 109 9 7 7 0 01-9-9z",
  help: "M12 3a9 9 0 100 18 9 9 0 000-18zM9.5 9.5a2.5 2.5 0 114 2c-.9.6-1.5 1.1-1.5 2.5M12 17h.01",
  signout: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10",
  pin: "M9 4h6l-1 6 3 3H7l3-3zM12 13v7",
  stop: "M7 7h10v10H7z",
  play: "M8 5l11 7-11 7z",
  back: "M15 6l-6 6 6 6",
  chevron: "M6 9l6 6 6-6",
  close: "M6 6l12 12M18 6L6 18",
  warn: "M12 3l9 16H3zM12 10v4M12 17h.01",
  check: "M5 12l5 5 9-10",
  location: "M12 21s-7-6.2-7-11a7 7 0 0114 0c0 4.8-7 11-7 11zM12 8a2 2 0 100 4 2 2 0 000-4z",
  download: "M12 4v11M7 10l5 5 5-5M5 20h14",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 12a8 8 0 11-2.3-5.7M20 4v5h-5",
} as const;

/** An icon's name. */
export type IconName = keyof typeof ICONS;

/** One icon; `size` in px. */
export function Icon({ name, size = 18, strokeWidth = 1.7, title }: { name: IconName; size?: number; strokeWidth?: number; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round"
      strokeLinejoin="round" aria-hidden={title ? undefined : true} role={title ? "img" : undefined} aria-label={title}>
      <path d={ICONS[name]} />
    </svg>
  );
}
