// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecoveryCodes, TotpEnrolment } from "./TotpEnrolment";
import { byText, cleanup, click, mount, settle, submit, type } from "../testing/dom";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

interface Call { method: string; path: string; body: unknown }

/** The BFF, as far as the enrolment page asks it. `state` is what GET /me/totp says; the routes change it the way the real ones do. */
function backend(state: { available?: boolean; enrolled?: boolean; pending?: boolean; recoveryCodesLeft?: number; reason?: string }, failConfirm = false) {
  const calls: Call[] = [];
  const codes = Array.from({ length: 10 }, (_, i) => `aaaa-bbbb-cccc-dd${String(i).padStart(2, "0")}`);
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const path = String(url).replace(/^\/api/, "");
    calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const ok = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    if (path === "/me/totp") return ok({ available: true, enrolled: false, pending: false, recoveryCodesLeft: 0, ...state });
    if (path === "/me/totp/begin") { state.pending = true; return ok({ secret: "ABCDEFGHIJKLMNOP", otpauthUri: "otpauth://totp/SMO:ana?secret=ABCDEFGHIJKLMNOP", issuer: "SMO", account: "ana" }); }
    if (path === "/me/totp/confirm") {
      if (failConfirm) return ok({ title: "INVALID_CODE", status: 400, detail: "the code does not match" }, 400);
      Object.assign(state, { enrolled: true, pending: false, recoveryCodesLeft: 10 });
      return ok({ status: "enrolled", recoveryCodes: codes, recoveryCodesLeft: 10 });
    }
    if (path === "/me/totp/recovery-codes") return ok({ recoveryCodes: codes, recoveryCodesLeft: 10 });
    return ok({ title: "NOT_FOUND" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, codes };
}

const render = (props: Parameters<typeof TotpEnrolment>[0] = {}) =>
  mount(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TotpEnrolment {...props} /></QueryClientProvider>);

beforeEach(() => { document.body.innerHTML = ""; });

describe("TotpEnrolment", () => {
  it("walks from the setup key to the recovery codes, which need an explicit acknowledgement", async () => {
    const { calls, codes } = backend({});
    const { container } = await render();
    await settle();
    await click(byText(container, "button", "Set up a one-time code")!);
    await settle();
    // the secret is shown for typing, in groups, and there is no image
    const key = container.querySelector<HTMLInputElement>("input[aria-label='Setup key']")!;
    expect(key.value).toBe("ABCD EFGH IJKL MNOP");
    expect(container.querySelector("img, canvas, svg")).toBeNull();
    expect(container.textContent).toMatch(/there is no QR code here/);
    expect(container.querySelector<HTMLInputElement>("input[aria-label='otpauth link']")!.value).toMatch(/^otpauth:\/\/totp\//);
    // not confirmed yet: the button waits for six digits
    const confirm = byText<HTMLButtonElement>(container, "button", "Confirm")!;
    expect(confirm.disabled).toBe(true);
    await type(container.querySelector<HTMLInputElement>("input[autocomplete=one-time-code]")!, "123 456");
    expect(confirm.disabled).toBe(false);
    await submit(container.querySelectorAll("form")[0] as HTMLFormElement);
    await settle();
    expect(calls.find((c) => c.path === "/me/totp/confirm")?.body).toEqual({ code: "123456" });
    // the recovery codes: all ten, once, with the warning; the secret is gone from the page
    expect(container.textContent).toMatch(/shown only this once/);
    for (const c of codes) expect(container.textContent).toContain(c);
    expect(container.textContent).not.toContain("ABCD EFGH");
    await click(byText(container, "button", "I have saved them")!);
    await settle();
    // after the acknowledgement the codes cannot be read again, and the page says what is left
    for (const c of codes) expect(container.textContent).not.toContain(c);
    expect(container.textContent).toMatch(/10 recovery codes left/);
  });

  it("says why a code was refused and stays on the step", async () => {
    backend({ pending: true }, true);
    const { container } = await render();
    await settle();
    await click(byText(container, "button", "Start again with a new secret")!);
    await settle();
    await type(container.querySelector<HTMLInputElement>("input[autocomplete=one-time-code]")!, "000000");
    await submit(container.querySelectorAll("form")[0] as HTMLFormElement);
    await settle();
    expect(container.querySelector("[role=alert]")?.textContent).toBe("the code does not match");
    expect(container.querySelector("input[aria-label='Setup key']")).not.toBeNull();
  });

  it("tells an admin who must enrol why they are here", async () => {
    backend({});
    const { container } = await render({ required: true });
    await settle();
    expect(container.textContent).toMatch(/administrator account must have a one-time code/);
  });

  it("shows an enrolled account its recovery codes left and can make new ones", async () => {
    const { calls, codes } = backend({ enrolled: true, recoveryCodesLeft: 3 });
    const { container } = await render();
    await settle();
    expect(container.textContent).toMatch(/3 recovery codes left/);
    expect(byText(container, "button", "Set up a one-time code")).toBeNull();
    await type(container.querySelector<HTMLInputElement>("input[autocomplete=one-time-code]")!, "654321");
    await submit(container.querySelectorAll("form")[0] as HTMLFormElement);
    await settle();
    expect(calls.find((c) => c.path === "/me/totp/recovery-codes")?.body).toEqual({ code: "654321" });
    for (const c of codes) expect(container.textContent).toContain(c);
  });

  it("says so when the server has no key", async () => {
    backend({ available: false });
    const { container } = await render();
    await settle();
    expect(container.textContent).toMatch(/GUI_TOTP_KEY/);
    expect(byText(container, "button", "Set up a one-time code")).toBeNull();
  });

  it("has nothing to set up for a user of the identity provider", async () => {
    backend({ available: false, reason: "identity provider" });
    const { container } = await render();
    await settle();
    expect(container.textContent).toMatch(/identity provider, which asks for the second factor/);
  });
});

describe("RecoveryCodes", () => {
  it("copies all the codes, one per line", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const done = vi.fn();
    const { container } = await mount(<RecoveryCodes codes={["aaaa-bbbb-cccc-dddd", "eeee-ffff-gggg-hhhh"]} onDone={done} />);
    await click(byText(container, "button", "Copy all")!);
    expect(writeText).toHaveBeenCalledWith("aaaa-bbbb-cccc-dddd\neeee-ffff-gggg-hhhh\n");
    expect(byText(container, "button", "Copied")).not.toBeNull();
    await click(byText(container, "button", "I have saved them")!);
    expect(done).toHaveBeenCalled();
  });
});
