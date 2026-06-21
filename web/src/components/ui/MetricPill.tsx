import type { ReactNode } from "react";
import { cn } from "../../lib/format";

/** Compact labeled stat used in headers / summary strips. */
export function MetricPill({
  label,
  value,
  sub,
  className,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded-lg border border-line bg-canvas px-2.5 py-1.5", className)}>
      <div className="text-2xs uppercase tracking-wide text-slate-400">{label}</div>
      <div className="tnum text-sm font-semibold leading-tight text-brand-900">{value}</div>
      {sub != null && <div className="text-2xs text-slate-400">{sub}</div>}
    </div>
  );
}
