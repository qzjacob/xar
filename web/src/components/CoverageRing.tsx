import { cn, heat } from "../lib/format";
import { COVERAGE_DIMS, type CompanyCoverage } from "../types-thesis";

/**
 * Coverage 360 — compact radial composite + 16 per-dimension mini bars
 * (name_cn / rows / score in tooltips). Sits near the company header;
 * callers hide it entirely when coverage is null.
 */
export function CoverageRing({
  coverage,
  className,
}: {
  coverage: CompanyCoverage;
  className?: string;
}) {
  const composite = Math.max(0, Math.min(1, coverage.composite ?? 0));
  const tone = heat(composite * 100, "good-high", 1).color;
  const R = 15.5;
  const CIRC = 2 * Math.PI * R;

  return (
    <div className={cn("flex items-center gap-3", className)}>
      {/* radial composite */}
      <div
        className="relative h-11 w-11 shrink-0"
        title={`覆盖度综合分 Coverage composite · ${Math.round(composite * 100)}/100`}
      >
        <svg viewBox="0 0 40 40" className="h-11 w-11 -rotate-90">
          <circle
            cx={20}
            cy={20}
            r={R}
            fill="none"
            stroke="rgba(148,163,184,0.18)"
            strokeWidth={4}
          />
          <circle
            cx={20}
            cy={20}
            r={R}
            fill="none"
            stroke={tone}
            strokeWidth={4}
            strokeLinecap="round"
            strokeDasharray={`${(CIRC * composite).toFixed(2)} ${CIRC.toFixed(2)}`}
          />
        </svg>
        <span
          className="tnum absolute inset-0 flex items-center justify-center text-2xs font-semibold"
          style={{ color: tone }}
        >
          {Math.round(composite * 100)}
        </span>
      </div>

      {/* per-dimension mini bars */}
      <div className="min-w-0">
        <div className="text-2xs uppercase tracking-wide text-slate-400">
          Coverage 360 <span className="normal-case">覆盖度</span>
        </div>
        <div className="mt-1 flex items-end gap-[3px]">
          {COVERAGE_DIMS.map((d) => {
            const cell = coverage.dims?.[d.key];
            const score = Math.max(0, Math.min(1, cell?.score ?? 0));
            return (
              <span
                key={d.key}
                title={`${d.cn} ${d.en} · ${cell?.n ?? 0} rows · ${Math.round(score * 100)}%`}
                className="flex h-5 w-[7px] cursor-help items-end overflow-hidden rounded-[2px] bg-surface-2"
              >
                {score > 0 && (
                  <span
                    className="w-full rounded-[1px]"
                    style={{
                      height: `${Math.max(10, score * 100)}%`,
                      backgroundColor: heat(score * 100, "good-high", 0.9).backgroundColor,
                    }}
                  />
                )}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}
