// @vitest-environment jsdom
/** Infrastructure → O1 endpoints & jobs (moved from pages/Infrastructure.test.tsx): the managed elements' region / tenant column and the register
 * form's region / tenant payload (SEC-10.2). Run: `npx vitest run src/pages/infrastructure` from smo/gui. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith } from "../../../testing/bff";
import { byText, cleanup, click, settle } from "../../../testing/dom";
import { Infrastructure } from "..";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = "#o1"; });

const endpoint = (ref: string, extra: Record<string, unknown> = {}) => ({
  endpointId: `e-${ref}`, managedElementRef: ref, adaptorUri: "http://a:9/netconf", protocolSupport: ["NETCONF"], registeredVia: "MNS_REGISTRY_NRM",
  healthStatus: "ACTIVE", lastHeartbeatAt: null, ...extra,
});

/**
 * Starts the fake BFF as an admin with three O1 endpoints (two with a region, one with a tenant) and the registration route.
 */
function bff() {
  return fakeBff({
    "GET /me": { username: "ana", role: "admin", csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role: "admin", rules },
    "GET /smo/ran-nf-oam/o1-adaptor-endpoints": { items: [endpoint("ME-1", { region: "eu-west", tenant: "acme" }), endpoint("ME-2", { region: "eu-west", tenant: null }), endpoint("ME-3")], limit: 50, offset: 0 },
    "GET /smo/ran-nf-oam/config-jobs": { items: [], limit: 50, offset: 0 },
    "GET /smo/ran-nf-oam/software-management-jobs": { items: [], limit: 50, offset: 0 },
    "POST /smo/ran-nf-oam/o1-adaptor-endpoints": { status: 201, body: { endpointId: "e-9" } },
  });
}

const open = () => mountWith(<AuthProvider><Infrastructure /></AuthProvider>);

describe("the managed elements and where they are (SEC-10.2)", () => {
  // Each element shows its region and tenant, and a dash for what is not set.
  it("shows the region and tenant of each element, and a dash for what is not set", async () => {
    bff();
    const { container } = await open();
    await settle(6);
    const rows = Array.from(container.querySelectorAll("tbody tr")).filter((r) => r.textContent?.includes("ME-"));
    const cells = (ref: string) => Array.from(rows.find((r) => r.textContent?.includes(ref))!.querySelectorAll("td")).map((c) => c.textContent);
    expect(Array.from(container.querySelectorAll("th")).map((h) => h.textContent)).toContain("Region / tenant");
    expect(cells("ME-1")).toContain("eu-west / acme");
    expect(cells("ME-2")).toContain("eu-west / —");
    expect(cells("ME-3")).toContain("—");
  });

  // Registering an element sends its region and tenant, and leaves them out when blank.
  it("registers an element with a region and a tenant, and leaves them out when blank", async () => {
    const calls = bff();
    const { container } = await open();
    await settle(6);
    await click(byText(container, "button", "Register endpoint")!);
    await settle();
    const set = (label: string, value: string) => {
      const input = Array.from(document.querySelectorAll("label")).find((l) => l.textContent?.startsWith(label))!.querySelector("input") as HTMLInputElement;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };
    set("Managed element ref", "ME-9");
    set("Region", " eu-west ");
    await settle();
    await click(byText(document.body, "button", "Register")!);
    await settle();
    const posted = calls.find((c) => c.method === "POST" && c.path === "/smo/ran-nf-oam/o1-adaptor-endpoints")!;
    expect(posted.body).toMatchObject({ managedElementRef: "ME-9", region: "eu-west", tenant: null });
  });
});
