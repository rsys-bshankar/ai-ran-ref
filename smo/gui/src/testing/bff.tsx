// A fake BFF for component tests: `fetch` answers by method and path (without /api), records every call, and a test mounts a component in the providers it needs.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import { ToastProvider } from "../components/Toast";
import { mount } from "./dom";

export interface Call { method: string; path: string; query: URLSearchParams; headers: Record<string, string>; body: unknown }
export type Reply = { status?: number; body?: unknown } | unknown;
export type Handler = (call: Call) => Reply;

/** Routes are `"GET /rapps"`-style keys (a path with no query), or a function for everything. The first key that matches wins; no match is a 404. */
export function fakeBff(routes: Record<string, Handler | Reply>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const [rawPath, search = ""] = String(url).replace(/^\/api/, "").split("?");
    const headers: Record<string, string> = {};
    new Headers(init?.headers).forEach((v, k) => { headers[k.toLowerCase()] = v; });
    const call: Call = { method: (init?.method ?? "GET").toUpperCase(), path: rawPath, query: new URLSearchParams(search), headers, body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const key = `${call.method} ${call.path}`;
    const found = Object.entries(routes).find(([k]) => k === key || (k.endsWith("*") && key.startsWith(k.slice(0, -1))));
    if (!found) return new Response(JSON.stringify({ title: "NOT_FOUND" }), { status: 404, headers: { "content-type": "application/json" } });
    const reply = typeof found[1] === "function" ? (found[1] as Handler)(call) : found[1];
    const wrapped = reply && typeof reply === "object" && ("status" in reply || "body" in reply) ? reply as { status?: number; body?: unknown } : { body: reply };
    const status = wrapped.status ?? 200;
    return status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(wrapped.body ?? {}), { status, headers: { "content-type": "application/json" } });
  }));
  return calls;
}

export const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

export function withProviders(element: ReactElement, opts: { at?: string; route?: string } = {}) {
  return (
    <QueryClientProvider client={newClient()}>
      <ToastProvider>
        <MemoryRouter initialEntries={[opts.at ?? "/"]}>
          {opts.route ? <Routes><Route path={opts.route} element={element} /></Routes> : element}
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

export const mountWith = (element: ReactElement, opts: { at?: string; route?: string } = {}) => mount(withProviders(element, opts));
