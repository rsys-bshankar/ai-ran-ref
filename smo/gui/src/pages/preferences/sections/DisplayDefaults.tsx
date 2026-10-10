/** Preferences › Display defaults (section `preferences.defaults`): start page, rows per page, time zone, clock, reduce motion, alarm sound. */
import { Card, Field } from "../../../components/ui";
import { CHOICES, type Preferences } from "../../../data/preferences";
import { NAV } from "../../../shell/nav";

/** The defaults form; every change goes to the page's draft (saved by the page's Save button). */
export function DisplayDefaults({ value, onChange }: { value: Preferences; onChange: (p: Partial<Preferences>) => void }) {
  const label = (to: string) => NAV.find((n) => n.to === to)?.label ?? to;
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return (
    <Card title="Display defaults" sub="used whenever a page opens" section="preferences.defaults">
      <div className="grid g2">
        <Field label="Start page after sign-in">
          <select value={value.startPage} onChange={(e) => onChange({ startPage: e.target.value })}>
            {CHOICES.startPage.map((p) => <option key={p} value={p}>{label(p)}</option>)}
          </select>
        </Field>
        <Field label="Rows per page in tables">
          <select value={value.rowsPerPage} onChange={(e) => onChange({ rowsPerPage: Number(e.target.value) as Preferences["rowsPerPage"] })}>
            {CHOICES.rowsPerPage.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </Field>
        <Field label="Time zone">
          <select value={value.timeZone} onChange={(e) => onChange({ timeZone: e.target.value as Preferences["timeZone"] })}>
            <option value="local">Browser ({zone})</option><option value="UTC">UTC</option>
          </select>
        </Field>
        <Field label="Time format">
          <select value={value.clock} onChange={(e) => onChange({ clock: e.target.value as Preferences["clock"] })}>
            <option value="24h">24-hour (14:05)</option><option value="12h">12-hour (2:05 PM)</option>
          </select>
        </Field>
      </div>
      <label className="check"><input type="checkbox" checked={value.reduceMotion} onChange={(e) => onChange({ reduceMotion: e.target.checked })} /> Reduce motion (no animated charts or transitions)</label>
      <label className="check"><input type="checkbox" checked={value.alarmSound} onChange={(e) => onChange({ alarmSound: e.target.checked })} /> Play a sound for new critical alarms</label>
    </Card>
  );
}
