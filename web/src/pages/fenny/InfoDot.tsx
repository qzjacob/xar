import { Info } from "lucide-react";

// A small ⓘ that reveals a plain-language explanation on hover/focus (FN-2) — the
// reference desk puts one on every parameter so non-options clients aren't left guessing.
// Pure CSS hover bubble (no positioning lib); also sets `title` for touch / a11y.
export function InfoDot({ tip, className }: { tip: string; className?: string }) {
  if (!tip) return null;
  return (
    <span className={"group relative inline-flex align-middle " + (className ?? "")}>
      <Info
        size={11}
        className="cursor-help text-slate-500 transition-colors hover:text-accent-100"
        aria-hidden
      />
      <span className="sr-only">{tip}</span>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-1 hidden w-56 -translate-x-1/2 rounded-md border border-line bg-surface px-2 py-1.5 text-[10px] leading-snug text-slate-200 shadow-xl group-hover:block group-focus-within:block"
      >
        {tip}
      </span>
    </span>
  );
}
