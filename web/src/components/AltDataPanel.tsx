import { Radio } from "lucide-react";
import { cn, fmtDate, fmtPct } from "../lib/format";
import { pillarKindLabel } from "../types-thesis";
import {
  momentumArrow,
  signalShort,
  signalTone,
  signalToneText,
  type AltData,
  type AltSignal,
} from "../types-alt";
import { Badge, Card, SectionHeader } from "./ui";

/**
 * Alt-Data 360 — dense high-frequency signal panel on the company page. Each
 * signal renders as one row: direction arrow · name_cn + short/scope tags · z
 * headline (colored by contribution) · momentum % · period. A pillar-kind
 * roll-up strip sits on top. Sits right below the thesis so the signals read as
 * a live correction to it. Renders nothing when `alt` is null / empty (~99% of
 * names have no bindings).
 */
export function AltDataPanel({ alt }: { alt: AltData | null | undefined }) {
  if (!alt || (alt.signals?.length ?? 0) === 0) return null;

  // strongest absolute contribution first
  const signals = [...alt.signals].sort(
    (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution),
  );
  const pillars = Object.entries(alt.pillar_scores ?? {}).sort(
    (a, b) => Math.abs(b[1].score) - Math.abs(a[1].score),
  );

  return (
    <Card>
      <SectionHeader
        title="Alternative Data"
        titleCn="另类数据 · 高频信号"
        icon={<Radio size={15} strokeWidth={2} />}
        right={
          <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
            {signals.length}
          </Badge>
        }
      />

      {/* pillar-kind roll-up strip */}
      {pillars.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-b border-line px-3 py-2.5">
          {pillars.map(([kind, ps]) => {
            const label = pillarKindLabel(kind);
            const tone = signalToneText(
              ps.score > 0.05 ? "pos" : ps.score < -0.05 ? "neg" : "neutral",
            );
            return (
              <span
                key={kind}
                title={`${label.cn} ${label.en} · 信号分 ${ps.score.toFixed(2)} · ${ps.signals.length} 信号`}
                className="inline-flex items-center gap-1.5 rounded-md border border-line bg-canvas px-2 py-1"
              >
                <span className="text-2xs font-medium text-brand-500">{label.cn}</span>
                <span className={cn("tnum text-2xs font-semibold", tone)}>
                  {ps.score > 0 ? "+" : ""}
                  {ps.score.toFixed(2)}
                </span>
              </span>
            );
          })}
        </div>
      )}

      {/* dense signal rows */}
      <div className="divide-y divide-line/70">
        {signals.map((s, i) => (
          <SignalRow key={s.signal_key + i} s={s} />
        ))}
      </div>

      <div className="border-t border-line px-3 py-2 text-2xs leading-relaxed text-brand-200">
        z = 标准分(-3..3),颜色随贡献方向(绿多 / 红空);链级 = 主题级信号;
        <span className="text-brand-500">注意力</span> 为无方向关注度信号。
      </div>
    </Card>
  );
}

/** One high-frequency signal row. */
function SignalRow({ s }: { s: AltSignal }) {
  const tone = signalTone(s);
  const text = signalToneText(tone);
  const short = signalShort(s.signal_key);
  const z = s.z ?? 0;
  const attention = s.good_when == null;
  return (
    <div className="flex items-center gap-2.5 px-3 py-2">
      {/* direction */}
      <span
        className={cn("tnum w-3 shrink-0 text-center text-xs leading-none", text)}
        aria-hidden="true"
      >
        {momentumArrow(s.momentum)}
      </span>

      {/* name + tags */}
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-xs font-semibold text-brand-900">{s.name_cn}</span>
        <div className="flex flex-wrap items-center gap-1">
          <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
            {short.short}
          </Badge>
          {s.scope === "theme" && (
            <Badge
              className="bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20"
              title={`链级信号 theme-scope${s.theme ? ` · ${s.theme}` : ""}`}
            >
              链级
            </Badge>
          )}
          {attention && (
            <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
              注意力
            </Badge>
          )}
          {typeof s.n === "number" && s.n > 0 && (
            <span className="tnum text-2xs text-brand-200">n{s.n}</span>
          )}
        </div>
      </div>

      {/* momentum % */}
      <span
        className={cn("tnum w-16 shrink-0 text-right text-2xs", attention ? "text-brand-500" : text)}
        title="动量 Momentum"
      >
        {fmtPct(s.momentum * 100)}
      </span>

      {/* z headline */}
      <div className="flex w-11 shrink-0 flex-col items-end leading-none">
        <span
          className={cn("tnum text-sm font-semibold", text)}
          title={`z-score ${z.toFixed(2)} · 贡献 ${s.contribution.toFixed(2)}`}
        >
          {z > 0 ? "+" : ""}
          {z.toFixed(1)}
        </span>
        <span className="mt-0.5 text-2xs text-brand-200">z</span>
      </div>

      {/* period */}
      <span className="tnum w-11 shrink-0 text-right text-2xs text-brand-500">
        {s.period_end ? fmtDate(s.period_end) : "—"}
      </span>
    </div>
  );
}
