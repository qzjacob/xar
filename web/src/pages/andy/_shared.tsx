import { useEffect, useState, type ReactNode } from "react";
import { Card } from "../../components/ui";

/** Small fetch hook with loading/error + manual reload (mirrors ops/_shared). */
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

/** Standard andy page width container. */
export function AndyContainer({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return <div className={wide ? "mx-auto max-w-[1400px]" : "mx-auto max-w-[1200px]"}>{children}</div>;
}

/** Page header band shared by andy pages. */
export function AndyHeader({
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
          <span className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface text-andy-500">
            {icon}
          </span>
        )}
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <h1 className="text-lg font-semibold tracking-tight text-brand-900">{title}</h1>
            {titleCn && <span className="text-xs text-brand-500">{titleCn}</span>}
          </div>
          {subtitle && <div className="mt-0.5 text-2xs text-brand-500">{subtitle}</div>}
        </div>
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  );
}

export function AndyLoading({ label = "Loading…" }: { label?: string }) {
  return <div className="flex h-64 items-center justify-center text-sm text-brand-500">{label}</div>;
}

export function AndyError({ error }: { error: string }) {
  return (
    <Card className="p-6 text-center">
      <div className="text-sm font-semibold text-neg-700">加载失败 / Failed to load</div>
      <div className="mt-1 font-mono text-2xs text-brand-500">{error}</div>
    </Card>
  );
}

/** Inline degraded-state note for the 勾稽 crosswalk endpoints (may 404 while the
 * backend lands): never an error card, just a quiet placeholder. */
export function LinkUnavailable({ loading }: { loading?: boolean }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-3 py-2 text-2xs text-brand-200">
      {loading ? "勾稽数据加载中 · loading crosswalk…" : "勾稽数据暂不可用 · crosswalk API not yet available"}
    </div>
  );
}
