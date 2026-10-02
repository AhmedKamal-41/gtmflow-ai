"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "./AuthProvider";
import { Icon, type IconName } from "./Icon";

const NAV: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Home", icon: "home" },
  { href: "/upload", label: "Upload", icon: "upload" },
  { href: "/batches", label: "Batches", icon: "layers" },
  { href: "/metrics", label: "Metrics", icon: "chart" },
  { href: "/seller-profile", label: "Seller", icon: "users" },
  { href: "/annotation", label: "Annotate", icon: "file" },
  { href: "/demo", label: "Demo", icon: "play" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppHeader() {
  const pathname = usePathname() ?? "/";
  const { session, signOut, signOutError } = useAuth();

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200/80 bg-white/85 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">
        <Link
          href="/"
          className="flex items-center gap-2 rounded-md text-base font-semibold tracking-tight text-slate-900"
        >
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 text-sm font-bold text-white shadow-sm">
            G
          </span>
          GTMFlow
        </Link>

        <nav className="flex items-center gap-0.5 text-sm">
          {NAV.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                aria-label={item.label}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 font-medium transition-colors ${
                  active
                    ? "bg-brand-50 text-brand-700"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`}
              >
                <Icon name={item.icon} className="h-4 w-4" />
                <span className="hidden sm:inline">{item.label}</span>
              </Link>
            );
          })}
        </nav>
        {session && (
          <div className="flex items-center gap-2 text-xs text-slate-500" aria-label="Signed in">
            {session.role === "guest" ? (
              <span className="rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-800 ring-1 ring-amber-200">
                Guest
              </span>
            ) : (
              <span>
                {session.username}
                {session.role === "viewer" ? " (read-only)" : ""}
              </span>
            )}
            <button
              type="button"
              className="rounded-md px-2 py-1 font-medium text-slate-600 hover:bg-slate-100"
              onClick={() => void signOut()}
            >
              Sign out
            </button>
          </div>
        )}
      </div>
      {signOutError && <p role="alert" className="px-4 pb-2 text-sm text-red-700">{signOutError}</p>}
    </header>
  );
}
