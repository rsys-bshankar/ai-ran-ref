/** A segmented control (`.seg`): time range, autonomy mode, grouped/flat. Rendered as a radio group so the arrow-free
 * click model still reads correctly to assistive technology; `disabled` greys every option (a viewer sees the mode but cannot change it). */
import type { ReactNode } from "react";

/** The control; `onChange` is not called for the option already selected. */
export function Segmented<T extends string>({ options, value, onChange, label, disabled }: {
  options: { id: T; label: ReactNode; title?: string }[]; value: T; onChange: (v: T) => void; label: string; disabled?: boolean;
}) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" role="radio" aria-checked={value === o.id} className={value === o.id ? "on" : ""}
          disabled={disabled} title={o.title} onClick={() => value !== o.id && onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
