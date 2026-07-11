import { Info } from "lucide-react";

// A small ⓘ that reveals a plain-language explanation on hover/focus (FN-2) — the
// reference desk puts one on every parameter so non-options clients aren't left guessing.
// Single tooltip channel: the styled bubble shows on hover AND on focus (a tap focuses the
// span, covering touch); aria-label carries the text once for screen readers. No native
// `title` (would double up with the bubble on desktop and re-announce for SR).
// `down` opens the bubble downward (top-full) instead of upward — needed inside a header row of a
// horizontally-scrollable grid, where an upward bubble is clipped by the overflow box / the bars above.
export function InfoDot({ tip, className, down }: { tip: string; className?: string; down?: boolean }) {
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
        className={
          "pointer-events-none absolute left-1/2 z-40 hidden w-56 -translate-x-1/2 rounded-md border border-line bg-surface px-2 py-1.5 text-[10px] font-normal normal-case leading-snug text-slate-200 shadow-xl group-hover:block group-focus-within:block " +
          (down ? "top-full mt-1" : "bottom-full mb-1")
        }
      >
        {tip}
      </span>
    </span>
  );
}
