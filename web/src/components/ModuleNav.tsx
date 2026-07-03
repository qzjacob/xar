import { Activity, CandlestickChart, LayoutDashboard, Sparkles, Telescope, SlidersHorizontal } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { cn } from "../lib/format";

type Mod = {
  key: string;
  label: string;
  cn: string;
  to: string;
  icon: typeof Sparkles;
  match: (p: string) => boolean;
};

/** The four primary modules + two satellite consoles. Order = primary first. */
export const MODULES: Mod[] = [
  { key: "chathy", label: "Chathy", cn: "对话分析", to: "/", icon: Sparkles,
    match: (p) => p === "/" || p.startsWith("/chathy") },
  { key: "andy", label: "Andy", cn: "宏观指标", to: "/andy", icon: Activity,
    match: (p) => p.startsWith("/andy") },
  { key: "genny", label: "Genny", cn: "研究终端", to: "/genny", icon: LayoutDashboard,
    match: (p) => p.startsWith("/genny") || p.startsWith("/segment") || p.startsWith("/company") },
  { key: "fenny", label: "Fenny", cn: "结构化票据", to: "/fenny", icon: CandlestickChart,
    match: (p) => p.startsWith("/fenny") },
];

const SATELLITES: Mod[] = [
  { key: "explore", label: "Explore", cn: "前沿探索", to: "/explore", icon: Telescope,
    match: (p) => p.startsWith("/explore") },
  { key: "ops", label: "Ops", cn: "运营控制台", to: "/ops", icon: SlidersHorizontal,
    match: (p) => p.startsWith("/ops") },
];

/** Shared module switcher rendered in every module's chrome. `variant` tunes density. */
export function ModuleNav({ variant = "rail" }: { variant?: "rail" | "bar" }) {
  const { pathname } = useLocation();
  const bar = variant === "bar";

  const Item = ({ m, satellite }: { m: Mod; satellite?: boolean }) => {
    const active = m.match(pathname);
    const Icon = m.icon;
    return (
      <Link
        to={m.to}
        title={`${m.label} · ${m.cn}`}
        className={cn(
          "group flex items-center gap-2 rounded-lg transition-colors",
          bar ? "px-2.5 py-1.5" : "px-2.5 py-2",
          active
            ? "bg-accent-50 text-accent-100 ring-1 ring-inset ring-accent/25"
            : "text-slate-400 hover:bg-surface-2 hover:text-brand-900",
        )}
      >
        <Icon size={bar ? 15 : 16} className={cn(active ? "text-accent-500" : "", satellite && !active ? "opacity-70" : "")} />
        {!bar ? (
          <span className="flex min-w-0 flex-col leading-tight">
            <span className="text-xs font-semibold">{m.label}</span>
            {!satellite && <span className="text-[10px] text-slate-500">{m.cn}</span>}
          </span>
        ) : (
          <span className="text-xs font-semibold">{m.label}</span>
        )}
      </Link>
    );
  };

  return (
    <nav className={cn(bar ? "flex items-center gap-1" : "flex flex-col gap-1")}>
      {MODULES.map((m) => <Item key={m.key} m={m} />)}
      <div className={cn(bar ? "mx-1 h-4 w-px bg-line" : "my-1 h-px w-full bg-line")} />
      {SATELLITES.map((m) => <Item key={m.key} m={m} satellite />)}
    </nav>
  );
}
