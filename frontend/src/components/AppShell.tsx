"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { Sheet } from "@/components/ui/Sheet";

const NAV = [
  { href: "/chat", label: "Ask", icon: "◇" },
  { href: "/library", label: "Library", icon: "▣" },
  { href: "/about", label: "About", icon: "○" },
  { href: "/admin", label: "Admin", icon: "⚙", admin: true },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, logout, loading } = useAuth();
  const path = usePathname();
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);

  useEffect(() => {
    if (!loading && !user && path !== "/login") {
      router.replace("/login");
    }
  }, [loading, user, path, router]);

  useEffect(() => {
    setMobileNav(false);
  }, [path]);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-[var(--ink-muted)] text-sm">
        Loading…
      </div>
    );
  }

  if (path === "/login") return <>{children}</>;

  if (!user) {
    return (
      <div className="flex h-screen items-center justify-center text-[var(--ink-muted)] text-sm">
        Redirecting to sign in…
      </div>
    );
  }

  const navLinks = NAV.filter((n) => !n.admin || user.role === "admin").map((n) => {
    const active = path === n.href || path.startsWith(n.href + "/");
    return (
      <Link
        key={n.href}
        href={n.href}
        title={n.label}
        className={`flex items-center gap-2 rounded-[var(--radius)] px-3 py-2 text-sm no-underline focus-ring ${
          active
            ? "bg-[var(--accent-wash)] font-medium text-[var(--accent)]"
            : "text-[var(--ink)] hover:bg-[var(--surface-2)]"
        } ${collapsed ? "justify-center px-2" : ""}`}
      >
        <span className="text-[13px] opacity-70" aria-hidden>
          {n.icon}
        </span>
        {!collapsed && <span>{n.label}</span>}
      </Link>
    );
  });

  return (
    <div className="flex h-screen min-h-0">
      {/* Desktop rail */}
      <aside
        className={`hidden shrink-0 flex-col border-r border-[var(--hairline)] bg-[var(--surface)] md:flex ${
          collapsed ? "w-[64px]" : "w-[200px]"
        }`}
      >
        <div className="border-b border-[var(--hairline)] px-3 py-3">
          {!collapsed ? (
            <>
              <div className="font-display text-lg tracking-tight text-[var(--ink)]">COBMIS</div>
              <div className="mt-0.5 text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                Local · private
              </div>
            </>
          ) : (
            <div className="text-center font-display text-sm text-[var(--accent)]">C</div>
          )}
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 p-2">{navLinks}</nav>
        <div className="border-t border-[var(--hairline)] p-2">
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            className="mb-2 w-full rounded px-2 py-1 text-[11px] text-[var(--ink-muted)] hover:bg-[var(--surface-2)] focus-ring"
          >
            {collapsed ? "»" : "Collapse"}
          </button>
          {!collapsed && (
            <div className="px-1 text-xs text-[var(--ink-muted)]">
              <div className="truncate font-medium text-[var(--ink)]">{user.display_name}</div>
              <div className="truncate">{user.email}</div>
              <button
                type="button"
                onClick={() => logout().then(() => router.push("/login"))}
                className="mt-2 text-[var(--accent)] hover:underline focus-ring"
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </aside>

      {/* Mobile top bar */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-[var(--hairline)] px-3 py-2 md:hidden">
          <button
            type="button"
            className="rounded border border-[var(--hairline)] px-2 py-1 text-xs focus-ring"
            onClick={() => setMobileNav(true)}
          >
            Menu
          </button>
          <span className="font-display text-base">COBMIS</span>
          <span className="ml-auto text-[10px] uppercase tracking-wide text-[var(--ink-muted)]">
            Local
          </span>
        </div>
        <main className="min-h-0 min-w-0 flex-1 overflow-hidden">{children}</main>
      </div>

      <Sheet open={mobileNav} title="Navigate" onClose={() => setMobileNav(false)} side="left">
        <nav className="flex flex-col gap-0.5 p-2">{navLinks}</nav>
        <div className="border-t border-[var(--hairline)] p-3 text-xs text-[var(--ink-muted)]">
          <div className="font-medium text-[var(--ink)]">{user.display_name}</div>
          <button
            type="button"
            onClick={() => logout().then(() => router.push("/login"))}
            className="mt-2 text-[var(--accent)]"
          >
            Sign out
          </button>
        </div>
      </Sheet>
    </div>
  );
}
