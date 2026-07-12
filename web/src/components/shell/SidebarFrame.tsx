import type { ReactNode } from "react";

/**
 * 全项目唯一 w-60 左侧竖栏框架 — 所有模块共用同一宽度/内边距/字号/分区样式。
 * 头部是统一的"模块名"行(替代旧的 per-module 品牌块:品牌只在全局顶栏出现一次)。
 */
export function SidebarFrame({
  title,
  titleCn,
  badge,
  children,
  footer,
}: {
  title: string;
  titleCn?: string;
  badge?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-surface text-brand-100">
      <div className="flex items-baseline gap-2 px-4 pb-3 pt-4">
        <span className="text-sm font-bold tracking-tight text-brand-900">{title}</span>
        {titleCn && <span className="text-2xs text-brand-500">{titleCn}</span>}
        {badge && (
          <span className="ml-auto rounded border border-line bg-surface-2 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-brand-200">
            {badge}
          </span>
        )}
      </div>
      <div className="scroll-thin flex-1 overflow-y-auto px-2 pb-3">{children}</div>
      {footer && <div className="mt-auto border-t border-line px-4 py-3">{footer}</div>}
    </aside>
  );
}
