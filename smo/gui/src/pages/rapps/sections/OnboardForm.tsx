/** "Onboard a package" (call flow 01, first step): posts a CSAR location to Onboarding, which validates it asynchronously; the result shows up
 * as the package state in the packages table. Role-gated by the Packages tab. Section id `rapps.onboard`. */
import { useState, type FormEvent } from "react";

import { useSmoAction } from "../../../api/hooks";
import { Card, Field } from "../../../components/ui";
import { PACKAGES_PATH } from "../data/queries";

/** The onboarding form. */
export function OnboardForm() {
  const [location, setLocation] = useState("");
  const [applicationType, setApplicationType] = useState("rApp");
  const action = useSmoAction();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    action.mutate({ method: "POST", path: PACKAGES_PATH, json: { location, applicationType }, success: "Onboarding accepted — watch the package state" },
      { onSuccess: () => setLocation("") });
  };
  return (
    <Card section="rapps.onboard" title="Onboard a package">
      <form className="form inline" onSubmit={submit}>
        <Field label="CSAR location (URL reachable from the Onboarding service)" hint="Validation runs asynchronously: the result shows up as the package state (AVAILABLE or FAILED).">
          <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="http://r1-termination:8899/energy-saving-rapp.csar" required pattern="https?://.+" />
        </Field>
        <Field label="Application type"><select value={applicationType} onChange={(e) => setApplicationType(e.target.value)}><option>rApp</option></select></Field>
        <button type="submit" className="btn primary" disabled={action.isPending}>Onboard</button>
      </form>
    </Card>
  );
}
