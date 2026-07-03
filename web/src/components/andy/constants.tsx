// Andy domain constants — hardness ladder, verdict lamps, source grades and the
// forced「未识别 · 勿作因果」watermark. This epistemic discipline is the product's
// signature: every SOFT value/chart MUST carry the watermark banner.
import type { ReactNode } from "react";
import { ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { cn } from "../../lib/format";
import { Badge } from "../ui";
import type { ClaimStatus, Hardness } from "../../types-andy";

// --- hardness ---------------------------------------------------------------
export const HARDNESS_META: Record<
  Hardness,
  { en: string; cn: string; chip: string; dot: string; text: string }
> = {
  hard: {
    en: "Hard", cn: "硬",
    chip: "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/25",
    dot: "bg-pos", text: "text-pos-700",
  },
  medium: {
    en: "Medium", cn: "中",
    chip: "bg-andy-50 text-andy-500 ring-1 ring-inset ring-andy/25",
    dot: "bg-andy", text: "text-andy-500",
  },
  soft: {
    en: "Soft", cn: "软·未识别",
    chip: "bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/25",
    dot: "bg-warn", text: "text-warn-700",
  },
  wall: {
    en: "Wall", cn: "承重墙",
    chip: "bg-surface-2 text-slate-400 ring-1 ring-inset ring-line",
    dot: "bg-slate-500", text: "text-slate-400",
  },
};

/** Crash-proof lookup: unknown hardness degrades to a neutral chip, never throws. */
export function hardnessMeta(h: string | null | undefined) {
  return (
    HARDNESS_META[(h ?? "") as Hardness] ?? {
      en: h ?? "?", cn: h ?? "?",
      chip: "bg-surface-2 text-slate-400 ring-1 ring-inset ring-line",
      dot: "bg-slate-500", text: "text-slate-400",
    }
  );
}

export function HardnessBadge({ hardness, withEn = true, className }: {
  hardness: string | null | undefined;
  withEn?: boolean;
  className?: string;
}) {
  const m = hardnessMeta(hardness);
  return (
    <Badge className={cn(m.chip, className)} title={`硬度 · ${m.cn} / ${m.en}`}>
      <span className={cn("h-1.5 w-1.5 rounded-full", m.dot)} aria-hidden="true" />
      {m.cn}
      {withEn && <span className="opacity-60">{m.en}</span>}
    </Badge>
  );
}

// --- overclaim verdict lamps --------------------------------------------------
export const VERDICT_META: Record<
  ClaimStatus,
  { en: string; cn: string; lamp: string; chip: string; text: string }
> = {
  fixation_triggered: {
    en: "Fixated", cn: "固化", lamp: "bg-neg shadow-[0_0_8px_rgb(var(--c-neg)/0.6)]",
    chip: "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/25", text: "text-neg-700",
  },
  falsified: {
    en: "Falsified", cn: "证伪", lamp: "bg-pos shadow-[0_0_8px_rgb(var(--c-pos)/0.6)]",
    chip: "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/25", text: "text-pos-700",
  },
  expired: {
    en: "Expired", cn: "过期", lamp: "bg-warn shadow-[0_0_8px_rgb(var(--c-warn)/0.6)]",
    chip: "bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/25", text: "text-warn-700",
  },
  inconclusive: {
    en: "Inconclusive", cn: "待识别", lamp: "bg-slate-500",
    chip: "bg-surface-2 text-slate-400 ring-1 ring-inset ring-line", text: "text-slate-400",
  },
  open: {
    en: "Open", cn: "未决", lamp: "bg-andy shadow-[0_0_8px_rgb(var(--c-andy)/0.55)]",
    chip: "bg-andy-50 text-andy-500 ring-1 ring-inset ring-andy/25", text: "text-andy-500",
  },
};

/** Crash-proof verdict lookup. */
export function verdictMeta(s: string | null | undefined) {
  return (
    VERDICT_META[(s ?? "") as ClaimStatus] ?? {
      en: s ?? "?", cn: s ?? "?", lamp: "bg-slate-500",
      chip: "bg-surface-2 text-slate-400 ring-1 ring-inset ring-line", text: "text-slate-400",
    }
  );
}

export function VerdictLamp({ status, size = 10, withLabel = true, className }: {
  status: string | null | undefined;
  size?: number;
  withLabel?: boolean;
  className?: string;
}) {
  const m = verdictMeta(status);
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)} title={`${m.cn} · ${m.en}`}>
      <span
        className={cn("inline-block shrink-0 rounded-full", m.lamp)}
        style={{ width: size, height: size }}
        aria-hidden="true"
      />
      {withLabel && (
        <span className={cn("text-xs font-semibold", m.text)}>
          {m.cn} <span className="font-normal opacity-60">{m.en}</span>
        </span>
      )}
    </span>
  );
}

// --- source grades -------------------------------------------------------------
export const SOURCE_GRADE_LABEL: Record<string, string> = {
  A_official: "官方一级",
  B_public_curated: "公开可信",
  C_vendor_estimate: "厂商估算",
  D_derived: "派生",
  E_press: "新闻",
};

export function sourceGradeLabel(g: string | null | undefined): string {
  if (!g) return "—";
  return SOURCE_GRADE_LABEL[g] ?? g;
}

// --- forced watermark for SOFT metrics/claims -----------------------------------
/** 「未识别 · 勿作因果」banner — MUST accompany any SOFT value/chart. `watermark`
 * is the API's verbatim identification.watermark text (shown as subline + tooltip). */
export function SoftWatermark({ watermark, compact, className }: {
  watermark?: string | null;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div
      title={watermark ?? undefined}
      className={cn(
        "flex items-start gap-2 rounded-lg border border-dashed border-warn/50 bg-warn-50 text-warn-700",
        compact ? "px-2 py-1" : "px-3 py-2",
        className,
      )}
    >
      <ShieldAlert size={compact ? 12 : 14} strokeWidth={2.25} className="mt-0.5 shrink-0" />
      <div className="min-w-0">
        <div className={cn("font-semibold", compact ? "text-2xs" : "text-xs")}>
          未识别 · 勿作因果 <span className="font-normal opacity-70">Unidentified — not causal</span>
        </div>
        {!compact && watermark && (
          <div className="mt-0.5 text-2xs leading-snug opacity-80">{watermark}</div>
        )}
      </div>
    </div>
  );
}

// --- theory anchor chip ----------------------------------------------------------
export function AnchorChip({ anchor, title, active, onClick }: {
  anchor: string;
  title?: string;
  active?: boolean;
  onClick?: () => void;
}) {
  const label = anchor.startsWith("META_") ? `META·${anchor.slice(5)}` : anchor;
  const cls = cn(
    "inline-flex items-center rounded-md px-1.5 py-0.5 font-mono text-2xs font-medium ring-1 ring-inset transition-colors",
    active
      ? "bg-andy-100 text-andy-500 ring-andy/40"
      : "bg-surface-2 text-slate-400 ring-line",
    onClick && "cursor-pointer hover:bg-andy-50 hover:text-andy-500",
  );
  if (onClick) {
    return (
      <button type="button" onClick={onClick} title={title ?? anchor} className={cls}>
        {label}
      </button>
    );
  }
  return <span title={title ?? anchor} className={cls}>{label}</span>;
}

// --- number / slope / countdown helpers -------------------------------------------
/** Compact metric value: null → "—"; scales decimals to magnitude, tnum-friendly. */
export function fmtMetric(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (a >= 100) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  if (a === 0) return "0";
  return v.toPrecision(3);
}

/** Slope direction + tone. Alignment with good_when decides pos/neg; without a
 * stance the arrow stays neutral (a slope is a fact, not a verdict). */
export function slopeInfo(
  slope: number | null | undefined,
  goodWhen: "rising" | "falling" | null | undefined = null,
): { arrow: string; cls: string; label: string } {
  if (slope === null || slope === undefined || Number.isNaN(slope))
    return { arrow: "—", cls: "text-slate-500", label: "斜率 —" };
  const rising = slope > 0;
  const flat = slope === 0;
  const arrow = flat ? "▶" : rising ? "▲" : "▼";
  let cls = "text-slate-400";
  if (!flat && goodWhen) {
    const aligned = (rising && goodWhen === "rising") || (!rising && goodWhen === "falling");
    cls = aligned ? "text-pos-700" : "text-neg-700";
  }
  return { arrow, cls, label: `斜率 ${fmtMetric(slope)}` };
}

/** Parse a decision window like "24m" / "90d" / "6w" / "2y" into an expiry date. */
export function windowExpiry(windowStart: string, decisionWindow: string | null | undefined): Date | null {
  if (!windowStart) return null;
  const start = new Date(`${windowStart}T00:00:00`);
  if (Number.isNaN(start.getTime())) return null;
  const m = /^(\d+)\s*([dwmy])/i.exec(decisionWindow ?? "");
  if (!m) return null;
  const n = Number(m[1]);
  const unit = m[2].toLowerCase();
  const d = new Date(start);
  if (unit === "d") d.setDate(d.getDate() + n);
  else if (unit === "w") d.setDate(d.getDate() + n * 7);
  else if (unit === "m") d.setMonth(d.getMonth() + n);
  else d.setFullYear(d.getFullYear() + n);
  return d;
}

/** Days remaining in the decision window as seen from `asOf` (negative = expired). */
export function windowDaysLeft(
  windowStart: string,
  decisionWindow: string | null | undefined,
  asOf: string,
): number | null {
  const expiry = windowExpiry(windowStart, decisionWindow);
  if (!expiry) return null;
  const ref = new Date(`${asOf}T00:00:00`);
  if (Number.isNaN(ref.getTime())) return null;
  return Math.round((expiry.getTime() - ref.getTime()) / 86400000);
}

/** "判定窗 ⏳ N天" | "⌛ 已过期 N天" countdown element. */
export function WindowCountdown({ windowStart, decisionWindow, asOf, className }: {
  windowStart: string;
  decisionWindow: string | null | undefined;
  asOf: string;
  className?: string;
}) {
  const days = windowDaysLeft(windowStart, decisionWindow, asOf);
  if (days === null) {
    return <span className={cn("tnum text-2xs text-slate-500", className)}>判定窗 —</span>;
  }
  if (days < 0) {
    return (
      <span className={cn("tnum text-2xs font-medium text-warn-700", className)}>
        ⌛ 已过期 {Math.abs(days)}天
      </span>
    );
  }
  return (
    <span
      className={cn(
        "tnum text-2xs font-medium",
        days <= 30 ? "text-warn-700" : "text-slate-400",
        className,
      )}
      title={`窗口 ${decisionWindow ?? "—"} · 起点 ${windowStart}`}
    >
      判定窗 ⏳ {days}天
    </span>
  );
}

// --- metric-key chip (mono, linked) ----------------------------------------------
export function MetricKeyChip({ metricKey, to, children }: {
  metricKey: string;
  to: string;
  children?: ReactNode;
}) {
  return (
    <Link
      to={to}
      className="inline-flex items-center gap-1 rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-2xs text-brand-700 ring-1 ring-inset ring-line transition-colors hover:bg-andy-50 hover:text-andy-500 hover:ring-andy/30"
      title={metricKey}
    >
      {children ?? metricKey}
    </Link>
  );
}
