"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { getSession, logout as apiLogout, setUnauthorizedHandler, type SessionInfo } from "@/lib/api";

// Phase 12: every page except /login needs a signed-in operator. The backend
// enforces this on every request; this provider only decides what to show
// and where to send the browser. Nothing here stores credentials: the
// session is an HttpOnly cookie.

type AuthState = {
  session: SessionInfo | null;
  signOut: () => Promise<void>;
  signedIn: (info: SessionInfo) => void;
};

const AuthContext = createContext<AuthState>({ session: null, signOut: async () => undefined, signedIn: () => undefined });

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

const PUBLIC_ROUTES = new Set(["/login"]);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [checked, setChecked] = useState(false);
  const isPublic = PUBLIC_ROUTES.has(pathname);

  const toLogin = useCallback(() => {
    setSession(null);
    if (!PUBLIC_ROUTES.has(window.location.pathname)) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      router.replace(`/login?next=${next}`);
    }
  }, [router]);

  useEffect(() => {
    setUnauthorizedHandler(toLogin);
    return () => setUnauthorizedHandler(null);
  }, [toLogin]);

  useEffect(() => {
    if (isPublic) {
      setChecked(true);
      return;
    }
    let active = true;
    getSession()
      .then((info) => {
        if (active) setSession(info);
      })
      .catch(() => {
        if (active) toLogin();
      })
      .finally(() => {
        if (active) setChecked(true);
      });
    return () => {
      active = false;
    };
  }, [isPublic, toLogin]);

  const signOut = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setSession(null);
      router.replace("/login");
    }
  }, [router]);

  const signedIn = useCallback((info: SessionInfo) => setSession(info), []);

  return (
    <AuthContext.Provider value={{ session, signOut, signedIn }}>
      {isPublic || (checked && session) ? children : (
        <p className="py-16 text-center text-sm text-slate-500" role="status">
          Checking your session…
        </p>
      )}
    </AuthContext.Provider>
  );
}
