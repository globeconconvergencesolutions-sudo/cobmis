"use client";

type Props = {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  side?: "left" | "right";
};

export function Sheet({ open, title, onClose, children, side = "right" }: Props) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex">
      <button
        type="button"
        aria-label="Close overlay"
        className="absolute inset-0 bg-[var(--ink)]/25"
        onClick={onClose}
      />
      <aside
        className={`relative z-10 flex h-full w-[min(100%,360px)] flex-col bg-[var(--surface)] shadow-lg evidence-enter ${
          side === "left" ? "mr-auto" : "ml-auto"
        }`}
      >
        <div className="flex items-center justify-between border-b border-[var(--hairline)] px-3 py-2">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-xs text-[var(--ink-muted)] hover:text-[var(--ink)]"
          >
            Close
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  );
}
