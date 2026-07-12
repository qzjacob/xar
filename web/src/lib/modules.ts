import {
  Activity,
  CandlestickChart,
  LayoutDashboard,
  Sparkles,
  SlidersHorizontal,
  Telescope,
  type LucideIcon,
} from "lucide-react";

/** 模块注册表 — 全项目单一事实源(顶栏页签/外壳/改名都从这里取数)。 */
export type ModuleKey = "chathy" | "andy" | "genny" | "fenny" | "romy" | "jarvy";

export interface ModuleDef {
  key: ModuleKey;
  label: string;
  cn: string;
  route: string;
  icon: LucideIcon;
  /** true = 后端管理中心(Jarvy):顶栏右侧、分隔线之后、idle 态弱化显示 */
  admin?: boolean;
  match: (pathname: string) => boolean;
}

export const MODULES: ModuleDef[] = [
  { key: "chathy", label: "Chathy", cn: "对话分析", route: "/", icon: Sparkles,
    match: (p) => p === "/" || p.startsWith("/chathy") },
  { key: "andy", label: "Andy", cn: "宏观指标", route: "/andy", icon: Activity,
    match: (p) => p.startsWith("/andy") },
  { key: "genny", label: "Genny", cn: "研究终端", route: "/genny", icon: LayoutDashboard,
    match: (p) => p.startsWith("/genny") || p.startsWith("/segment") || p.startsWith("/company") },
  { key: "fenny", label: "Fenny", cn: "结构化票据", route: "/fenny", icon: CandlestickChart,
    match: (p) => p.startsWith("/fenny") },
  { key: "romy", label: "Romy", cn: "前沿探索", route: "/romy", icon: Telescope,
    match: (p) => p.startsWith("/romy") || p.startsWith("/explore") },
  { key: "jarvy", label: "Jarvy", cn: "后台管理", route: "/jarvy", icon: SlidersHorizontal, admin: true,
    match: (p) => p.startsWith("/jarvy") || p.startsWith("/ops") },
];

export const RESEARCH_MODULES = MODULES.filter((m) => !m.admin);
export const ADMIN_MODULES = MODULES.filter((m) => m.admin);
