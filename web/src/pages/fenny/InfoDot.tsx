import { Info } from "lucide-react";

// A small ⓘ that reveals a plain-language explanation on hover/focus (FN-2) — the
// reference desk puts one on every parameter so non-options clients aren't left guessing.
// Single tooltip channel: the styled bubble shows on hover AND on focus (a tap focuses the
// span, covering touch); aria-label carries the text once for screen readers. No native
// `title` (would double up with the bubble on desktop and re-announce for SR).
export function InfoDot({ tip, className }: { tip: string; className?: string }) {
  if (!tip) return null;
  return (
    <span
      className={"group relative inline-flex align-middle focus:outline-none " + (className ?? "")}
      tabIndex={0}
      role="note"
      aria-label={tip}
    >
      <Info
        size={11}
        className="cursor-help text-slate-500 transition-colors hover:text-accent-100 group-focus:text-accent-100"
        aria-hidden
      />
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-1 hidden w-56 -translate-x-1/2 rounded-md border border-line bg-surface px-2 py-1.5 text-[10px] leading-snug text-slate-200 shadow-xl group-hover:block group-focus-within:block"
      >
        {tip}
      </span>
    </span>
  );
}
