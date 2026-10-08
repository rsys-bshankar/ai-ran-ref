// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginCodeStep } from "./LoginCodeStep";
import { byText, cleanup, click, mount, submit, type } from "../testing/dom";

afterEach(cleanup);

const render = (over: Partial<Parameters<typeof LoginCodeStep>[0]> = {}) => {
  const onSubmit = vi.fn();
  const onBack = vi.fn();
  return { onSubmit, onBack, mounted: mount(<LoginCodeStep onSubmit={onSubmit} onBack={onBack} error={null} busy={false} {...over} />) };
};

describe("LoginCodeStep", () => {
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

  it("sends the code without the spaces", async () => {
    const { mounted, onSubmit } = render();
    const { container } = await mounted;
    await type(container.querySelector("input") as HTMLInputElement, "123 456");
    await submit(container.querySelector("form") as HTMLFormElement);
    expect(onSubmit).toHaveBeenCalledWith("123456");
  });

  it("takes a recovery code too", async () => {
    const { mounted, onSubmit } = render();
    const { container } = await mounted;
    await type(container.querySelector("input") as HTMLInputElement, "abcd-efgh-jkmn-pqrs");
    await submit(container.querySelector("form") as HTMLFormElement);
    expect(onSubmit).toHaveBeenCalledWith("abcdefghjkmnpqrs");
  });

  it("does not send what is not a code", async () => {
    const { mounted, onSubmit } = render();
    const { container } = await mounted;
    await type(container.querySelector("input") as HTMLInputElement, "hello");
    await submit(container.querySelector("form") as HTMLFormElement);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("shows the reason a code was refused, and goes back to the password", async () => {
    const { mounted, onBack } = render({ error: "That code is wrong, or has been used already." });
    const { container } = await mounted;
    expect(container.querySelector("[role=alert]")?.textContent).toMatch(/wrong, or has been used/);
    await click(byText(container, "button", "Back")!);
    expect(onBack).toHaveBeenCalled();
  });

  it("shows that it is working", async () => {
    const { mounted } = render({ busy: true });
    const { container } = await mounted;
    expect(byText<HTMLButtonElement>(container, "button", "Checking…")?.disabled).toBe(true);
  });
});
