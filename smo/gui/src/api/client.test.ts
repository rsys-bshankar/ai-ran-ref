import { describe, expect, it } from "vitest";

import { buildQuery, describeError, readCookie } from "./client";

describe("readCookie", () => {
  it("finds the CSRF cookie among others and decodes it", () => {
    expect(readCookie("a=1; smo_csrf=abc%3D%3D; b=2", "smo_csrf")).toBe("abc==");
    expect(readCookie("smo_csrf=x=y", "smo_csrf")).toBe("x=y");
    expect(readCookie("a=1", "smo_csrf")).toBeUndefined();
    expect(readCookie("", "smo_csrf")).toBeUndefined();
  });
});

describe("buildQuery", () => {
  it("drops empty values and repeats arrays", () => {
    expect(buildQuery({ state: "", model_id: undefined, limit: 5, ok: true, ids: ["a", "b"] })).toBe("?limit=5&ok=true&ids=a&ids=b");
    expect(buildQuery({})).toBe("");
    expect(buildQuery()).toBe("");
  });
});

describe("describeError", () => {
  it("reads the BFF's problem shape", () => {
    expect(describeError(403, { title: "FORBIDDEN", status: 403, detail: "requires role admin" })).toEqual({ title: "FORBIDDEN", detail: "requires role admin" });
  });
  it("reads FastAPI's detail string and validation arrays", () => {
    expect(describeError(404, { detail: "no such model" })).toEqual({ title: "HTTP 404", detail: "no such model" });
    expect(describeError(422, { detail: [{ loc: ["body", "version"], msg: "Field required" }] }).detail).toBe("version Field required");
  });
  it("reads OAuth2 error bodies and falls back to the status", () => {
    expect(describeError(400, { error: "invalid_grant" })).toEqual({ title: "invalid_grant", detail: undefined });
    expect(describeError(502, "")).toEqual({ title: "HTTP 502" });
  });
});
