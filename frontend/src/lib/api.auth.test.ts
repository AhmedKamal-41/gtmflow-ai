import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";

/** Phase 12: the fetch wrapper sends cookies, attaches the CSRF token only to
 * state-changing requests, and reports 401s so the app can ask for sign-in. */

type Call = { url: string; init: RequestInit };
let calls: Call[] = [];
let nextStatus = 200;

beforeEach(() => {
  calls = [];
  nextStatus = 200;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(nextStatus === 204 ? null : JSON.stringify(nextStatus === 200 ? {
      username: "ada", role: "operator", expires_at: "2026-09-26T12:00:00Z", csrf_token: "csrf-123",
    } : { detail: "Sign in required." }), { status: nextStatus, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => {
  vi.unstubAllGlobals();
  api.setCsrfToken(null);
  api.setUnauthorizedHandler(null);
});

const header = (c: Call, name: string) => (c.init.headers as Record<string, string>)[name];

describe("session-aware requests", () => {
  it("sends cookies always and the CSRF token only on writes", async () => {
    await api.login("ada", "pw");
    await api.getLeads();
    await api.runDemo().catch(() => undefined);
    const [loginCall, getCall, postCall] = calls;
    expect(loginCall.init.credentials).toBe("include");
    expect(header(getCall, "X-CSRF-Token")).toBeUndefined();
    expect(header(postCall, "X-CSRF-Token")).toBe("csrf-123");
  });

  it("asks for sign-in on 401, except for the auth endpoints themselves", async () => {
    const handler = vi.fn();
    api.setUnauthorizedHandler(handler);
    nextStatus = 401;
    await expect(api.getLeads()).rejects.toBeInstanceOf(api.APIError);
    expect(handler).toHaveBeenCalledTimes(1);
    await expect(api.login("ada", "wrong")).rejects.toBeInstanceOf(api.APIError);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("forgets the CSRF token at logout", async () => {
    await api.login("ada", "pw");
    nextStatus = 204;
    await api.logout();
    nextStatus = 200;
    await api.runDemo().catch(() => undefined);
    expect(header(calls[calls.length - 1], "X-CSRF-Token")).toBeUndefined();
  });
});
