const STYLES: Record<string, string> = {
  queued: "bg-[var(--surface-2)] text-[var(--ink-muted)]",
  running: "bg-[var(--accent-wash)] text-[var(--accent)]",
  succeeded: "bg-[#e8f5ee] text-[var(--accent)]",
  failed: "bg-[#fcebe8] text-[var(--warn)]",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
        STYLES[status] || STYLES.queued
      }`}
    >
      {status}
    </span>
  );
}
