import { useEffect, useState, type ReactNode } from "react";
import { cn } from "../../lib/format";
import { Card } from "../../components/ui";

/** Small fetch hook with loading/error + manual reload (for action buttons). */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[] = [],
): { data: T | null; loading: boolean; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let on = true;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => {
        if (on) {
          setData(d);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (on) {
          setError(String(e));
          setLoading(false);
        }
      });
    return () => {
      on = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, loading, error, reload: () => setTick((t) => t + 1) };
}

/** Page header band shared by all ops console pages. */
export function OpsHeader({
  title,
  titleCn,
  subtitle,
  icon,
  right,
}: {
  title: string;
  titleCn?: string;
  subtitle?: ReactNode;
  icon?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="mb-5 flex items-start justify-between gap-4">
      <div className="flex items-start gap-3">
        {icon && (
          <span className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface text-accent">
            {icon}
          </span>
        )}
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <h1 className="text-lg font-semibold tracking-tight text-brand-900">{title}</h1>
            {titleCn && <span className="text-xs text-slate-400">{titleCn}</span>}
          </div>
          {subtitle && <div className="mt-0.5 text-2xs text-slate-400">{subtitle}</div>}
        </div>
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  );
}

export function OpsLoading() {
  return (
    <div className="flex h-64 items-center justify-center text-sm text-slate-400">Loading…</div>
  );
}

export function OpsError({ error }: { error: string }) {
  return (
    <Card className="p-6 text-center">
      <div className="text-sm font-semibold text-neg">加载失败 / Failed to load</div>
      <div className="mt-1 text-2xs text-slate-400">{error}</div>
    </Card>
  );
}

const STATUS_DOT: Record<string, string> = {
  ok: "bg-pos",
  done: "bg-pos",
  degraded: "bg-warn",
  empty: "bg-warn",
  unconfigured: "bg-slate-300",
  fail: "bg-neg",
  started: "bg-accent",
};

/** Status indicator dot for selftest / source / connector states. */
export function StatusDot({ status, className }: { status: string; className?: string }) {
  return <span className={cn("inline-block h-2 w-2 rounded-full", STATUS_DOT[status] ?? "bg-slate-300", className)} />;
}

export function statusChip(status: string): string {
  switch (status) {
    case "ok":
    case "done":
      return "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20";
    case "degraded":
    case "empty":
      return "bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/20";
    case "fail":
      return "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20";
    case "started":
      return "bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20";
    default:
      return "bg-slate-100 text-slate-500 ring-1 ring-inset ring-slate-200";
  }
}

/** Standard ops page width container. */
export function OpsContainer({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-[1200px]">{children}</div>;
}
