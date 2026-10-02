"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { getSession, logout as apiLogout, setCsrfToken, setUnauthorizedHandler, type SessionInfo } from "@/lib/api";

// Phase 12: every page except /login needs a signed-in operator. The backend
// enforces this on every request; this provider only decides what to show
// and where to send the browser. Nothing here stores credentials: the
// session is an HttpOnly cookie.

type AuthState = {
  session: SessionInfo | null;
  signOut: () => Promise<void>;
  signedIn: (info: SessionInfo) => void;
  signOutError: string | null;
};

const AuthContext = createContext<AuthState>({ session: null, signOut: async () => undefined, signedIn: () => undefined, signOutError: null });

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

const PUBLIC_ROUTES = new Set(["/login"]);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [checked, setChecked] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const sessionGeneration = useRef(0);
  const isPublic = PUBLIC_ROUTES.has(pathname);

  const toLogin = useCallback(() => {
    sessionGeneration.current += 1;
    setCsrfToken(null);
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
    const generation = ++sessionGeneration.current;
    getSession()
      .then((info) => {
        if (active && generation === sessionGeneration.current) setSession(info);
      })
      .catch(() => {
        if (active && generation === sessionGeneration.current) toLogin();
      })
      .finally(() => {
        if (active) setChecked(true);
      });
    return () => {
      active = false;
    };
  }, [isPublic, toLogin]);

  const signOut = useCallback(async () => {
    sessionGeneration.current += 1;
    setSignOutError(null);
    try {
      await apiLogout();
      setSession(null);
      router.replace("/login");
    } catch {
      setSignOutError("Sign-out could not be confirmed. Refresh this page and retry.");
    }
  }, [router]);

  const signedIn = useCallback((info: SessionInfo) => {
    sessionGeneration.current += 1;
    setSignOutError(null);
    setSession(info);
  }, []);

  return (
    <AuthContext.Provider value={{ session, signOut, signedIn, signOutError }}>
      {isPublic || (checked && session) ? children : (
        <p className="py-16 text-center text-sm text-slate-500" role="status">
          Checking your session…
        </p>
      )}
    </AuthContext.Provider>
  );
}
