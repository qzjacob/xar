import { Link, useLocation } from "react-router-dom";
import { cn } from "../../lib/format";
import { ADMIN_MODULES, RESEARCH_MODULES, type ModuleDef } from "../../lib/modules";
import { BrandLogo } from "./BrandLogo";

/**
 * 全局常驻顶栏 — 在 App 顶层 layout route 渲染一次,切模块不重挂。
 * 左:恒定 BrandLogo;中:研究模块页签(高亮当前);右:分隔竖线 + Jarvy(后端管理,弱化)。
 * 设计约束:此组件不得接入任何数据 context(useData 等)。
 */
export function GlobalTopBar() {
  const { pathname } = useLocation();

  const Tab = ({ m }: { m: ModuleDef }) => {
    const active = m.match(pathname);
    const Icon = m.icon;
    return (
      <Link
        to={m.route}
        title={`${m.label} · ${m.cn}`}
        className={cn(
          "flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold transition-colors",
          active
            ? "bg-accent-50 text-accent-100 ring-1 ring-inset ring-accent/25"
            : m.admin
              ? "text-brand-200/70 hover:bg-surface-2 hover:text-brand-900"
              : "text-brand-500 hover:bg-surface-2 hover:text-brand-900",
        )}
      >
        <Icon size={15} className={cn(active && "text-accent-500")} />
        <span>{m.label}</span>
        <span className={cn("text-[10px] font-normal", active ? "text-accent-100/70" : "text-brand-200/60")}>
          {m.cn}
        </span>
      </Link>
    );
  };

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-surface px-4">
      <BrandLogo />
      <nav className="ml-2 flex items-center gap-1">
        {RESEARCH_MODULES.map((m) => (
          <Tab key={m.key} m={m} />
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-3">
        <div className="h-5 w-px bg-line" aria-hidden />
        {ADMIN_MODULES.map((m) => (
          <Tab key={m.key} m={m} />
        ))}
      </div>
    </header>
  );
}
