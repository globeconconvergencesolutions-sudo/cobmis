export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mx-auto max-w-md px-4 py-12 text-center">
      <h2 className="text-sm font-semibold text-[var(--ink)]">{title}</h2>
      {body && <p className="mt-2 text-sm text-[var(--ink-muted)]">{body}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
