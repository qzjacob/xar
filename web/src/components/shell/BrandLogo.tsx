import { Link } from "react-router-dom";
import { cn } from "../../lib/format";

/**
 * 全项目唯一品牌区 — 极简科技风几何 X + XAR 字标 + "powered by AI from HKU.CDS"。
 * 刻意零 per-module prop:进入任何模块都不变(设计约束,勿加变体)。
 */
export function BrandLogo({ to = "/", className }: { to?: string; className?: string }) {
  return (
    <Link to={to} className={cn("group flex select-none items-center gap-2.5", className)} title="XAR">
      {/* X mark:两笔圆头斜杠,一笔带断口(电路/数据流意象),accent 单色 */}
      <svg
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        className="shrink-0 text-accent-500 transition-transform duration-200 group-hover:scale-105"
        aria-hidden
      >
        {/* 完整笔画 ↘ */}
        <path d="M5 5L19 19" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        {/* 断口笔画 ↗:中心留隙,右上段亮、左下段暗 → 极简科技感 */}
        <path d="M19 5L14.2 9.8" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        <path d="M9.8 14.2L5 19" stroke="currentColor" strokeWidth="3" strokeLinecap="round" opacity="0.45" />
      </svg>
      <span className="flex flex-col items-end leading-none">
        <span className="text-base font-bold tracking-tight text-brand-900">XAR</span>
        <span className="mt-0.5 whitespace-nowrap text-[9px] leading-none text-brand-200/60">
          powered by AI from HKU.CDS
        </span>
      </span>
    </Link>
  );
}
