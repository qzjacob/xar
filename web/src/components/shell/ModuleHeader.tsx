import type { ReactNode } from "react";

/**
 * 可选的模块上下文条(h-14) — 面包屑 + 模块级控件(健康芯片/日期/Refresh…)。
 * 全局模块切换在 GlobalTopBar,这里绝不再放模块页签。
 */
export function ModuleHeader({
  crumb,
  title,
  titleCn,
  children,
}: {
  crumb: string;
  title: string;
  titleCn?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-5">
      <div className="flex items-baseline gap-2 text-sm">
        <span className="text-brand-200">{crumb}</span>
        <span className="text-brand-200">/</span>
        <span className="font-semibold text-brand-900">{title}</span>
        {titleCn && <span className="text-xs text-brand-500">{titleCn}</span>}
      </div>
      <div className="ml-auto flex items-center gap-3">{children}</div>
    </div>
  );
}
