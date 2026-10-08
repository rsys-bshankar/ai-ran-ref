// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { ToastProvider } from "../components/Toast";
import { Login } from "./Login";
import { byText, cleanup, click, mount, settle, submit, type } from "../testing/dom";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; });

interface Config { localLogin: boolean; loginMode?: string; breakGlass?: boolean; oidc: { enabled: boolean; providerName?: string; loginUrl?: string } }
type Handler = (body: Record<string, string>) => { status?: number; body: unknown };

/** The BFF's sign-in routes. `login` and `totp` answer the two steps; `me` is 401 until a session was opened. */
function backend(config: Config, handlers: { login?: Handler; totp?: Handler } = {}) {
  const calls: { path: string; body: Record<string, string> | undefined }[] = [];
  let signedIn: unknown = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url).replace(/^\/api/, "");
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, body });
    const reply = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });
    if (path === "/auth/config") return reply(config);
    if (path === "/me") return signedIn ? reply(signedIn) : reply({ title: "UNAUTHENTICATED" }, 401);
    const handler = path === "/login" ? handlers.login : path === "/login/totp" ? handlers.totp : undefined;
    if (!handler) return reply({ title: "NOT_FOUND" }, 404);
    const out = handler(body ?? {});
    if ((out.status ?? 200) < 300 && (out.body as { username?: string }).username) signedIn = out.body;
    return reply(out.body, out.status ?? 200);
  }));
  return calls;
}

const render = () => mount(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ToastProvider><AuthProvider><MemoryRouter initialEntries={["/login"]}><Login /></MemoryRouter></AuthProvider></ToastProvider>
  </QueryClientProvider>,
);

const fill = async (container: HTMLElement, user: string, password: string) => {
  const inputs = container.querySelectorAll<HTMLInputElement>("input");
  await type(inputs[0], user);
  await type(inputs[1], password);
  await submit(container.querySelector("form") as HTMLFormElement);
  await settle();
};

const SESSION = { username: "ana", role: "viewer", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false };

describe("Login", () => {
  it("is the password form alone when nothing else is on", async () => {
    backend({ localLogin: true, oidc: { enabled: false } });
    const { container } = await render();
    await settle();
    expect(container.querySelector("input[type=password]")).not.toBeNull();
    expect(byText(container, "a", /Sign in with/)).toBeNull();
    expect(byText(container, "button", "Break-glass sign-in")).toBeNull();
  });

  it("goes to the one-time code after a right password, and signs in with it", async () => {
    const calls = backend({ localLogin: true, oidc: { enabled: false } }, {
      login: () => ({ body: { mfaRequired: true, challenge: "chal.enge.token", expiresIn: 300 } }),
      totp: () => ({ body: SESSION }),
    });
    const { container } = await render();
    await settle();
    await fill(container, "ana", "a-password-1");
    expect(container.querySelector("input[type=password]")).toBeNull();      // the password step is gone
    expect(container.textContent).toMatch(/Enter the 6-digit code/);
    await type(container.querySelector("input") as HTMLInputElement, "123 456");
    await submit(container.querySelector("form") as HTMLFormElement);
    await settle();
    expect(calls.find((c) => c.path === "/login/totp")?.body).toEqual({ challenge: "chal.enge.token", code: "123456" });
  });

  it("says a wrong code is wrong and stays on the code step", async () => {
    backend({ localLogin: true, oidc: { enabled: false } }, {
      login: () => ({ body: { mfaRequired: true, challenge: "c.c.c", expiresIn: 300 } }),
      totp: () => ({ status: 401, body: { title: "INVALID_CODE", status: 401, detail: "x" } }),
    });
    const { container } = await render();
    await settle();
    await fill(container, "ana", "a-password-1");
    await type(container.querySelector("input") as HTMLInputElement, "000000");
    await submit(container.querySelector("form") as HTMLFormElement);
    await settle();
    expect(container.querySelector("[role=alert]")?.textContent).toMatch(/wrong, or has been used/);
    expect(container.textContent).toMatch(/Enter the 6-digit code/);
  });

  it("goes back to the password when the challenge is spent or the account is locked", async () => {
    backend({ localLogin: true, oidc: { enabled: false } }, {
      login: () => ({ body: { mfaRequired: true, challenge: "c.c.c", expiresIn: 300 } }),
      totp: () => ({ status: 401, body: { title: "CHALLENGE_INVALID", status: 401 } }),
    });
    const { container } = await render();
    await settle();
    await fill(container, "ana", "a-password-1");
    await type(container.querySelector("input") as HTMLInputElement, "123456");
    await submit(container.querySelector("form") as HTMLFormElement);
    await settle();
    expect(container.querySelector("input[type=password]")).not.toBeNull();
    expect(container.querySelector("[role=alert]")?.textContent).toMatch(/Enter your password again/);
  });

  it("returns from the code step to the password with Back", async () => {
    backend({ localLogin: true, oidc: { enabled: false } }, { login: () => ({ body: { mfaRequired: true, challenge: "c.c.c", expiresIn: 300 } }) });
    const { container } = await render();
    await settle();
    await fill(container, "ana", "a-password-1");
    await click(byText(container, "button", "Back")!);
    expect(container.querySelector("input[type=password]")).not.toBeNull();
  });

  it("shows only the provider's button when the mode is oidc, and the form behind a break-glass link", async () => {
    const calls = backend({ localLogin: false, loginMode: "oidc", breakGlass: true, oidc: { enabled: true, providerName: "Keycloak", loginUrl: "/api/oidc/login" } }, {
      login: () => ({ body: { mfaRequired: true, challenge: "c.c.c", expiresIn: 300 } }),
    });
    const { container } = await render();
    await settle();
    expect(byText(container, "a", "Sign in with Keycloak")?.getAttribute("href")).toBe("/api/oidc/login");
    expect(container.querySelector("input")).toBeNull();                       // no form
    await click(byText(container, "button", "Break-glass sign-in")!);
    expect(container.textContent).toMatch(/Break-glass accounts only/);
    expect(container.querySelector("input[type=password]")).not.toBeNull();
    await fill(container, "emergency", "a-password-1");
    expect(calls.some((c) => c.path === "/login")).toBe(true);
    expect(container.textContent).toMatch(/Enter the 6-digit code/);
  });

  it("shows no form and no link when the mode is oidc and local login is off altogether", async () => {
    backend({ localLogin: false, loginMode: "oidc", breakGlass: false, oidc: { enabled: true, providerName: "Keycloak", loginUrl: "/api/oidc/login" } });
    const { container } = await render();
    await settle();
    expect(container.querySelector("input")).toBeNull();
    expect(byText(container, "button", "Break-glass sign-in")).toBeNull();
    expect(byText(container, "a", "Sign in with Keycloak")).not.toBeNull();
  });

  it("warns, after a recovery code, how many are left", async () => {
    backend({ localLogin: true, oidc: { enabled: false } }, {
      login: () => ({ body: { mfaRequired: true, challenge: "c.c.c", expiresIn: 300 } }),
      totp: () => ({ body: { ...SESSION, recoveryCodesLeft: 1 } }),
    });
    const { container } = await render();
    await settle();
    await fill(container, "ana", "a-password-1");
    await type(container.querySelector("input") as HTMLInputElement, "abcd-efgh-jkmn-pqrs");
    await submit(container.querySelector("form") as HTMLFormElement);
    await settle();
    expect(document.body.textContent).toMatch(/1 recovery code left/);
  });
});
