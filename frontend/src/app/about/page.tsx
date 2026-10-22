"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";

type Catalog = {
  about_body?: string | null;
  about_approved?: boolean;
  coverage: Array<{ fiscal_year: string; period_type: string; documents: number; pages: number }>;
  counts: Record<string, number>;
  documents: Array<{
    doc_id: number;
    title: string;
    fiscal_year: string | null;
    period_type: string | null;
    page_count: number;
    collection: string;
    ingested_at: string | null;
    source_url: string | null;
  }>;
  metrics: Array<{ metric: string; description?: string; label?: string; unit?: string }>;
  limitations: string[];
  examples: string[];
};

const TOC = [
  { id: "coverage", label: "Coverage" },
  { id: "documents", label: "Documents" },
  { id: "metrics", label: "Metrics" },
  { id: "notes", label: "Notes" },
  { id: "try", label: "Try asking" },
];

export default function AboutPage() {
  const [data, setData] = useState<Catalog | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [metricQ, setMetricQ] = useState("");

  useEffect(() => {
    api<Catalog>("/api/catalog")
      .then(setData)
      .catch((e) => setErr(e.message));
  }, []);

  const filteredMetrics = useMemo(() => {
    if (!data?.metrics) return [];
    const q = metricQ.trim().toLowerCase();
    if (!q) return data.metrics;
    return data.metrics.filter(
      (m) =>
        m.metric.toLowerCase().includes(q) ||
        (m.description || m.label || "").toLowerCase().includes(q),
    );
  }, [data, metricQ]);

  if (err) return <p className="p-6 text-[var(--warn)]">{err}</p>;
  if (!data) {
    return (
      <div className="space-y-3 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="About this data"
        subtitle="Same SQL-backed catalog as the CLI — one source of truth."
      />
      <div className="flex min-h-0 flex-1">
        <nav className="hidden w-40 shrink-0 overflow-y-auto border-r border-[var(--hairline)] p-3 text-xs lg:block">
          <div className="sticky top-3 space-y-1">
            {TOC.map((t) => (
              <a
                key={t.id}
                href={`#${t.id}`}
                className="block rounded px-2 py-1 text-[var(--ink-muted)] no-underline hover:bg-[var(--surface-2)] hover:text-[var(--accent)]"
              >
                {t.label}
              </a>
            ))}
          </div>
        </nav>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-6 sm:px-6">
            {data.about_body && (
              <section className="rounded-[var(--radius)] border border-[var(--hairline)] bg-[var(--surface)] p-4 text-sm leading-relaxed">
                {data.about_body}
                {!data.about_approved && (
                  <span className="mt-2 block text-xs text-[var(--ink-muted)]">(draft)</span>
                )}
              </section>
            )}

            <section id="coverage" className="mt-8 scroll-mt-4">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
                Coverage
              </h2>
              <ul className="mt-2 space-y-1 text-sm font-mono">
                {data.coverage.map((c) => (
                  <li key={`${c.fiscal_year}-${c.period_type}`}>
                    {c.fiscal_year} {c.period_type}: {c.documents} doc(s), {c.pages} pages
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-[var(--ink-muted)]">
                Docs {data.counts.documents ?? "—"} · Chunks {data.counts.chunks ?? "—"} · SQL facts{" "}
                {data.counts.sql_facts ?? "—"}
              </p>
            </section>

            <section id="documents" className="mt-8 scroll-mt-4">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
                Documents
              </h2>
              <ul className="mt-2 divide-y divide-[var(--hairline)] rounded-[var(--radius)] border border-[var(--hairline)] bg-[var(--surface)]">
                {data.documents.map((d) => (
                  <li key={d.doc_id} className="px-3 py-2 text-sm">
                    <div className="font-medium">{d.title}</div>
                    <div className="text-xs text-[var(--ink-muted)]">
                      {d.fiscal_year} {d.period_type} · {d.page_count} pages · {d.collection}
                      {d.source_url && (
                        <>
                          {" · "}
                          <a href={d.source_url} target="_blank" rel="noreferrer">
                            cob.go.ke
                          </a>
                        </>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            <section id="metrics" className="mt-8 scroll-mt-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
                  Metric dictionary
                </h2>
                <input
                  value={metricQ}
                  onChange={(e) => setMetricQ(e.target.value)}
                  placeholder="Filter metrics…"
                  className="rounded border border-[var(--hairline)] bg-[var(--bg)] px-2 py-1 text-xs focus-ring"
                />
              </div>
              <ul className="mt-2 max-h-72 space-y-1 overflow-y-auto text-sm">
                {filteredMetrics.map((m) => (
                  <li key={m.metric} className="font-mono text-[12px]">
                    <span className="text-[var(--accent)]">{m.metric}</span>
                    <span className="text-[var(--ink-muted)]">
                      {" "}
                      — {m.description || m.label}
                    </span>
                    {m.unit ? ` [${m.unit}]` : ""}
                  </li>
                ))}
              </ul>
            </section>

            {data.limitations?.length > 0 && (
              <section id="notes" className="mt-8 scroll-mt-4">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
                  Notes
                </h2>
                <ul className="mt-2 space-y-1.5 text-sm text-[var(--ink-muted)]">
                  {data.limitations.map((l) => (
                    <li
                      key={l}
                      className="rounded border border-[var(--hairline)] bg-[var(--surface)] px-3 py-2"
                    >
                      {l}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section id="try" className="mb-12 mt-8 scroll-mt-4">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--ink-muted)]">
                Try asking
              </h2>
              <ul className="mt-2 space-y-1">
                {data.examples.map((q) => (
                  <li key={q}>
                    <Link
                      href={`/chat?q=${encodeURIComponent(q)}`}
                      className="text-sm text-[var(--accent)]"
                    >
                      {q}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
