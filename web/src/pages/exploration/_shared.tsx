import { cn } from "../../lib/format";

export { useAsync } from "../ops/_shared";

/** maturity -> chip classes */
export function maturityChip(m: string): string {
  switch (m) {
    case "accelerating":
      return "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20";
    case "maturing":
      return "bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/20";
    case "emerging":
    default:
      return "bg-explore-50 text-explore-700 ring-1 ring-inset ring-explore/20";
  }
}

export function horizonLabel(h: string): string {
  return h === "near" ? "0–1y" : h === "long" ? "3y+" : "1–3y";
}

/** small 0–100 momentum bar (indigo) */
export function MomentumBar({ value, className }: { value: number; className?: string }) {
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-2", className)}>
      <div
        className="h-full rounded-full bg-explore"
        style={{ width: `${Math.max(2, Math.min(100, value))}%` }}
      />
    </div>
  );
}
