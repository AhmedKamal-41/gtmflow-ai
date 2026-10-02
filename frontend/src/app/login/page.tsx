"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import {
  APIError,
  type AuthOptions,
  continueAsGuest,
  getAuthOptions,
  login,
  register,
  resendCode,
  type SessionInfo,
  verifyEmail,
} from "@/lib/api";

// Sign in, create an account (confirmed with an emailed 6-digit code), or
// continue as a guest. Sign-up and guest access appear only when the server
// enables them; the backend enforces every rule.

type Mode = "signin" | "signup" | "verify";

const RESEND_SECONDS = 60;
const INPUT = "mt-1 w-full rounded-md border border-slate-300 px-3 py-2";

function safeNext(value: string | null): string {
  // Only same-site paths: never redirect to another origin after sign-in.
  if (!value || !value.startsWith("/") || value.startsWith("//") || /[\\\u0000- ]/.test(value)) return "/";
  const target = new URL(value, window.location.origin);
  return target.origin === window.location.origin ? target.pathname + target.search + target.hash : "/";
}

function message(error: unknown, fallback: string): string {
  return error instanceof APIError ? (error.detail ?? error.message) : fallback;
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { signedIn } = useAuth();
  const [options, setOptions] = useState<AuthOptions | null>(null);
  const [mode, setMode] = useState<Mode>("signin");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<"form" | "guest" | "resend" | null>(null);
  const [resendIn, setResendIn] = useState(0);

  useEffect(() => {
    getAuthOptions()
      .then(setOptions)
      .catch(() => setOptions({ self_signup: false, guest_access: false, email_delivery: "mock", guest_can_edit: false }));
  }, []);

  useEffect(() => {
    if (resendIn <= 0) return;
    const timer = window.setTimeout(() => setResendIn((s) => s - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [resendIn]);

  function switchTo(next: Mode) {
    setMode(next);
    setError(null);
    setNotice(null);
    setPassword("");
    setCode("");
  }

  function finish(info: SessionInfo) {
    setPassword("");
    setCode("");
    signedIn(info);
    router.replace(safeNext(params?.get("next") ?? null));
  }

  async function run(kind: "form" | "guest" | "resend", action: () => Promise<void>, fallback: string) {
    setBusy(kind);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(message(e, fallback));
    } finally {
      setBusy(null);
    }
  }

  function submitSignIn(event: React.FormEvent) {
    event.preventDefault();
    void run("form", async () => finish(await login(username, password)), "Sign-in failed.");
  }

  function submitSignUp(event: React.FormEvent) {
    event.preventDefault();
    void run("form", async () => {
      const sent = await register(email, password);
      setPassword("");
      setMode("verify");
      setNotice(sent.message);
      setResendIn(RESEND_SECONDS);
    }, "Sign-up failed.");
  }

  function submitCode(event: React.FormEvent) {
    event.preventDefault();
    void run("form", async () => finish(await verifyEmail(email, code)), "Verification failed.");
  }

  function resend() {
    void run("resend", async () => {
      const sent = await resendCode(email);
      setNotice(sent.message);
      setResendIn(RESEND_SECONDS);
    }, "The code could not be re-sent.");
  }

  function guest() {
    void run("guest", async () => finish(await continueAsGuest()), "Guest access failed.");
  }

  const alerts = (
    <>
      {notice && <p role="status" className="rounded-md bg-brand-50 px-3 py-2 text-sm text-brand-800">{notice}</p>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </>
  );

  return (
    <div className="mx-auto max-w-sm space-y-4">
      {mode === "signin" && (
        <Card title="Sign in" subtitle="Use your email (or operator username) and password.">
          <form onSubmit={submitSignIn} className="space-y-3" aria-label="Sign in">
            <label className="block text-sm">
              <span className="text-slate-700">Email or username</span>
              <input className={INPUT} autoComplete="username" value={username}
                onChange={(e) => setUsername(e.target.value)} required />
            </label>
            <label className="block text-sm">
              <span className="text-slate-700">Password</span>
              <input className={INPUT} type="password" autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)} required />
            </label>
            {alerts}
            <Button type="submit" loading={busy === "form"} fullWidth>Sign in</Button>
          </form>
          {options?.self_signup && (
            <div className="mt-4 space-y-1 text-center text-sm text-slate-600">
              <p>
                New here?{" "}
                <button type="button" className="font-medium text-brand-700 hover:underline" onClick={() => switchTo("signup")}>
                  Create an account
                </button>
              </p>
              <p>
                <button type="button" className="text-brand-700 hover:underline"
                  onClick={() => { setEmail(username.includes("@") ? username : email); switchTo("verify"); }}>
                  I have a verification code
                </button>
              </p>
            </div>
          )}
        </Card>
      )}

      {mode === "signup" && (
        <Card title="Create an account" subtitle="We'll email you a 6-digit code to confirm your address.">
          <form onSubmit={submitSignUp} className="space-y-3" aria-label="Create an account">
            <label className="block text-sm">
              <span className="text-slate-700">Email</span>
              <input className={INPUT} type="email" autoComplete="email" value={email}
                onChange={(e) => setEmail(e.target.value)} required />
            </label>
            <label className="block text-sm">
              <span className="text-slate-700">Password</span>
              <input className={INPUT} type="password" autoComplete="new-password" minLength={12} value={password}
                onChange={(e) => setPassword(e.target.value)} required />
              <span className="mt-1 block text-xs text-slate-500">At least 12 characters.</span>
            </label>
            {alerts}
            <Button type="submit" loading={busy === "form"} fullWidth>Create account</Button>
          </form>
          <p className="mt-4 text-center text-sm text-slate-600">
            Already have an account?{" "}
            <button type="button" className="font-medium text-brand-700 hover:underline" onClick={() => switchTo("signin")}>
              Sign in
            </button>
          </p>
        </Card>
      )}

      {mode === "verify" && (
        <Card title="Check your email" subtitle="Enter the 6-digit code to finish creating your account.">
          <form onSubmit={submitCode} className="space-y-3" aria-label="Verify your email">
            <label className="block text-sm">
              <span className="text-slate-700">Email</span>
              <input className={INPUT} type="email" autoComplete="email" value={email}
                onChange={(e) => setEmail(e.target.value)} required />
            </label>
            <label className="block text-sm">
              <span className="text-slate-700">Verification code</span>
              <input className={`${INPUT} tracking-[0.4em]`} inputMode="numeric" autoComplete="one-time-code"
                pattern="[0-9]{6}" maxLength={6} value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} required />
            </label>
            {options?.email_delivery === "mock" && (
              <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
                Development server: no email is sent. The code is printed in the API server&apos;s terminal.
              </p>
            )}
            {alerts}
            <Button type="submit" loading={busy === "form"} disabled={code.length !== 6} fullWidth>
              Verify and continue
            </Button>
          </form>
          <div className="mt-4 flex items-center justify-between text-sm">
            <button type="button" className="text-brand-700 hover:underline disabled:text-slate-400 disabled:no-underline"
              onClick={resend} disabled={resendIn > 0 || busy === "resend" || !email}>
              {resendIn > 0 ? `Resend code in ${resendIn}s` : "Resend code"}
            </button>
            <button type="button" className="text-slate-600 hover:underline" onClick={() => switchTo("signin")}>
              Back to sign in
            </button>
          </div>
        </Card>
      )}

      {options?.guest_access && (
        <div className="space-y-2 text-center">
          <div className="flex items-center gap-3 text-xs uppercase tracking-wide text-slate-400">
            <span className="h-px flex-1 bg-slate-200" />or<span className="h-px flex-1 bg-slate-200" />
          </div>
          <Button variant="secondary" onClick={guest} loading={busy === "guest"} fullWidth>
            Continue as guest
          </Button>
          <p className="text-xs text-slate-500">
            No account needed. Guest sessions are temporary
            {options.guest_can_edit ? "." : "; guests can look around but cannot change data on this server."}
          </p>
        </div>
      )}
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
