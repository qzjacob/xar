import type { ReactNode } from "react";
import { cn } from "../../lib/format";

/** Base white card: 12px radius, 1px border, soft shadow. */
export function Card({
  className,
  children,
  onClick,
}: {
  className?: string;
  children: ReactNode;
  onClick?: () => void;
}) {
  return (
    <div
      className={cn("rounded-xl border border-line bg-surface shadow-card", className)}
      onClick={onClick}
    >
      {children}
    </div>
  );
}
