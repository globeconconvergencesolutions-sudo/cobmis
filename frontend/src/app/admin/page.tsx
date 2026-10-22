"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";

type Tab = "health" | "jobs" | "users" | "audit";

function relativeTime(iso: string | null | undefined) {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const sec = Math.round((Date.now() - t) / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export default function AdminPage() {
  const { user, csrf } = useAuth();
  const [tab, setTab] = useState<Tab>("health");
  const [toast, setToast] = useState<string | null>(null);

  if (user && user.role !== "admin") {
    return <p className="p-6 text-[var(--warn)]">Admin only.</p>;
  }

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Admin" subtitle="Health, jobs, users, and audit">
        {toast && (
          <span className="rounded bg-[var(--accent-wash)] px-2 py-1 text-xs text-[var(--accent)]">
            {toast}
          </span>
        )}
      </PageHeader>
      <div className="flex gap-1 border-b border-[var(--hairline)] px-4 text-xs font-medium">
        {(["health", "jobs", "users", "audit"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`rounded-t px-3 py-2 capitalize focus-ring ${
              tab === t
                ? "bg-[var(--accent-wash)] text-[var(--accent)]"
                : "text-[var(--ink-muted)] hover:bg-[var(--surface-2)]"
            }`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {tab === "health" && <HealthPanel />}
        {tab === "jobs" && <JobsPanel csrf={csrf} onToast={flash} />}
        {tab === "users" && <UsersPanel csrf={csrf} onToast={flash} />}
        {tab === "audit" && <AuditPanel />}
      </div>
    </div>
  );
}

function HealthPanel() {
  const [h, setH] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    api<Record<string, unknown>>("/api/admin/health").then(setH).catch(console.error);
  }, []);
  if (!h) return <p className="text-sm text-[var(--ink-muted)]">Loading…</p>;
  return (
    <dl className="grid max-w-lg grid-cols-2 gap-2 text-sm">
      {Object.entries(h).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-[var(--ink-muted)]">{k}</dt>
          <dd className="break-all font-mono text-xs">
            {typeof v === "object" ? JSON.stringify(v) : String(v)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function JobsPanel({
  csrf,
  onToast,
}: {
  csrf: string | null;
  onToast: (m: string) => void;
}) {
  const [jobs, setJobs] = useState<Array<Record<string, unknown>>>([]);
  const load = useCallback(() => {
    api<{ jobs: Array<Record<string, unknown>> }>("/api/admin/jobs")
      .then((r) => setJobs(r.jobs))
      .catch(console.error);
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [load]);

  async function start(kind: "scrape" | "ingest") {
    if (!csrf) return;
    await api("/api/admin/jobs", {
      method: "POST",
      csrf,
      body: JSON.stringify({ kind }),
    });
    onToast(`${kind} job queued`);
    load();
  }

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => start("scrape")}
          className="rounded-[var(--radius)] border border-[var(--hairline)] px-3 py-1.5 text-sm hover:bg-[var(--surface-2)] focus-ring"
        >
          Run scrape (--limit 3)
        </button>
        <button
          type="button"
          onClick={() => start("ingest")}
          className="rounded-[var(--radius)] border border-[var(--hairline)] px-3 py-1.5 text-sm hover:bg-[var(--surface-2)] focus-ring"
        >
          Run ingest
        </button>
      </div>
      <ul className="mt-4 space-y-2">
        {jobs.map((j) => (
          <li
            key={String(j.job_id)}
            className="rounded-[var(--radius)] border border-[var(--hairline)] p-3 text-sm"
          >
            <div className="flex flex-wrap items-center gap-2 font-mono text-xs">
              <span>#{String(j.job_id)}</span>
              <span>{String(j.kind)}</span>
              <StatusBadge status={String(j.status)} />
              <span className="text-[var(--ink-muted)]">
                {relativeTime(j.created_at as string)}
              </span>
            </div>
            {j.detail ? (
              <div className="mt-1 text-xs text-[var(--ink-muted)]">{String(j.detail)}</div>
            ) : null}
            {j.log_tail ? (
              <pre className="mt-2 max-h-32 overflow-auto bg-[var(--surface-2)] p-2 text-[10px]">
                {String(j.log_tail)}
              </pre>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function UsersPanel({
  csrf,
  onToast,
}: {
  csrf: string | null;
  onToast: (m: string) => void;
}) {
  const [users, setUsers] = useState<Array<Record<string, unknown>>>([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("viewer");

  const load = useCallback(() => {
    api<{ users: Array<Record<string, unknown>> }>("/api/admin/users")
      .then((r) => setUsers(r.users))
      .catch(console.error);
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    if (!csrf) return;
    await api("/api/admin/users", {
      method: "POST",
      csrf,
      body: JSON.stringify({ email, display_name: name, password, role }),
    });
    setEmail("");
    setName("");
    setPassword("");
    onToast("User created");
    load();
  }

  return (
    <div className="max-w-xl">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase text-[var(--ink-muted)]">
            <th className="py-1">Email</th>
            <th>Role</th>
            <th>Active</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={String(u.user_id)} className="border-t border-[var(--hairline)]">
              <td className="py-1.5">{String(u.email)}</td>
              <td className="font-mono text-xs">{String(u.role)}</td>
              <td>{u.is_active ? "yes" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <form onSubmit={onCreate} className="mt-6 space-y-2 border-t border-[var(--hairline)] pt-4">
        <h3 className="text-sm font-semibold">Create user</h3>
        <input
          className="w-full rounded border border-[var(--hairline)] bg-[var(--bg)] px-2 py-1.5 text-sm focus-ring"
          placeholder="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <input
          className="w-full rounded border border-[var(--hairline)] bg-[var(--bg)] px-2 py-1.5 text-sm focus-ring"
          placeholder="display name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <input
          type="password"
          className="w-full rounded border border-[var(--hairline)] bg-[var(--bg)] px-2 py-1.5 text-sm focus-ring"
          placeholder="password (min 8)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={8}
          required
        />
        <select
          className="w-full rounded border border-[var(--hairline)] bg-[var(--bg)] px-2 py-1.5 text-sm focus-ring"
          value={role}
          onChange={(e) => setRole(e.target.value)}
        >
          <option value="viewer">viewer</option>
          <option value="analyst">analyst</option>
          <option value="admin">admin</option>
        </select>
        <button
          type="submit"
          className="rounded bg-[var(--accent)] px-3 py-1.5 text-sm text-white focus-ring"
        >
          Create
        </button>
      </form>
    </div>
  );
}

function AuditPanel() {
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  useEffect(() => {
    api<{ events: Array<Record<string, unknown>> }>("/api/admin/audit")
      .then((r) => setEvents(r.events))
      .catch(console.error);
  }, []);

  return (
    <div>
      <a href="/api/admin/audit?csv_export=true" className="text-sm text-[var(--accent)]">
        Download CSV
      </a>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-[var(--surface-2)] text-left text-[11px] uppercase text-[var(--ink-muted)]">
              <th className="px-2 py-1.5">When</th>
              <th className="px-2 py-1.5">User</th>
              <th className="px-2 py-1.5">Route</th>
              <th className="px-2 py-1.5">Question</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={String(e.event_id)} className="border-t border-[var(--hairline)]">
                <td className="whitespace-nowrap px-2 py-1.5 font-mono text-[11px] text-[var(--ink-muted)]">
                  {relativeTime(e.created_at as string)}
                </td>
                <td className="px-2 py-1.5 text-xs">{String(e.email || e.user_id)}</td>
                <td className="px-2 py-1.5">
                  <span className={`route-chip ${String(e.route || "")}`}>
                    {String(e.route || "—")}
                  </span>
                </td>
                <td className="max-w-md truncate px-2 py-1.5 text-xs">{String(e.question)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
