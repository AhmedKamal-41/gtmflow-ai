"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { ModelStatusPill, useAIStatus } from "@/components/AIStatusProvider";
import { useAuth } from "@/components/AuthProvider";
import { Icon, type IconName } from "@/components/Icon";
import { Logo } from "@/components/Logo";

const NAV: { href: string; label: string; icon: IconName; also?: string[] }[] = [
  { href: "/", label: "Today", icon: "home" },
  { href: "/leads", label: "Leads", icon: "inbox" },
  { href: "/imports", label: "Imports", icon: "upload", also: ["/batches", "/upload"] },
  { href: "/metrics", label: "Insights", icon: "chart" },
  { href: "/settings", label: "Settings", icon: "settings", also: ["/seller-profile"] },
];

function isActive(pathname: string, item: (typeof NAV)[number]): boolean {
  if (item.href === "/") return pathname === "/";
  return [item.href, ...(item.also ?? [])].some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const { session, signOut, signOutError } = useAuth();
  const { status } = useAIStatus();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => setMenuOpen(false), [pathname]);

  if (pathname === "/login") {
    return (
      <div className="min-h-dvh px-4 py-12">
        <div className="mx-auto mb-8 flex max-w-sm items-center justify-center gap-2.5">
          <Logo />
          <span className="text-xl font-semibold tracking-tight text-slate-900">GTMFlow</span>
        </div>
        {children}
      </div>
    );
  }

  const sidebar = (
    <nav aria-label="Main" className="flex h-full flex-col gap-1 px-3 py-4">
      <Link href="/" className="mb-6 flex items-center gap-2.5 px-2">
        <Logo className="h-7 w-7" />
        <span className="text-base font-semibold tracking-tight text-slate-900">GTMFlow</span>
      </Link>
      {NAV.map((item) => {
        const active = isActive(pathname, item);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              active ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
            }`}
          >
            <Icon name={item.icon} className="h-4 w-4" />
            {item.label}
          </Link>
        );
      })}
      <div className="mt-auto space-y-3 border-t border-slate-200 px-2 pt-4">
        <Link href="/settings" className="block rounded-md hover:bg-slate-50" aria-label="Drafting model status">
          <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">Drafting model</div>
          <ModelStatusPill status={status} />
        </Link>
        {session && (
          <div className="flex items-center justify-between gap-2" aria-label="Signed in">
            {session.role === "guest" ? (
              <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-amber-200">
                Guest
              </span>
            ) : (
              <span className="truncate text-xs text-slate-600" title={session.username}>
                {session.username}
                {session.role === "viewer" ? " (read-only)" : ""}
              </span>
            )}
            <button
              type="button"
              className="rounded-md px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100"
              onClick={() => void signOut()}
            >
              Sign out
            </button>
          </div>
        )}
        {signOutError && <p role="alert" className="text-xs text-red-700">{signOutError}</p>}
      </div>
    </nav>
  );

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
      <aside className="hidden border-r border-slate-200 bg-white lg:block">
        <div className="sticky top-0 h-dvh">{sidebar}</div>
      </aside>
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 lg:hidden">
        <Link href="/" className="flex items-center gap-2">
          <Logo className="h-7 w-7" />
          <span className="font-semibold text-slate-900">GTMFlow</span>
        </Link>
        <button type="button" aria-label="Open menu" aria-expanded={menuOpen}
          className="rounded-md p-2 text-slate-600 hover:bg-slate-100" onClick={() => setMenuOpen((open) => !open)}>
          <Icon name="menu" className="h-5 w-5" />
        </button>
      </div>
      {menuOpen && <div className="border-b border-slate-200 bg-white lg:hidden">{sidebar}</div>}
      <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-10">{children}</main>
    </div>
  );
}
