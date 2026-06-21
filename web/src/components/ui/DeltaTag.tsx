import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { cn, fmtPct, signClass } from "../../lib/format";

/** Signed percentage with a directional arrow and semantic color. */
export function DeltaTag({
  value,
  digits = 1,
  className,
  size = 12,
}: {
  value: number;
  digits?: number;
  className?: string;
  size?: number;
}) {
  const Icon = value >= 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className={cn(
        "tnum inline-flex items-center gap-0.5 text-xs font-semibold",
        signClass(value),
        className,
      )}
    >
      <Icon size={size} strokeWidth={2.5} />
      {fmtPct(value, digits)}
    </span>
  );
}
