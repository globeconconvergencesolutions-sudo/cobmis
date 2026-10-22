export function PageHeader({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-2 border-b border-[var(--hairline)] px-4 py-3">
      <div>
        <h1 className="font-display text-2xl text-[var(--ink)]">{title}</h1>
        {subtitle && <p className="mt-0.5 text-xs text-[var(--ink-muted)]">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}
