import type { ReactNode } from "react";
import { cn } from "../../lib/format";

/** Card header row: icon + title (+ CN subtitle) on the left, actions on the right. */
export function SectionHeader({
  title,
  titleCn,
  icon,
  right,
  className,
}: {
  title: string;
  titleCn?: string;
  icon?: ReactNode;
  right?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 border-b border-line px-4 py-3",
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        {icon && <span className="text-slate-400">{icon}</span>}
        <h3 className="truncate text-sm font-semibold text-brand-900">{title}</h3>
        {titleCn && <span className="truncate text-2xs text-slate-400">{titleCn}</span>}
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  );
}
