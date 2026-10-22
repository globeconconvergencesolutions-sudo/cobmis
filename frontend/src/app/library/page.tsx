"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";

type Doc = {
  doc_id: number;
  title: string;
  fiscal_year: string | null;
  period_type: string | null;
  page_count: number;
  file_name: string;
  source_url: string | null;
  ingested_at: string | null;
  needs_ocr_pages: number;
  collection: string;
  visibility: string;
};

export default function LibraryPage() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [selected, setSelected] = useState<Doc | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api<{ documents: Doc[] }>("/api/documents")
      .then((r) => setDocs(r.documents))
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Library" subtitle="Reports ingested on this machine" />
      <div className="flex min-h-0 flex-1">
        <div
          className={`${selected ? "hidden w-[42%] sm:block sm:w-[42%]" : "w-full"} min-w-0 overflow-y-auto border-r border-[var(--hairline)]`}
        >
          {err && <p className="p-4 text-[var(--warn)]">{err}</p>}
          {loading && (
            <div className="space-y-2 p-4">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-[80%]" />
            </div>
          )}
          {!loading && !docs.length && !err && (
            <EmptyState
              title="No documents yet"
              body="Run scrape + ingest, or open Admin → Jobs."
            />
          )}
          {!!docs.length && (
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-[var(--surface-2)]">
                <tr className="text-left text-[11px] uppercase tracking-wide text-[var(--ink-muted)]">
                  <th className="px-3 py-2 font-medium">Title</th>
                  <th className="px-3 py-2 font-medium">FY</th>
                  <th className="px-3 py-2 font-medium">Period</th>
                  <th className="px-3 py-2 font-medium">Pages</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr
                    key={d.doc_id}
                    onClick={() => setSelected(d)}
                    className={`cursor-pointer border-t border-[var(--hairline)] hover:bg-[var(--accent-wash)] ${
                      selected?.doc_id === d.doc_id ? "bg-[var(--accent-wash)]" : ""
                    }`}
                  >
                    <td className="px-3 py-2">{d.title}</td>
                    <td className="px-3 py-2 font-mono text-xs">{d.fiscal_year}</td>
                    <td className="px-3 py-2 font-mono text-xs">{d.period_type}</td>
                    <td className="px-3 py-2 font-mono text-xs">{d.page_count}</td>
                    <td className="px-3 py-2 text-xs text-[var(--ink-muted)]">
                      {d.needs_ocr_pages ? `${d.needs_ocr_pages} OCR skip` : "Ready"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {selected && (
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex flex-wrap items-center gap-2 border-b border-[var(--hairline)] px-3 py-2 text-sm">
              <button
                type="button"
                className="text-xs text-[var(--ink-muted)] sm:hidden"
                onClick={() => setSelected(null)}
              >
                ← List
              </button>
              <span className="min-w-0 flex-1 truncate font-medium">{selected.title}</span>
              <a
                href={`/api/documents/${selected.doc_id}/file`}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-[var(--accent)]"
              >
                Open PDF
              </a>
              {selected.source_url && (
                <a
                  href={selected.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs"
                >
                  cob.go.ke
                </a>
              )}
              <button
                type="button"
                className="hidden text-xs text-[var(--ink-muted)] sm:inline"
                onClick={() => setSelected(null)}
              >
                Close
              </button>
            </div>
            <iframe
              title={selected.title}
              src={`/api/documents/${selected.doc_id}/file`}
              className="min-h-0 flex-1 w-full bg-[var(--surface-2)]"
            />
          </div>
        )}
      </div>
    </div>
  );
}
