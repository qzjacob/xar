import {
  BrainCircuit,
  Cpu,
  Database,
  Gauge,
  Layers3,
  Network,
  Plug,
  Radar,
  Workflow,
} from "lucide-react";
import { SidebarFrame } from "./shell/SidebarFrame";
import { SidebarNav, type SideNavItem } from "./shell/SidebarNav";

const ADMIN_NAV: SideNavItem[] = [
  { to: "/jarvy", label: "Overview", cn: "总览", icon: Gauge, exact: true },
  { to: "/jarvy/ontology", label: "Ontology", cn: "本体", icon: Network },
  { to: "/jarvy/coverage", label: "Coverage", cn: "覆盖度", icon: Radar },
  { to: "/jarvy/sources", label: "Data Sources", cn: "数据源", icon: Database },
  { to: "/jarvy/datalake", label: "Data Lake", cn: "数据湖", icon: Layers3 },
  { to: "/jarvy/altdata", label: "Alt-Data AI", cn: "另类数据", icon: BrainCircuit },
  { to: "/jarvy/models", label: "Models & LLM", cn: "模型", icon: Cpu },
  { to: "/jarvy/connectors", label: "MCP & API", cn: "连接器", icon: Plug },
  { to: "/jarvy/skills", label: "Agent Skills", cn: "技能", icon: Workflow },
];

/** Jarvy 左栏 — 统一 SidebarFrame/SidebarNav 体系(品牌只在全局顶栏,模块切换亦然)。 */
export function AdminSidebar() {
  return (
    <SidebarFrame title="Jarvy" titleCn="后台管理" badge="Admin">
      <SidebarNav heading="Control Plane" items={ADMIN_NAV} />
    </SidebarFrame>
  );
}
