// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import rules from "../auth/permissions.fixture.json";
import { fakeBff } from "../testing/bff";
import { mountWith } from "../testing/bff";
import { byText, cleanup, click, field, pick, settle, type, typeArea } from "../testing/dom";
import { Infrastructure } from "./Infrastructure";
import { OnboardingTab } from "./Onboarding";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const template = (over: Record<string, unknown> = {}) => ({
  name: "du-basic", description: "basic DU", entityType: "O-DU", vendorName: null, softwareBaseline: "2.1", requireBaseline: false, autoApply: false, enabled: true,
  changes: [{ managedFunctionRef: "NRCellDU=1", attributeChanges: { txPower: 20 }, operation: "merge" }], createdAt: "2026-10-01T10:00:00Z", updatedAt: "2026-10-01T10:00:00Z", ...over,
});
const row = (ref: string, over: Record<string, unknown> = {}) => ({
  managedElementRef: ref, status: "TEMPLATE_SELECTED", templateName: "du-basic", softwareVersion: "2.1", softwareBaseline: "2.1", softwareCheck: "MATCH", configJobId: null, detail: null,
  createdAt: "2026-10-01T10:00:00Z", updatedAt: "2026-10-01T10:05:00Z", ...over,
});

function bff(role: "viewer" | "operator" | "admin", overrides: Record<string, unknown> = {}) {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /smo/ran-nf-oam/onboarding-templates": { items: [template(), template({ name: "du-acme", vendorName: "acme", enabled: false })], limit: 200, offset: 0 },
    "GET /smo/ran-nf-oam/element-onboarding": { items: [
      row("ME-1"), row("ME-2", { status: "FAILED", detail: "config job j-1 ended FAILED: NETCONF_RPC_FAILED", configJobId: "7a2c9f1b-2222-4b3c-8d4e-bbbbbbbbbbbb" }),
      row("ME-3", { status: "ONBOARDED", softwareVersion: "2.0", softwareCheck: "MISMATCH" }), row("ME-4", { status: "APPLYING" }), row("ME-5", { status: "NO_TEMPLATE", templateName: null, softwareCheck: "NOT_CHECKED", softwareVersion: null, softwareBaseline: null }),
    ], limit: 200, offset: 0 },
    "GET /smo/ran-nf-oam/lifecycle-subscriptions": { items: [{ subscriptionId: "s-1", callbackUri: "https://noc.example/hook", events: ["ONBOARDING_FAILED"], createdAt: "2026-10-01T10:00:00Z" }], limit: 200, offset: 0 },
    "GET /smo/ran-nf-oam/o1-adaptor-endpoints": { items: [{ endpointId: "e-9", managedElementRef: "ME-9", adaptorUri: "http://a", protocolSupport: ["NETCONF"], registeredVia: "x", healthStatus: "ACTIVE", lastHeartbeatAt: null }], limit: 200, offset: 0 },
    ...overrides,
  });
}

const open = () => mountWith(<AuthProvider><OnboardingTab /></AuthProvider>);
const rowOf = (container: HTMLElement, text: string) => Array.from(container.querySelectorAll("tbody tr")).find((r) => r.textContent?.includes(text)) as HTMLElement;

describe("the onboarding templates", () => {
  it("lists each template with what it applies to, and says when none is defined", async () => {
    const { container } = await (bff("viewer"), open());
    await settle();
    const basic = rowOf(container, "du-basic");
    expect(basic.textContent).toContain("O-DU");
    expect(basic.textContent).toContain("any vendor");
    expect(basic.textContent).toContain("2.1");
    expect(basic.textContent).toContain("by an operator");
    expect(basic.textContent).toContain("ENABLED");
    const acme = rowOf(container, "du-acme");
    expect(acme.textContent).toContain("acme");
    expect(acme.textContent).toContain("DISABLED");
    cleanup();
    bff("viewer", { "GET /smo/ran-nf-oam/onboarding-templates": { items: [], limit: 200, offset: 0 } });
    const empty = await open();
    await settle();
    expect(empty.container.textContent).toContain("No template is defined");
  });

  it("gives a viewer nothing to change: no new, edit or delete, and the row opens a read-only view", async () => {
    bff("viewer");
    const { container } = await open();
    await settle();
    expect(byText(container, "button", "New template…")).toBeNull();
    expect(byText(container, "button", "Edit")).toBeNull();
    expect(byText(container, "button", "Delete")).toBeNull();
    await click(rowOf(container, "du-basic"));
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    expect(dialog.textContent).toContain("Template du-basic");
    expect(dialog.querySelector("textarea")).toBeNull();
    expect(dialog.querySelector("ul[aria-label='Template changes']")?.textContent).toContain("merge NRCellDU=1");
  });

  it("makes a template as an admin, with the name in the path and the fields as RAN NF OAM takes them", async () => {
    const calls = bff("admin", { "PUT /smo/ran-nf-oam/onboarding-templates/cu-basic": { body: template({ name: "cu-basic" }) } });
    const { container } = await open();
    await settle();
    await click(byText(container, "button", "New template…")!);
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    await type(field(dialog, "Name"), "cu-basic");
    await type(field(dialog, "Entity type"), "O-CU");
    await type(field(dialog, "Software baseline"), "5.0");
    await typeArea(field<HTMLTextAreaElement>(dialog, "Changes"), '[{"attributeChanges": {"adminState": "unlocked"}}]');
    await click(dialog.querySelector("input[type=checkbox]") as HTMLElement);                    // requireBaseline
    await click(byText(dialog, "button", "Save")!);
    await settle();
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.path).toBe("/smo/ran-nf-oam/onboarding-templates/cu-basic");
    expect(put.body).toMatchObject({ entityType: "O-CU", vendorName: null, softwareBaseline: "5.0", requireBaseline: true, autoApply: false, enabled: true,
      changes: [{ attributeChanges: { adminState: "unlocked" }, operation: "merge" }] });
    expect(document.querySelector("[role=dialog]")).toBeNull();                                   // closed after saving
  });

  it("shows what is wrong with a template before sending it", async () => {
    const calls = bff("admin");
    const { container } = await open();
    await settle();
    await click(byText(container, "button", "New template…")!);
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    await type(field(dialog, "Name"), "-bad");
    await click(byText(dialog, "button", "Save")!);
    expect(dialog.querySelector("[role=alert]")?.textContent).toContain("A template name is 1 to 100");
    await type(field(dialog, "Name"), "ok");
    await click(byText(dialog, "button", "Save")!);
    expect(dialog.querySelector("[role=alert]")?.textContent).toContain("entity type");
    await type(field(dialog, "Entity type"), "O-DU");
    await typeArea(field<HTMLTextAreaElement>(dialog, "Changes"), '[{"managedElementRef": "ME-1", "attributeChanges": {}}]');
    await click(byText(dialog, "button", "Save")!);
    expect(dialog.querySelector("[role=alert]")?.textContent).toContain("managedElementRef");
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("edits a template under its own name and deletes one after asking", async () => {
    const calls = bff("admin", { "PUT /smo/ran-nf-oam/onboarding-templates/du-basic": { body: template() }, "DELETE /smo/ran-nf-oam/onboarding-templates/du-acme": { status: 204 } });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container } = await open();
    await settle();
    await click(byText(rowOf(container, "du-basic") as HTMLElement, "button", "Edit")!);
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    expect(field(dialog, "Name").readOnly).toBe(true);
    await type(field(dialog, "Software baseline"), "2.2");
    await click(byText(dialog, "button", "Save")!);
    await settle();
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ softwareBaseline: "2.2", entityType: "O-DU" });
    await click(byText(rowOf(container, "du-acme") as HTMLElement, "button", "Delete")!);
    await settle();
    expect(confirm).toHaveBeenCalledOnce();
    expect(calls.find((c) => c.method === "DELETE")!.path).toBe("/smo/ran-nf-oam/onboarding-templates/du-acme");
  });
});

describe("the onboarding of elements", () => {
  it("lists each element with its state, template, software check and why it failed", async () => {
    bff("viewer");
    const { container } = await open();
    await settle();
    const failed = rowOf(container, "ME-2");
    expect(failed.textContent).toContain("FAILED");
    expect(failed.textContent).toContain("NETCONF_RPC_FAILED");
    const mismatch = rowOf(container, "ME-3");
    expect(mismatch.textContent).toContain("2.0");
    expect(mismatch.textContent).toContain("baseline 2.1");
    expect(mismatch.textContent).toContain("MISMATCH");
    expect(rowOf(container, "ME-5").textContent).toContain("NO_TEMPLATE");
    expect(rowOf(container, "ME-5").textContent).toContain("not reported");
  });

  it("gives a viewer no apply or select", async () => {
    bff("viewer");
    const { container } = await open();
    await settle();
    expect(byText(container, "button", "Apply")).toBeNull();
    expect(byText(container, "button", "Select…")).toBeNull();
    expect(byText(container, "button", "Select a template for an element…")).toBeNull();
  });

  it("offers apply and select only where the state machine allows them", async () => {
    bff("operator");
    const { container } = await open();
    await settle();
    const buttons = (ref: string) => Array.from(rowOf(container, ref).querySelectorAll("button")).map((b) => b.textContent);
    expect(buttons("ME-1")).toEqual(["Apply", "Select…"]);              // TEMPLATE_SELECTED
    expect(buttons("ME-2")).toEqual(["Apply again", "Select…"]);        // FAILED
    expect(buttons("ME-3")).toEqual(["Apply again", "Select…"]);        // ONBOARDED
    expect(buttons("ME-4")).toEqual([]);                                // APPLYING: being written
    expect(buttons("ME-5")).toEqual(["Select…"]);                       // NO_TEMPLATE
  });

  it("applies a template with the version the element runs, and sends no name for who applied it", async () => {
    const calls = bff("operator", { "POST /smo/ran-nf-oam/element-onboarding/ME-1/apply": { status: 202, body: row("ME-1", { status: "ONBOARDED" }) } });
    const { container } = await open();
    await settle();
    await click(byText(rowOf(container, "ME-1"), "button", "Apply")!);
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    expect(dialog.textContent).toContain("Apply du-basic to ME-1");
    expect((field(dialog, "Software version") as HTMLInputElement).value).toBe("2.1");           // what the row already knows
    await type(field(dialog, "Software version"), "2.3");
    await click(byText(dialog, "button", "Apply")!);
    await settle();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe("/smo/ran-nf-oam/element-onboarding/ME-1/apply");
    expect(post.body).toEqual({ softwareVersion: "2.3" });                                       // requestedBy is the backend's to set
    expect(document.querySelector("[role=dialog]")).toBeNull();
  });

  it("selects a template for an element again, naming one or taking the best match", async () => {
    const calls = bff("operator", { "POST /smo/ran-nf-oam/element-onboarding/ME-5/select": row("ME-5") });
    const { container } = await open();
    await settle();
    await click(byText(rowOf(container, "ME-5"), "button", "Select…")!);
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    const options = Array.from(field<HTMLSelectElement>(dialog, "Template").options).map((o) => o.value);
    expect(options).toEqual(["", "du-basic"]);                                                   // the disabled template is not offered
    await click(byText(dialog, "button", "Select")!);
    await settle();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({});
    await click(byText(rowOf(container, "ME-5"), "button", "Select…")!);
    await settle();
    const again = document.querySelector("[role=dialog]") as HTMLElement;
    await pick(field<HTMLSelectElement>(again, "Template"), "du-basic");
    await type(field(again, "Software version"), "2.1");
    await click(byText(again, "button", "Select")!);
    await settle();
    expect(calls.filter((c) => c.method === "POST")[1].body).toEqual({ template: "du-basic", softwareVersion: "2.1" });
  });

  it("selects a template for an element that has no row yet", async () => {
    const calls = bff("operator", { "POST /smo/ran-nf-oam/element-onboarding/ME-9/select": row("ME-9") });
    const { container } = await open();
    await settle();
    await click(byText(container, "button", "Select a template for an element…")!);
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    expect(byText(dialog, "button", "Select")!.hasAttribute("disabled")).toBe(true);              // no element chosen yet
    await pick(field<HTMLSelectElement>(dialog, "Managed element"), "ME-9");
    await click(byText(dialog, "button", "Select")!);
    await settle();
    expect(calls.find((c) => c.method === "POST")!.path).toBe("/smo/ran-nf-oam/element-onboarding/ME-9/select");
  });

  it("opens the detail of a row, with the config job of a failed apply", async () => {
    bff("viewer", { "GET /smo/ran-nf-oam/element-onboarding/ME-2": row("ME-2", { status: "FAILED", detail: "config job j-1 ended FAILED: NETCONF_RPC_FAILED", configJobId: "7a2c9f1b-2222-4b3c-8d4e-bbbbbbbbbbbb" }),
      "GET /smo/ran-nf-oam/config-jobs/7a2c9f1b-2222-4b3c-8d4e-bbbbbbbbbbbb": { jobId: "7a2c9f1b-2222-4b3c-8d4e-bbbbbbbbbbbb", status: "FAILED", subChanges: [] } });
    const { container } = await open();
    await settle();
    await click(rowOf(container, "ME-2"));
    await settle();
    const drawer = document.querySelector("[role=dialog]") as HTMLElement;
    expect(drawer.textContent).toContain("Onboarding of ME-2");
    expect(drawer.textContent).toContain("The template could not be written");
    expect(drawer.textContent).toContain("NETCONF_RPC_FAILED");
    await click(Array.from(drawer.querySelectorAll("button")).find((b) => b.querySelector("code")) as HTMLElement);        // the config job
    await settle();
    expect(document.querySelectorAll("[role=dialog]").length).toBe(2);
  });

  it("filters by state and shows an error instead of an empty table", async () => {
    const calls = bff("viewer");
    const { container } = await open();
    await settle();
    await pick(container.querySelector("select[aria-label='Onboarding status']") as HTMLSelectElement, "FAILED");
    await settle();
    expect(calls.filter((c) => c.path === "/smo/ran-nf-oam/element-onboarding").some((c) => c.query.get("status") === "FAILED")).toBe(true);
    cleanup();
    bff("viewer", { "GET /smo/ran-nf-oam/element-onboarding": { status: 503, body: { title: "ENDPOINT_UNREACHABLE", detail: "RAN NF OAM is down" } } });
    const broken = await open();
    await settle();
    expect(broken.container.textContent).toContain("RAN NF OAM is down");
  });
});

describe("who is told", () => {
  it("lists the watchers, and an admin adds one for chosen events and removes one", async () => {
    const calls = bff("admin", { "POST /smo/ran-nf-oam/lifecycle-subscriptions": { status: 201, body: {} }, "DELETE /smo/ran-nf-oam/lifecycle-subscriptions/s-1": { status: 204 } });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("https://noc.example/hook");
    await click(byText(container, "button", "Add watcher")!);
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    await type(field(dialog, "Callback URL"), " https://ops.example/events ");
    await click(Array.from(dialog.querySelectorAll("input[type=checkbox]"))[1] as HTMLElement);         // CAMPAIGN_HALTED
    await click(byText(dialog, "button", "Add watcher")!);
    await settle();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ callbackUri: "https://ops.example/events", events: ["CAMPAIGN_HALTED"] });
    await click(byText(container, "button", "Remove")!);
    await settle();
    expect(calls.find((c) => c.method === "DELETE")!.path).toBe("/smo/ran-nf-oam/lifecycle-subscriptions/s-1");
  });

  it("gives an operator the list and no way to change it", async () => {
    bff("operator");
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("https://noc.example/hook");
    expect(byText(container, "button", "Add watcher")).toBeNull();
    expect(byText(container, "button", "Remove")).toBeNull();
  });
});

describe("the Infrastructure page", () => {
  it("has the onboarding and campaign tabs, and opens on them from the address", async () => {
    bff("viewer", { "GET /smo/ran-nf-oam/software-campaigns": { items: [], limit: 200, offset: 0 } });
    window.location.hash = "#onboarding";
    const { container } = await mountWith(<AuthProvider><Infrastructure /></AuthProvider>);
    await settle();
    const tabs = Array.from(container.querySelectorAll("[role=tab]")).map((t) => t.textContent);
    expect(tabs).toContain("Onboarding");
    expect(tabs).toContain("Software campaigns");
    expect(container.textContent).toContain("Onboarding templates");
    await click(byText(container, "[role=tab]", "Software campaigns")!);
    await settle();
    expect(container.textContent).toContain("No campaign has been started.");
  });
});
