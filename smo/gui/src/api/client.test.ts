/**
 * Unit tests of the pure helpers in `client.ts`: cookie parsing, query-string building and the error-body reader.
 * No network or DOM is involved. Run: `npx vitest run src/api/client.test.ts` from `smo/gui`.
 */

import { describe, expect, it } from "vitest";

import { buildQuery, describeError, readCookie } from "./client";

describe("readCookie", () => {
  // The double-submit token is read out of document.cookie: it must be found among other cookies, percent-decoded, and keep an "=" inside the value.
  it("finds the CSRF cookie among others and decodes it", () => {
    expect(readCookie("a=1; smo_csrf=abc%3D%3D; b=2", "smo_csrf")).toBe("abc==");
    expect(readCookie("smo_csrf=x=y", "smo_csrf")).toBe("x=y");
    expect(readCookie("a=1", "smo_csrf")).toBeUndefined();
    expect(readCookie("", "smo_csrf")).toBeUndefined();
  });
});

describe("buildQuery", () => {
  // An unset filter must not reach the BFF as an empty parameter; an array becomes a repeated key.
  it("drops empty values and repeats arrays", () => {
    expect(buildQuery({ state: "", model_id: undefined, limit: 5, ok: true, ids: ["a", "b"] })).toBe("?limit=5&ok=true&ids=a&ids=b");
    expect(buildQuery({})).toBe("");
    // an empty keyset cursor is meaningful (the first page), so it is kept
    expect(buildQuery({ after: "", limit: 2 })).toBe("?after=&limit=2");
    expect(buildQuery()).toBe("");
  });
});

describe("describeError", () => {
  // The BFF's own {title, status, detail} problem body is shown as title and detail.
  it("reads the BFF's problem shape", () => {
    expect(describeError(403, { title: "FORBIDDEN", status: 403, detail: "requires role admin" })).toEqual({ title: "FORBIDDEN", detail: "requires role admin" });
  });
  // A refusal raised inside a BFF dependency (revoked session, MFA enrolment required) arrives wrapped as {detail: {title, ...}} and must still show its title.
  it("reads an error the BFF raised from a dependency, wrapped as {detail: {title, detail}}", () => {
    expect(describeError(403, { detail: { title: "MFA_ENROLMENT_REQUIRED", status: 403, detail: "enrol first" } })).toEqual({ title: "MFA_ENROLMENT_REQUIRED", detail: "enrol first" });
    expect(describeError(401, { detail: { title: "SESSION_REVOKED", status: 401 } })).toEqual({ title: "SESSION_REVOKED", detail: undefined });
  });
  // A module's plain {detail: "..."} and the validation-error array become one readable line (field path plus message).
  it("reads FastAPI's detail string and validation arrays", () => {
    expect(describeError(404, { detail: "no such model" })).toEqual({ title: "HTTP 404", detail: "no such model" });
    expect(describeError(422, { detail: [{ loc: ["body", "version"], msg: "Field required" }] }).detail).toBe("version Field required");
  });
  // An OAuth2 error body is read, and an empty body falls back to "HTTP <status>" so the user always sees something.
  it("reads OAuth2 error bodies and falls back to the status", () => {
    expect(describeError(400, { error: "invalid_grant" })).toEqual({ title: "invalid_grant", detail: undefined });
    expect(describeError(502, "")).toEqual({ title: "HTTP 502" });
  });
});
