import { useEffect, useState } from "react";
import {
  Activity,
  BrainCircuit,
  Cpu,
  Database,
  Gauge,
  Layers3,
  Network,
  Plug,
  Radar,
  Satellite,
  Workflow,
} from "lucide-react";
import { ops } from "../lib/ops";
import { SidebarFrame } from "./shell/SidebarFrame";
import { SidebarNav, type SideNavItem } from "./shell/SidebarNav";

/**
 * 未解决 critical 告警的红点计数(2026-07-29)。放在左栏而非只在监控页内,是因为
 * 停摆最需要被看见的时刻恰恰是「人没在看监控页」的时刻 —— 手机推送管不在电脑前的情况,
 * 这个红点管人在 Jarvy 里干别的事的情况。
 */
function MonitorBadge() {
  const [n, setN] = useState(0);
  useEffect(() => {
    let on = true;
    const tick = () => {
      if (document.visibilityState !== "visible") return;
      ops.monitorSummary()
        .then((s) => { if (on) setN(s.openCritical || 0); })
        .catch(() => { /* 侧栏徽章静默失败:它不该因接口抖动而报错干扰导航 */ });
    };
    tick();
    const timer = window.setInterval(tick, 60_000);
    return () => { on = false; window.clearInterval(timer); };
  }, []);
  if (!n) return null;
  return (
    <span className="inline-flex min-w-4 items-center justify-center rounded-full bg-neg px-1
                     text-2xs font-semibold leading-4 text-white">
      {n}
    </span>
  );
}

const ADMIN_NAV: SideNavItem[] = [
  { to: "/jarvy", label: "Overview", cn: "总览", icon: Gauge, exact: true },
  { to: "/jarvy/monitor", label: "Monitor", cn: "任务监控", icon: Activity,
    badge: <MonitorBadge /> },
  { to: "/jarvy/fetchy", label: "Fetchy", cn: "抓取工人", icon: Satellite },
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
