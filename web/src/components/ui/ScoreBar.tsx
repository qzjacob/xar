import { cn, heat, type HeatScheme } from "../../lib/format";

/**
 * Horizontal 0..100 (or -100..100 for "divergent") meter, heat-colored.
 * The fill width reflects the position within the scheme so good/bad reads
 * consistently with the heatmap cells.
 */
export function ScoreBar({
  value,
  scheme = "good-high",
  className,
  height = 6,
}: {
  value: number;
  scheme?: HeatScheme;
  className?: string;
  height?: number;
}) {
  const t =
    scheme === "divergent"
      ? (value + 100) / 200
      : scheme === "good-low"
        ? 1 - value / 100
        : value / 100;
  const pct = Math.min(100, Math.max(0, t * 100));
  const fill = heat(value, scheme, 0.9).backgroundColor;
  return (
    <div
      className={cn("w-full overflow-hidden rounded-full bg-surface-2", className)}
      style={{ height }}
    >
      <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: fill }} />
    </div>
  );
}
