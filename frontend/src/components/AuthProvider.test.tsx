import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "@/app/login/page";

import { AuthProvider, useAuth } from "./AuthProvider";
import { logout } from "@/lib/api";

/** Phase 12: pages render only for a signed-in session; otherwise the
 * browser goes to /login, which returns to a same-site page after sign-in. */

const nav = vi.hoisted(() => ({ replace: vi.fn(), pathname: "/batches", search: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
  usePathname: () => nav.pathname,
  useSearchParams: () => nav.search,
}));

const fake = vi.hoisted(() => ({ session: null as unknown, loginResult: null as unknown }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getSession: vi.fn(() => (fake.session ? Promise.resolve(fake.session) : Promise.reject(new actual.APIError(401, "Unauthorized", "Sign in required.")))),
    login: vi.fn(() => (fake.loginResult ? Promise.resolve(fake.loginResult)
      : Promise.reject(new actual.APIError(401, "Unauthorized", "Invalid username or password.")))),
    logout: vi.fn(() => Promise.resolve()),
  };
});

const SESSION = { username: "ada", role: "operator", expires_at: "2026-09-26T12:00:00Z", csrf_token: "c" };

function SessionControls() {
  const { session, signOut, signOutError } = useAuth();
  return <><span>{session?.username}</span><button onClick={() => void signOut()}>Sign out</button>
    {signOutError && <p role="alert">{signOutError}</p>}</>;
}

beforeEach(() => {
  vi.clearAllMocks();
  nav.pathname = "/batches";
  nav.search = new URLSearchParams();
  fake.session = null;
  fake.loginResult = null;
  window.history.replaceState(null, "", "/batches?x=1");
});

describe("AuthProvider", () => {
  it("shows pages only with a live session", async () => {
    fake.session = SESSION;
    render(<AuthProvider><p>secret page</p></AuthProvider>);
    expect(await screen.findByText("secret page")).toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("sends a signed-out visitor to sign-in and remembers where they were", async () => {
    render(<AuthProvider><p>secret page</p></AuthProvider>);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login?next=%2Fbatches%3Fx%3D1"));
    expect(screen.queryByText("secret page")).not.toBeInTheDocument();
  });

  it("does not claim sign-out succeeded when the server could not revoke the session", async () => {
    fake.session = SESSION;
    vi.mocked(logout).mockRejectedValueOnce(new Error("network unavailable"));
    render(<AuthProvider><SessionControls /></AuthProvider>);
    expect(await screen.findByText("ada")).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByText("Sign out")));
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-out could not be confirmed");
    expect(screen.getByText("ada")).toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
    await act(async () => fireEvent.click(screen.getByText("Sign out")));
    expect(nav.replace).toHaveBeenLastCalledWith("/login");
  });
});

describe("login page", () => {
  beforeEach(() => {
    nav.pathname = "/login";
  });

  it("shows the server's generic error and keeps the user on the page", async () => {
    render(<AuthProvider><LoginPage /></AuthProvider>);
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "ada" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "nope" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign in" })));
    expect(screen.getByRole("alert")).toHaveTextContent("Invalid username or password.");
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("returns to the requested page, but never to another site", async () => {
    fake.loginResult = SESSION;
    nav.search = new URLSearchParams("next=/metrics");
    const { unmount } = render(<AuthProvider><LoginPage /></AuthProvider>);
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "ada" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "right-password-1" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign in" })));
    expect(nav.replace).toHaveBeenLastCalledWith("/metrics");
    unmount();
    nav.search = new URLSearchParams("next=//evil.example/steal");
    render(<AuthProvider><LoginPage /></AuthProvider>);
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "ada" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "right-password-1" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign in" })));
    expect(nav.replace).toHaveBeenLastCalledWith("/");
  });

  it.each(["/\\evil.example", "/\n/evil.example", "https://evil.example", "javascript:alert(1)"])(
    "rejects an unsafe return path %s", async (next) => {
      fake.loginResult = SESSION;
      nav.search = new URLSearchParams({ next });
      render(<AuthProvider><LoginPage /></AuthProvider>);
      fireEvent.change(screen.getByLabelText("Username"), { target: { value: "ada" } });
      fireEvent.change(screen.getByLabelText("Password"), { target: { value: "right-password-1" } });
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign in" })));
      expect(nav.replace).toHaveBeenLastCalledWith("/");
    },
  );
});
