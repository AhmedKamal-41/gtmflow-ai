"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { APIError, login } from "@/lib/api";

// Phase 12: operator sign-in. Accounts are created with the operator CLI
// (python -m app.auth_cli create-user); there is no registration.

function safeNext(value: string | null): string {
  // Only same-site paths: never redirect to another origin after sign-in.
  if (!value || !value.startsWith("/") || value.startsWith("//") || /[\\\u0000-\u0020]/.test(value)) return "/";
  const target = new URL(value, window.location.origin);
  return target.origin === window.location.origin ? target.pathname + target.search + target.hash : "/";
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { signedIn } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const info = await login(username, password);
      setPassword("");
      signedIn(info);
      router.replace(safeNext(params?.get("next") ?? null));
    } catch (e) {
      setError(e instanceof APIError ? (e.detail ?? e.message) : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm">
      <Card title="Sign in" subtitle="GTMFlow operators only. Ask an administrator for an account.">
        <form onSubmit={submit} className="space-y-3" aria-label="Sign in">
          <label className="block text-sm">
            <span className="text-slate-700">Username</span>
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </label>
          <label className="block text-sm">
            <span className="text-slate-700">Password</span>
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
          <Button type="submit" loading={busy} fullWidth>
            Sign in
          </Button>
        </form>
      </Card>
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
