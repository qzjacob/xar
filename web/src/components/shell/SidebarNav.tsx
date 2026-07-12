import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { cn } from "../../lib/format";

export interface SideNavItem {
  to: string;
  label: string;
  cn?: string;
  icon: LucideIcon;
  /** true = 仅精确路径高亮(index 项);默认前缀匹配 */
  exact?: boolean;
  badge?: ReactNode;
}

/**
 * 统一左栏导航 — 所有模块同一行高/字号/active 规则(border-l-2 accent)。
 * `transform` 用于装饰目标链接(如 Andy 给每个 to 追加 ?as_of=…)。
 */
export function SidebarNav({
  items,
  heading,
  transform,
}: {
  items: SideNavItem[];
  heading?: string;
  transform?: (to: string) => string;
}) {
  const { pathname } = useLocation();
  return (
    <nav className="flex flex-col gap-0.5">
      {heading && (
        <div className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-brand-200">
          {heading}
        </div>
      )}
      {items.map((it) => {
        const active = it.exact ? pathname === it.to : pathname.startsWith(it.to);
        const Icon = it.icon;
        return (
          <Link
            key={it.to + it.label}
            to={transform ? transform(it.to) : it.to}
            className={cn(
              "flex w-full items-center gap-2.5 rounded-md border-l-2 py-1.5 pl-2 pr-2 text-xs font-medium transition-colors",
              active
                ? "border-accent bg-accent/15 text-white"
                : "border-transparent text-brand-500 hover:bg-surface-2 hover:text-brand-900",
            )}
          >
            <Icon size={15} className={cn(active ? "text-accent-100" : "text-brand-200")} />
            <span className="min-w-0 flex-1 truncate">
              {it.label}
              {it.cn && <span className={cn("ml-1.5 text-[10px]", active ? "text-white/60" : "text-brand-200/70")}>{it.cn}</span>}
            </span>
            {it.badge}
          </Link>
        );
      })}
    </nav>
  );
}
