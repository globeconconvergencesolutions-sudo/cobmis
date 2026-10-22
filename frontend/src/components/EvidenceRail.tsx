"use client";

import { useEffect, useRef, useState } from "react";
import type { Citation } from "@/lib/api";

type SqlBlock = {
  sql: string | null;
  columns: string[];
  rows: unknown[][];
};

type Props = {
  open: boolean;
  citations: Citation[];
  sqlCitations: Citation[];
  sql: SqlBlock | null;
  activeTag: string | null;
  route?: string | null;
  waiting?: boolean;
  waitingLabel?: string;
  onClose: () => void;
  docIdForPage?: number | null;
  page?: number | null;
  asOverlay?: boolean;
};

/** Proof panel — plain language for analysts, technical detail behind secondary tabs. */
export function EvidenceRail({
  open,
  citations,
  sqlCitations,
  sql,
  activeTag,
  route,
  waiting,
  waitingLabel,
  onClose,
  docIdForPage,
  page,
  asOverlay,
}: Props) {
  const [tab, setTab] = useState<"sources" | "numbers" | "page">("sources");
  const [showQuery, setShowQuery] = useState(false);
  const [copied, setCopied] = useState(false);
  const cardRefs = useRef<Record<string, HTMLLIElement | null>>({});

  useEffect(() => {
    if (route === "sql") setTab("numbers");
    else if (route === "rag" || route === "both" || route === "catalog") setTab("sources");
  }, [route]);

  useEffect(() => {
    if (activeTag) {
      setTab("sources");
      cardRefs.current[activeTag]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [activeTag]);

  if (!open) return null;

  const tabs = (
    [
      ["sources", "Report pages"],
      ["numbers", "Figures"],
      ["page", "PDF"],
    ] as const
  );

  const panel = (
    <aside
      className={`evidence-enter flex h-full shrink-0 flex-col border-[var(--hairline)] bg-[var(--surface)] ${
        asOverlay ? "w-full max-w-[400px] shadow-xl" : "w-[360px] border-l"
      }`}
    >
      <div className="flex items-center justify-between border-b border-[var(--hairline)] px-3 py-2.5">
        <div>
          <div className="text-sm font-semibold text-[var(--ink)]">Proof</div>
          <div className="text-[11px] text-[var(--ink-muted)]">Where this answer came from</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded px-2 py-1 text-xs text-[var(--ink-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-ring"
        >
          Close
        </button>
      </div>
      <div className="flex gap-1 border-b border-[var(--hairline)] px-2 py-1.5 text-xs font-medium">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`rounded px-2.5 py-1 focus-ring ${
              tab === id
                ? "bg-[var(--accent-wash)] text-[var(--accent)]"
                : "text-[var(--ink-muted)] hover:bg-[var(--surface-2)]"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3 text-sm">
        {waiting && (
          <p className="mb-3 text-xs text-[var(--ink-muted)]">
            {waitingLabel || "Gathering proof for this answer…"}
          </p>
        )}

        {tab === "sources" && (
          <ul className="space-y-2">
            {!waiting && !citations.length && !sqlCitations.length && (
              <li className="rounded-[var(--radius)] border border-dashed border-[var(--hairline)] p-4 text-center text-[var(--ink-muted)]">
                No report pages attached yet. Ask a question to see citations here.
              </li>
            )}
            {citations.map((c, i) => {
              const tag = c.tag || `S${i + 1}`;
              const pages = c.pages;
              const p =
                pages && pages[0] != null
                  ? pages[0] === pages[1]
                    ? `p.${pages[0]}`
                    : `p.${pages[0]}–${pages[1]}`
                  : "";
              return (
                <li
                  key={tag}
                  ref={(el) => {
                    cardRefs.current[tag] = el;
                  }}
                  className={`rounded-[var(--radius)] border border-[var(--hairline)] p-2.5 transition-colors ${
                    activeTag === tag ? "border-[var(--accent)] bg-[var(--accent-wash)]" : ""
                  }`}
                >
                  <div className="font-mono text-[11px] font-semibold text-[var(--accent)]">
                    [{tag}]
                  </div>
                  <div className="mt-0.5 text-[13px] leading-snug">{c.document}</div>
                  <div className="mt-0.5 text-[11px] text-[var(--ink-muted)]">
                    {p}
                    {c.table ? ` · ${c.table}` : ""}
                  </div>
                  {c.snippet && (
                    <p className="mt-1.5 text-[12px] leading-snug text-[var(--ink-muted)]">
                      {c.snippet.slice(0, 280)}
                    </p>
                  )}
                </li>
              );
            })}
            {sqlCitations.map((c, i) => (
              <li
                key={`sql-${i}`}
                className="rounded-[var(--radius)] border border-[var(--hairline)] p-2.5"
              >
                <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--sql)]">
                  Table source
                </div>
                <div className="mt-0.5 text-[13px]">{c.document || c.source_title}</div>
                <div className="text-[11px] text-[var(--ink-muted)]">
                  {c.pages?.[0] != null
                    ? `p.${c.pages[0]}`
                    : c.source_page != null
                      ? `p.${c.source_page}`
                      : ""}
                  {c.table ? ` · ${c.table}` : ""}
                </div>
              </li>
            ))}
          </ul>
        )}

        {tab === "numbers" && (
          <div>
            {!sql?.rows?.length && !waiting ? (
              <p className="rounded-[var(--radius)] border border-dashed border-[var(--hairline)] p-4 text-center text-[var(--ink-muted)]">
                No structured figures for this answer. Narrative answers cite report pages
                instead.
              </p>
            ) : sql?.columns?.length ? (
              <>
                <p className="mb-2 text-xs text-[var(--ink-muted)]">
                  Numbers from the validated county tables (same figures as in the COB reports).
                </p>
                <div className="overflow-x-auto rounded-[var(--radius)] border border-[var(--hairline)]">
                  <table className="w-full border-collapse text-[11px] font-mono">
                    <thead>
                      <tr>
                        {sql.columns.map((c) => (
                          <th
                            key={c}
                            className="border-b border-[var(--hairline)] bg-[var(--surface-2)] px-2 py-1.5 text-left font-semibold"
                          >
                            {c}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sql.rows.slice(0, 40).map((row, i) => (
                        <tr key={i}>
                          {row.map((cell, j) => (
                            <td
                              key={j}
                              className="border-b border-[var(--hairline)] px-2 py-1"
                            >
                              {cell == null ? "" : String(cell)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {sql.rows.length > 40 && (
                  <p className="mt-1 text-[11px] text-[var(--ink-muted)]">
                    … {sql.rows.length} rows total
                  </p>
                )}
                {sql.sql && (
                  <div className="mt-3">
                    <button
                      type="button"
                      className="text-[11px] text-[var(--ink-muted)] hover:text-[var(--accent)] focus-ring"
                      onClick={() => setShowQuery((v) => !v)}
                    >
                      {showQuery ? "Hide technical query" : "Show technical query"}
                    </button>
                    {showQuery && (
                      <div className="mt-2">
                        <div className="mb-1 flex justify-end">
                          <button
                            type="button"
                            className="text-[11px] text-[var(--accent)] focus-ring"
                            onClick={async () => {
                              await navigator.clipboard.writeText(sql.sql || "");
                              setCopied(true);
                              setTimeout(() => setCopied(false), 1500);
                            }}
                          >
                            {copied ? "Copied" : "Copy"}
                          </button>
                        </div>
                        <pre className="overflow-x-auto whitespace-pre-wrap rounded-[var(--radius)] bg-[var(--surface-2)] p-2 text-[10px] leading-relaxed text-[var(--ink-muted)]">
                          {sql.sql}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </>
            ) : null}
          </div>
        )}

        {tab === "page" && <PdfFrame docId={docIdForPage} page={page} />}
      </div>
    </aside>
  );

  if (asOverlay) {
    return (
      <div className="fixed inset-0 z-30 flex justify-end">
        <button
          type="button"
          aria-label="Close proof"
          className="absolute inset-0 bg-[var(--ink)]/30"
          onClick={onClose}
        />
        <div className="relative z-10 h-full w-[min(100%,400px)]">{panel}</div>
      </div>
    );
  }

  return panel;
}

function PdfFrame({ docId, page }: { docId?: number | null; page?: number | null }) {
  if (!docId) {
    return (
      <p className="rounded-[var(--radius)] border border-dashed border-[var(--hairline)] p-4 text-center text-[var(--ink-muted)]">
        Open a report from Library to preview pages here.
      </p>
    );
  }
  const hash = page ? `#page=${page}` : "";
  return (
    <iframe
      title="PDF preview"
      src={`/api/documents/${docId}/file${hash}`}
      className="h-[70vh] w-full rounded-[var(--radius)] border border-[var(--hairline)]"
    />
  );
}
