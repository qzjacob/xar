import { Link, useLocation } from "react-router-dom";
import { cn } from "../../lib/format";
import { ADMIN_MODULES, RESEARCH_MODULES, type ModuleDef } from "../../lib/modules";
import { BrandLogo } from "./BrandLogo";

/** 顶栏页签 — 模块级组件(不定义在渲染体内,避免每次路由变化重挂 DOM)。 */
function ModuleTab({ m, pathname }: { m: ModuleDef; pathname: string }) {
  const active = m.match(pathname);
  const Icon = m.icon;
  return (
    <Link
      to={m.route}
      title={`${m.label} · ${m.cn}`}
      className={cn(
        "flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold transition-colors",
        active
          ? "bg-accent-50 text-accent-100 ring-1 ring-inset ring-accent/25"
          : m.admin
            ? "text-brand-200/70 hover:bg-surface-2 hover:text-brand-900"
            : "text-brand-500 hover:bg-surface-2 hover:text-brand-900",
      )}
    >
      <Icon size={15} className={cn(active && "text-accent-500")} />
      <span>{m.label}</span>
      <span
        className={cn(
          "hidden text-[10px] font-normal xl:inline",
          active ? "text-accent-100/70" : "text-brand-200/60",
        )}
      >
        {m.cn}
      </span>
    </Link>
  );
}

/**
 * 全局常驻顶栏 — 在 App 顶层 layout route 渲染一次,切模块不重挂。
 * 左:恒定 BrandLogo;右:研究模块页签(高亮当前,可横向滚动)+ 分隔竖线 + Jarvy(弱化)。
 * 设计约束:此组件不得接入任何数据 context(useData 等)。
 */
export function GlobalTopBar() {
  const { pathname } = useLocation();
  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-surface px-4">
      <BrandLogo className="shrink-0" />
      <nav className="scroll-thin ml-auto flex min-w-0 items-center gap-1 overflow-x-auto">
        {RESEARCH_MODULES.map((m) => (
          <ModuleTab key={m.key} m={m} pathname={pathname} />
        ))}
      </nav>
      <div className="flex shrink-0 items-center gap-3">
        <div className="h-5 w-px bg-line" aria-hidden />
        {ADMIN_MODULES.map((m) => (
          <ModuleTab key={m.key} m={m} pathname={pathname} />
        ))}
      </div>
    </header>
  );
}
