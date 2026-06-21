import type { ReactNode } from "react";
import { cn } from "../../lib/format";

/** Small pill. Pass tone classes via className (see format.ts chip helpers). */
export function Badge({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-2xs font-medium",
        className,
      )}
    >
      {children}
    </span>
  );
}
