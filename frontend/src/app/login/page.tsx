"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { login, user, loading } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDefaults, setShowDefaults] = useState(false);

  useEffect(() => {
    if (!loading && user) router.replace("/chat");
  }, [loading, user, router]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      router.push("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  function fillPoC() {
    setEmail("admin@local");
    setPassword("change-me-admin");
    setShowDefaults(false);
  }

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <section className="border-b border-[var(--hairline)] bg-[var(--surface-2)] px-6 py-8 md:flex md:w-[42%] md:flex-col md:justify-between md:border-b-0 md:border-r md:p-12">
        <div>
          <div className="font-display text-3xl text-[var(--ink)] md:text-4xl">COBMIS</div>
          <p className="mt-3 max-w-sm text-sm leading-relaxed text-[var(--ink-muted)]">
            County Budget Intelligence — answers from Controller of Budget reports on this
            machine, with proof beside every claim.
          </p>
        </div>
        <p className="mt-6 text-xs text-[var(--ink-muted)] md:mt-0">Loopback only · no hosted AI</p>
      </section>
      <section className="flex flex-1 items-center justify-center p-6 sm:p-8">
        <form
          onSubmit={onSubmit}
          className="w-full max-w-sm rounded-[var(--radius)] border border-[var(--hairline)] bg-[var(--surface)] p-6"
        >
          <h1 className="text-lg font-semibold text-[var(--ink)]">Sign in</h1>
          <p className="mt-1 text-sm text-[var(--ink-muted)]">Local account on this host.</p>
          <label className="mt-5 block text-xs font-medium uppercase tracking-wide text-[var(--ink-muted)]">
            Email
            <input
              className="mt-1 w-full rounded-[var(--radius)] border border-[var(--hairline)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--ink)] outline-none focus:border-[var(--accent)] focus-ring"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label className="mt-3 block text-xs font-medium uppercase tracking-wide text-[var(--ink-muted)]">
            Password
            <input
              type="password"
              className="mt-1 w-full rounded-[var(--radius)] border border-[var(--hairline)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--ink)] outline-none focus:border-[var(--accent)] focus-ring"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error && (
            <p className="mt-3 text-sm text-[var(--warn)]" role="alert">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={busy}
            className="mt-5 w-full rounded-[var(--radius)] bg-[var(--accent)] px-3 py-2.5 text-sm font-medium text-white disabled:opacity-60 focus-ring"
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
          <div className="mt-4 border-t border-[var(--hairline)] pt-3">
            <button
              type="button"
              className="text-xs text-[var(--ink-muted)] hover:text-[var(--accent)] focus-ring"
              onClick={() => setShowDefaults((v) => !v)}
            >
              {showDefaults ? "Hide PoC defaults" : "PoC defaults…"}
            </button>
            {showDefaults && (
              <div className="mt-2 rounded bg-[var(--surface-2)] p-2 text-[11px] text-[var(--ink-muted)]">
                <p>
                  After <code className="font-mono">seed_admin</code>:{" "}
                  <span className="font-mono">admin@local</span> /{" "}
                  <span className="font-mono">change-me-admin</span>
                </p>
                <button
                  type="button"
                  onClick={fillPoC}
                  className="mt-2 text-[var(--accent)] hover:underline focus-ring"
                >
                  Fill form
                </button>
              </div>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}
