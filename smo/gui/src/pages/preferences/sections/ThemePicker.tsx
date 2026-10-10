/** Preferences › Theme (section `preferences.theme`): dark, light or match the operating system. */
import { Card } from "../../../components/ui";
import { CHOICES, LABELS, type Preferences } from "../../../data/preferences";

const NOTES: Record<Preferences["theme"], string> = {
  dark: "Default. Easier on the eyes in dim rooms.",
  light: "High legibility in bright rooms.",
  system: "Follows your operating system's setting.",
};

/** The theme choice. */
export function ThemePicker({ value, onChange }: { value: Preferences["theme"]; onChange: (v: Preferences["theme"]) => void }) {
  return (
    <Card title="Theme" sub="dark suits a control room; light suits bright offices" section="preferences.theme">
      <div className="swatches" role="radiogroup" aria-label="Theme">
        {CHOICES.theme.map((t) => (
          <button key={t} type="button" role="radio" aria-checked={value === t} className={`swatch${value === t ? " on" : ""}`} onClick={() => onChange(t)} style={{ alignItems: "flex-start", minWidth: 160 }}>
            <strong>{LABELS.theme[t]}{value === t ? " ✓" : ""}</strong>
            <span className="xs muted">{NOTES[t]}</span>
          </button>
        ))}
      </div>
    </Card>
  );
}
