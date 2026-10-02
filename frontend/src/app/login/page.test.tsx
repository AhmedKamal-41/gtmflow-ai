import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "./page";

/** Sign-in page: sign-up with an emailed code and the guest button appear
 * only when the server enables them. */

const nav = vi.hoisted(() => ({ replace: vi.fn(), search: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
  usePathname: () => "/login",
  useSearchParams: () => nav.search,
}));

const auth = vi.hoisted(() => ({ signedIn: vi.fn() }));
vi.mock("@/components/AuthProvider", () => ({ useAuth: () => ({ signedIn: auth.signedIn }) }));

const fake = vi.hoisted(() => ({
  options: { self_signup: true, guest_access: true, email_delivery: "mock", guest_can_edit: true },
}));
const GUEST = { username: "guest-ab12cd34ef", role: "guest", expires_at: "2026-10-02T12:00:00Z", csrf_token: "g" };
const MEMBER = { username: "sam@example.com", role: "operator", expires_at: "2026-10-02T12:00:00Z", csrf_token: "m" };
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getAuthOptions: vi.fn(() => Promise.resolve(fake.options)),
    login: vi.fn(),
    register: vi.fn(() => Promise.resolve({ message: "If this address can be registered, a 6-digit code is on its way." })),
    resendCode: vi.fn(() => Promise.resolve({ message: "Code re-sent." })),
    verifyEmail: vi.fn((email: string, code: string) => code === "123456" ? Promise.resolve(MEMBER)
      : Promise.reject(new actual.APIError(400, "Bad Request", "That code is wrong or has expired. Request a new one."))),
    continueAsGuest: vi.fn(() => Promise.resolve(GUEST)),
  };
});

import { continueAsGuest, register, resendCode, verifyEmail } from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  nav.search = new URLSearchParams();
  fake.options = { self_signup: true, guest_access: true, email_delivery: "mock", guest_can_edit: true };
});

describe("sign-in page", () => {
  it("hides sign-up and guest access when the server does not offer them", async () => {
    fake.options = { self_signup: false, guest_access: false, email_delivery: "mock", guest_can_edit: true };
    render(<LoginPage />);
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Create an account" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Continue as guest" })).not.toBeInTheDocument();
  });

  it("continues as a guest straight into the app", async () => {
    nav.search = new URLSearchParams("next=/metrics");
    render(<LoginPage />);

    const guestButton = await screen.findByRole("button", { name: "Continue as guest" });
    await act(async () => fireEvent.click(guestButton));
    expect(continueAsGuest).toHaveBeenCalledOnce();
    expect(auth.signedIn).toHaveBeenCalledWith(GUEST);
    expect(nav.replace).toHaveBeenCalledWith("/metrics");
  });

  it("says when guests are read-only on this server", async () => {
    fake.options = { ...fake.options, guest_can_edit: false };
    render(<LoginPage />);
    expect(await screen.findByText(/cannot change data on this server/)).toBeInTheDocument();
  });

  it("creates an account, then signs in only after the emailed code", async () => {
    render(<LoginPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Create an account" }));
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "sam@example.com" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "a-long-password-1" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Create account" })));
    expect(register).toHaveBeenCalledWith("sam@example.com", "a-long-password-1");

    expect(screen.getByText("Check your email")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("6-digit code");
    expect(screen.getByText(/printed in the API server/)).toBeInTheDocument();
    const verify = screen.getByRole("button", { name: "Verify and continue" });
    expect(verify).toBeDisabled();
    expect(screen.getByRole("button", { name: /Resend code in/ })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Verification code"), { target: { value: "99-99-99" } });
    expect(screen.getByLabelText("Verification code")).toHaveValue("999999");
    await act(async () => fireEvent.click(verify));
    expect(screen.getByRole("alert")).toHaveTextContent("wrong or has expired");
    expect(auth.signedIn).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Verification code"), { target: { value: "123456" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Verify and continue" })));
    expect(verifyEmail).toHaveBeenLastCalledWith("sam@example.com", "123456");
    expect(auth.signedIn).toHaveBeenCalledWith(MEMBER);
    expect(nav.replace).toHaveBeenCalledWith("/");
  });

  it("re-sends a code only after the countdown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<LoginPage />);
      fireEvent.click(await screen.findByRole("button", { name: "Create an account" }));
      fireEvent.change(screen.getByLabelText("Email"), { target: { value: "sam@example.com" } });
      fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "a-long-password-1" } });
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Create account" })));
      expect(screen.getByRole("button", { name: /Resend code in 60s/ })).toBeDisabled();
      for (let second = 0; second < 60; second += 1) {
        await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
      }
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Resend code" })));
      expect(resendCode).toHaveBeenCalledWith("sam@example.com");
    } finally {
      vi.useRealTimers();
    }
  });
});
