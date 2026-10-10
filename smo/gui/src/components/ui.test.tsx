// @vitest-environment jsdom
/** Tests of the shared component fixes of GUI-10.7 to 10.10: a failed sign-out is caught and the user is signed out here and told; `Id`'s copy
 * says whether it worked and never leaves a rejected promise; `useHashTab` listens to the hash once, not once per render; the toast timers are
 * cleared on unmount. Uses the fake BFF of `src/testing/bff.tsx`. Run: `npx vitest run src/components` from smo/gui. */
import { act, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "../auth/AuthContext";
import { fakeBff, mountWith } from "../testing/bff";
import { byText, cleanup, click, mount, settle } from "../testing/dom";
import { ToastProvider, useToast } from "./Toast";
import { copyText, Id, useHashTab } from "./ui";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); window.location.hash = ""; });

/** Shows who is signed in and a sign-out button. */
function Who() {
  const { me, logout } = useAuth();
  return <div><span id="who">{me ? me.username : "signed out"}</span><button type="button" onClick={() => void logout()}>Sign out</button></div>;
}

describe("sign-out (GUI-10.7)", () => {
  // the BFF answers 502: no unhandled rejection, the session is dropped here, and a toast says the server did not confirm it
  it("signs out locally and says so when POST /logout fails", async () => {
    const unhandled = vi.fn();
    window.addEventListener("unhandledrejection", unhandled);
    fakeBff({ "GET /me": { username: "ana", role: "viewer", local: true }, "GET /permissions": { role: "viewer", rules: [] },
      "POST /logout": { status: 502, body: { title: "BAD_GATEWAY" } } });
    const { container } = await mountWith(<AuthProvider><Who /></AuthProvider>);
    await settle();
    expect(container.querySelector("#who")!.textContent).toBe("ana");
    await click(byText(container, "button", "Sign out")!);
    await settle();
    expect(container.querySelector("#who")!.textContent).toBe("signed out");
    expect(document.body.querySelector(".toast-error")?.textContent).toContain("did not confirm");
    expect(unhandled).not.toHaveBeenCalled();
    window.removeEventListener("unhandledrejection", unhandled);
  });
});

describe("Id copy (GUI-10.8)", () => {
  // a copy the browser refuses is caught and told; one it allows says "Copied"
  it("says whether the copy worked", async () => {
    const writeText = vi.fn().mockRejectedValueOnce(new Error("NotAllowedError")).mockResolvedValueOnce(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const { container } = await mount(<ToastProvider><Id value="0b9f3f1e-1111-4222-8333-444455556666" /></ToastProvider>);
    await click(container.querySelector("code.id")!);
    await settle();
    expect(document.body.querySelector(".toast-error")?.textContent).toContain("Could not copy");
    await click(container.querySelector("code.id")!);
    await settle();
    expect(document.body.querySelector(".toast-success")?.textContent).toBe("Copied");
    expect(writeText).toHaveBeenCalledTimes(2);
  });

  // no clipboard at all (an insecure context) is a refused copy, not a TypeError
  it("treats a missing clipboard as refused", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    expect(await copyText("x")).toBe(false);
  });
});

/** A tab hook and a button that re-renders the component. */
function Tabbed() {
  const [tab] = useHashTab(["a", "b"] as const, "a");
  const [n, setN] = useState(0);
  return <div><span id="tab">{tab}</span><button type="button" onClick={() => setN(n + 1)}>render {n}</button></div>;
}

describe("useHashTab (GUI-10.9)", () => {
  // the hashchange listener is added once however often the component renders, and still follows the hash
  it("subscribes once and follows the hash", async () => {
    const add = vi.spyOn(window, "addEventListener");
    const { container } = await mount(<Tabbed />);
    for (let i = 0; i < 3; i++) await click(byText(container, "button", /render/)!);
    expect(add.mock.calls.filter(([type]) => type === "hashchange")).toHaveLength(1);
    await act(async () => { window.location.hash = "#b"; window.dispatchEvent(new HashChangeEvent("hashchange")); });
    expect(container.querySelector("#tab")!.textContent).toBe("b");
  });
});

/** Pushes one toast when clicked. */
function Pusher() {
  const toast = useToast();
  return <button type="button" onClick={() => toast.push({ tone: "error", text: "boom" })}>push</button>;
}

describe("toast timers (GUI-10.10)", () => {
  // a toast's timer is cleared when the provider unmounts, so nothing fires into an unmounted tree; a click dismisses it and clears its timer too
  it("clears the dismiss timers on unmount and on an early dismiss", async () => {
    vi.useFakeTimers();
    const cleared = vi.spyOn(globalThis, "clearTimeout");
    const m = await mount(<ToastProvider><Pusher /></ToastProvider>);
    await act(async () => { byText(m.container, "button", "push")!.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { byText(m.container, "button", "push")!.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(vi.getTimerCount()).toBe(2);
    await act(async () => { (m.container.querySelector(".toast") as HTMLElement).dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(vi.getTimerCount()).toBe(1);
    m.unmount();
    expect(vi.getTimerCount()).toBe(0);
    expect(cleared).toHaveBeenCalled();
  });
});
