/** Preferences › Text size (section `preferences.size`): the root font size, so every page's text scales together. */
import { Card } from "../../../components/ui";
import { CHOICES, LABELS, type Preferences } from "../../../data/preferences";
import { Segmented } from "../../../kit/Segmented";

/** The text-size choice, with a sample line to judge it by. */
export function TextSize({ value, onChange }: { value: Preferences["size"]; onChange: (v: Preferences["size"]) => void }) {
  return (
    <Card title="Text size" sub="scales text on every page" section="preferences.size">
      <Segmented label="Text size" value={value} onChange={onChange} options={CHOICES.size.map((s) => ({ id: s, label: LABELS.size[s] }))} />
      <div className="inset"><strong>Loss of signal on 12 DUs in metro-a</strong><div className="small muted">The quick check: can you read this line comfortably from where you sit?</div></div>
    </Card>
  );
}
