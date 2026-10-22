"use client";

import { useEffect, useState } from "react";

export type ProgressStep = { step: string; label: string };

type Props = {
  steps: ProgressStep[];
  active: boolean;
  scopeLabel?: string | null;
  onCancel?: () => void;
};

function formatElapsed(ms: number) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m}:${String(r).padStart(2, "0")}` : `0:${String(r).padStart(2, "0")}`;
}

export function AskProgress({ steps, active, scopeLabel, onCancel }: Props) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!active) return;
    const start = Date.now();
    setElapsed(0);
    const t = setInterval(() => setElapsed(Date.now() - start), 250);
    return () => clearInterval(t);
  }, [active]);

  if (!active && steps.length === 0) return null;

  const visible = steps.filter((s) => s.step !== "heartbeat").slice(-8);
  const heartbeat = [...steps].reverse().find((s) => s.step === "heartbeat");
  const current = steps[steps.length - 1];
  const seconds = Math.floor(elapsed / 1000);

  return (
    <div className="ask-progress mt-2 rounded-[var(--radius)] border border-[var(--hairline)] bg-[var(--surface-2)] p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 text-xs font-medium text-[var(--ink)]">
          <span className="break-words">{current?.label || "Working…"}</span>
          <span className="ml-2 font-mono text-[var(--ink-muted)]">
            {formatElapsed(elapsed)}
          </span>
        </div>
        {active && onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="shrink-0 rounded border border-[var(--hairline)] bg-[var(--surface)] px-2 py-0.5 text-[11px] text-[var(--ink-muted)] hover:border-[var(--warn)] hover:text-[var(--warn)]"
          >
            Cancel
          </button>
        )}
      </div>

      {scopeLabel && (
        <p className="mt-1.5 text-[11px] text-[var(--accent)]">Scope: {scopeLabel}</p>
      )}

      {active && seconds >= 45 && seconds < 90 && (
        <p className="mt-2 text-[11px] leading-snug text-[var(--ink-muted)]">
          Still running on the local model — this machine can take a minute. You can Cancel and
          retry with a shorter question.
        </p>
      )}
      {active && seconds >= 90 && (
        <p className="mt-2 text-[11px] leading-snug text-[var(--warn)]">
          This is taking longer than usual. Cancel to free the model, then retry. A previous stuck
          ask can block new ones.
        </p>
      )}

      <ul className="mt-2 space-y-1">
        {visible.map((s, i) => {
          const isLast = i === visible.length - 1 && active && current?.step !== "heartbeat";
          const done = i < visible.length - 1 || !active;
          return (
            <li
              key={`${s.step}-${i}`}
              className={`flex items-start gap-2 text-[12px] ${
                isLast ? "text-[var(--accent)] progress-pulse" : "text-[var(--ink-muted)]"
              }`}
            >
              <span
                className={`mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                  done && !isLast
                    ? "bg-[var(--accent)]"
                    : isLast
                      ? "bg-[var(--accent)]"
                      : "bg-[var(--hairline)]"
                }`}
              />
              <span className="min-w-0 break-words">{s.label}</span>
            </li>
          );
        })}
        {active && heartbeat && (
          <li className="flex items-start gap-2 text-[12px] text-[var(--ink-muted)]">
            <span className="mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent)] progress-pulse" />
            <span className="min-w-0 break-words">{heartbeat.label}</span>
          </li>
        )}
      </ul>
      {active && (
        <div className="mt-3 space-y-2" aria-hidden>
          <div className="skeleton h-3 w-[92%]" />
          <div className="skeleton h-3 w-[78%]" />
          <div className="skeleton h-3 w-[64%]" />
        </div>
      )}
    </div>
  );
}
