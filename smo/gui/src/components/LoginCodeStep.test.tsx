// @vitest-environment jsdom
/**
 * Component tests of the second sign-in step (components/LoginCodeStep.tsx): when the button is enabled, what is sent, the error and the back button. Mounted in jsdom with testing/dom.tsx,
 * no network. Run: `cd gui && npx vitest run src/components/LoginCodeStep.test.tsx`.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginCodeStep } from "./LoginCodeStep";
import { byText, cleanup, click, mount, submit, type } from "../testing/dom";

afterEach(cleanup);

/**
 * Mounts the step with default props (no error, not busy); `over` replaces props. Returns the two spies and the pending mount.
 */
const render = (over: Partial<Parameters<typeof LoginCodeStep>[0]> = {}) => {
  const onSubmit = vi.fn();
  const onBack = vi.fn();
  return { onSubmit, onBack, mounted: mount(<LoginCodeStep onSubmit={onSubmit} onBack={onBack} error={null} busy={false} {...over} />) };
};

describe("LoginCodeStep", () => {
  // The Verify button stays disabled until the text is a plausible code (six digits, spaces allowed), and the field is marked as a one-time code for password managers.
  it("asks for the code and keeps the button off until it is plausible", async () => {
    const { mounted } = render();
    const { container } = await mounted;
    expect(container.textContent).toMatch(/6-digit code from your authenticator app/);
    const input = container.querySelector("input") as HTMLInputElement;
    expect(input.autocomplete).toBe("one-time-code");
    const verify = byText<HTMLButtonElement>(container, "button", "Verify")!;
    expect(verify.disabled).toBe(true);
    await type(input, "1234");
    expect(verify.disabled).toBe(true);
    await type(input, "123 456");
    expect(verify.disabled).toBe(false);
  });

  // The code is sent without the spaces people type while reading it.
  it("sends the code without the spaces", async () => {
    const { mounted, onSubmit } = render();
    const { container } = await mounted;
    await type(container.querySelector("input") as HTMLInputElement, "123 456");
    await submit(container.querySelector("form") as HTMLFormElement);
    expect(onSubmit).toHaveBeenCalledWith("123456");
  });

  // A sixteen-character recovery code with dashes is accepted and sent without them.
  it("takes a recovery code too", async () => {
    const { mounted, onSubmit } = render();
    const { container } = await mounted;
    await type(container.querySelector("input") as HTMLInputElement, "abcd-efgh-jkmn-pqrs");
    await submit(container.querySelector("form") as HTMLFormElement);
    expect(onSubmit).toHaveBeenCalledWith("abcdefghjkmnpqrs");
  });

  // Text that is neither a six-digit nor a recovery code is never submitted.
  it("does not send what is not a code", async () => {
    const { mounted, onSubmit } = render();
    const { container } = await mounted;
    await type(container.querySelector("input") as HTMLInputElement, "hello");
    await submit(container.querySelector("form") as HTMLFormElement);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  // The reason of a refused code is shown in an alert, and Back calls the back handler (to the password step).
  it("shows the reason a code was refused, and goes back to the password", async () => {
    const { mounted, onBack } = render({ error: "That code is wrong, or has been used already." });
    const { container } = await mounted;
    expect(container.querySelector("[role=alert]")?.textContent).toMatch(/wrong, or has been used/);
    await click(byText(container, "button", "Back")!);
    expect(onBack).toHaveBeenCalled();
  });

  // While a check is under way the button reads "Checking…" and is disabled, so a code is not sent twice.
  it("shows that it is working", async () => {
    const { mounted } = render({ busy: true });
    const { container } = await mounted;
    expect(byText<HTMLButtonElement>(container, "button", "Checking…")?.disabled).toBe(true);
  });
});
