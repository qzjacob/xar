import { clsx, type ClassValue } from "clsx";
import type { Polarity, SegmentRegime } from "../types";

/** Tailwind class joiner. */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}

// --- numbers ---------------------------------------------------------------
export function fmtPct(n: number, digits = 1): string {
  const s = n.toFixed(digits);
  return `${n > 0 ? "+" : ""}${s}%`;
}

export function fmtSigned(n: number, digits = 0): string {
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}`;
}

/** Market cap in USD bn → "$1.2T" / "$340B" / "$12B". */
export function fmtMktCap(bn: number): string {
  if (bn >= 1000) return `$${(bn / 1000).toFixed(2)}T`;
  if (bn >= 100) return `$${bn.toFixed(0)}B`;
  return `$${bn.toFixed(1)}B`;
}

export function fmtScore(n: number): string {
  return Math.round(n).toString();
}

// --- dates -----------------------------------------------------------------
export function relTime(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = Math.max(0, now - then);
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return `${Math.floor(d / 7)}w ago`;
}

export function fmtDate(iso: string): string {
  const d = new Date(iso + (iso.length <= 10 ? "T00:00:00" : ""));
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function fmtWeekday(iso: string): string {
  const d = new Date(iso + (iso.length <= 10 ? "T00:00:00" : ""));
  return d.toLocaleDateString("en-US", { weekday: "short" });
}

export function daysUntil(iso: string): number {
  // Compare calendar days (both normalized to local midnight) so the
  // today / "in Nd" boundary doesn't depend on the current time of day.
  const target = new Date(iso + "T00:00:00");
  const now = new Date();
  const midnightNow = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((target.getTime() - midnightNow.getTime()) / 86400000);
}

// --- color scales ----------------------------------------------------------
type RGB = [number, number, number];
const RED: RGB = [220, 38, 38]; // #DC2626
const AMBER: RGB = [217, 119, 6]; // #D97706
const GREEN: RGB = [22, 163, 74]; // #16A34A

function lerp(a: RGB, b: RGB, t: number): RGB {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];
}

/** Map a 0..1 value across red→amber→green. */
function ramp(t: number): RGB {
  const x = Math.min(1, Math.max(0, t));
  return x < 0.5 ? lerp(RED, AMBER, x * 2) : lerp(AMBER, GREEN, (x - 0.5) * 2);
}

export type HeatScheme = "divergent" | "good-high" | "good-low";

/**
 * Heat background + text color for a numeric cell.
 * - divergent: value is -100..100 (e.g. momentum / revisions)
 * - good-high: 0..100 where higher is better (e.g. alpha, supply tightness)
 * - good-low:  0..100 where higher is worse (e.g. valuation, crowding)
 */
export function heat(
  value: number,
  scheme: HeatScheme = "divergent",
  alpha = 0.16,
): { backgroundColor: string; color: string } {
  let t: number;
  if (scheme === "divergent") t = (value + 100) / 200;
  else if (scheme === "good-low") t = 1 - value / 100;
  else t = value / 100;
  const [r, g, b] = ramp(t);
  // text color: lean to the strong end for legibility
  const strong = t < 0.42 ? RED : t > 0.58 ? GREEN : AMBER;
  return {
    backgroundColor: `rgba(${r}, ${g}, ${b}, ${alpha})`,
    color: `rgb(${Math.round(strong[0] * 0.82)}, ${Math.round(strong[1] * 0.82)}, ${Math.round(
      strong[2] * 0.82,
    )})`,
  };
}

/** Solid token color (hex) for a polarity. */
export function polarityHex(p: Polarity): string {
  return p === "positive" ? "#16A34A" : p === "negative" ? "#DC2626" : "#64748b";
}

/** Tailwind text class for a polarity / signed number. */
export function signClass(n: number): string {
  return n > 0 ? "text-pos" : n < 0 ? "text-neg" : "text-slate-500";
}

export function polarityClass(p: Polarity): string {
  return p === "positive" ? "text-pos" : p === "negative" ? "text-neg" : "text-slate-500";
}

/** Soft chip classes (bg + text + ring) for a polarity. */
export function polarityChip(p: Polarity): string {
  if (p === "positive") return "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20";
  if (p === "negative") return "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20";
  return "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200";
}

/** Dot color class for a segment regime. */
export function regimeDot(r: SegmentRegime): string {
  switch (r) {
    case "accelerating":
      return "bg-pos";
    case "expansion":
      return "bg-accent";
    case "peaking":
      return "bg-warn";
    case "cooling":
      return "bg-orange-400";
    case "trough":
      return "bg-neg";
  }
}

export function regimeChip(r: SegmentRegime): string {
  switch (r) {
    case "accelerating":
      return "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20";
    case "expansion":
      return "bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20";
    case "peaking":
      return "bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/20";
    case "cooling":
      return "bg-orange-50 text-orange-700 ring-1 ring-inset ring-orange-200";
    case "trough":
      return "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20";
  }
}

export function severityChip(s: "high" | "medium" | "low"): string {
  if (s === "high") return "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20";
  if (s === "medium") return "bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/20";
  return "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200";
}

/** Market flag short label. */
export function marketLabel(m: string): string {
  return m === "ALL" ? "Global" : m;
}
