// @vitest-environment jsdom
/** Tests of the sign-in page (pages/login): the password step, the one-time-code step (right, wrong, spent code, Back), the OIDC-only mode with its
 * break-glass link, the recovery-code warning, and the redesign's split screen (brand panel, SSO first, the names scripts/gui_e2e.py looks for).
 * `fetch` is stubbed by `backend()` below; no BFF runs. Run: `npx vitest run src/pages/login` from smo/gui. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import { ToastProvider } from "../../../components/Toast";
import { Login } from "../index";
import { byText, cleanup, click, mount, settle, submit, type } from "../../../testing/dom";

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

/** Mounts the page at /login with the providers it needs. */
const render = () => mount(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ToastProvider><AuthProvider><MemoryRouter initialEntries={["/login"]}><Login /></MemoryRouter></AuthProvider></ToastProvider>
  </QueryClientProvider>,
);

/** Types a username and password and submits the form. */
const fill = async (container: HTMLElement, user: string, password: string) => {
  const inputs = container.querySelectorAll<HTMLInputElement>("input");
  await type(inputs[0], user);
  await type(inputs[1], password);
  await submit(container.querySelector("form") as HTMLFormElement);
  await settle();
};

const SESSION = { username: "ana", role: "viewer", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false };

describe("Login", () => {
  // the default deployment shows the password form and neither an SSO button nor a break-glass link
  it("is the password form alone when nothing else is on", async () => {
    backend({ localLogin: true, oidc: { enabled: false } });
    const { container } = await render();
    await settle();
    expect(container.querySelector("input[type=password]")).not.toBeNull();
    expect(byText(container, "a", /Sign in with/)).toBeNull();
    expect(byText(container, "button", "Break-glass sign-in")).toBeNull();
  });

  // a right password for an enrolled account leads to the code step, which posts the challenge and the cleaned code
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

  // a wrong code keeps the user on the code step with the backend's reason
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

  // a spent challenge drops back to the password step instead of leaving a dead code form
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

  // Back on the code step returns to the password form
  it("returns from the code step to the password with Back", async () => {
    backend({ localLogin: true, oidc: { enabled: false } }, { login: () => ({ body: { mfaRequired: true, challenge: "c.c.c", expiresIn: 300 } }) });
    const { container } = await render();
    await settle();
    await fill(container, "ana", "a-password-1");
    await click(byText(container, "button", "Back")!);
    expect(container.querySelector("input[type=password]")).not.toBeNull();
  });

  // oidc mode hides the form until the break-glass link is used, and that form still signs in
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

  // with no break-glass account there is no way to the password form at all
  it("shows no form and no link when the mode is oidc and local login is off altogether", async () => {
    backend({ localLogin: false, loginMode: "oidc", breakGlass: false, oidc: { enabled: true, providerName: "Keycloak", loginUrl: "/api/oidc/login" } });
    const { container } = await render();
    await settle();
    expect(container.querySelector("input")).toBeNull();
    expect(byText(container, "button", "Break-glass sign-in")).toBeNull();
    expect(byText(container, "a", "Sign in with Keycloak")).not.toBeNull();
  });

  // signing in with a recovery code warns how many are left
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

  // the redesign's split screen: a brand panel with the Radisys wordmark, and the SSO button placed before the local form
  it("puts the brand panel beside the form, and the SSO button first", async () => {
    backend({ localLogin: true, oidc: { enabled: true, providerName: "Keycloak", loginUrl: "/api/oidc/login" } });
    const { container } = await render();
    await settle();
    expect(container.querySelector(".login-brand .wordmark-chip img")?.getAttribute("src")).toBe("/brand/radisys-wordmark.png");
    expect(container.querySelector(".login-brand svg.login-rings")).not.toBeNull();
    const form = container.querySelector(".login-form-wrap form") as HTMLFormElement;
    const sso = byText(form, "a", "Sign in with Keycloak")!;
    const user = form.querySelector("input")!;
    expect(sso.compareDocumentPosition(user) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(form.textContent).toMatch(/or with a local account/);
    expect(container.textContent).toContain("Operator Console");               // scripts/gui_smoke.py and the OIDC e2e look for it
  });

  // an OIDC failure reason from the redirect shows its fixed sentence, never text from the provider
  it("shows the fixed sentence for an oidc_error reason", async () => {
    backend({ localLogin: true, oidc: { enabled: true, providerName: "Keycloak", loginUrl: "/api/oidc/login" } });
    const { container } = await mount(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ToastProvider><AuthProvider><MemoryRouter initialEntries={["/login?oidc_error=no_role"]}><Login /></MemoryRouter></AuthProvider></ToastProvider>
      </QueryClientProvider>,
    );
    await settle();
    expect(container.querySelector("[role=alert]")?.textContent).toMatch(/none of your groups/);
  });

  // scripts/gui_e2e.py finds the form by these exact accessible names, so a rename would break the browser check
  it("keeps the labels Username and Password and the button Sign in", async () => {
    backend({ localLogin: true, oidc: { enabled: false } });
    const { container } = await render();
    await settle();
    const labels = Array.from(container.querySelectorAll("label")).map((l) => l.querySelector(".field-label")?.textContent);
    expect(labels).toEqual(["Username", "Password"]);
    expect(byText(container, "button", "Sign in")).not.toBeNull();
  });
});
