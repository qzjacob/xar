import {
  Activity,
  Atom,
  Brain,
  Compass,
  Cpu,
  Globe,
  Sigma,
  type LucideIcon,
} from "lucide-react";
import { SidebarFrame } from "./shell/SidebarFrame";
import { SidebarNav, type SideNavItem } from "./shell/SidebarNav";
import type { ExploreSectionCard } from "../types-exploration";

const ICONS: Record<string, LucideIcon> = {
  brain: Brain,
  atom: Atom,
  sigma: Sigma,
  cpu: Cpu,
  activity: Activity,
  globe: Globe,
};

/** Romy 左栏 — 统一 SidebarFrame/SidebarNav;分区导航由 API sections 数据驱动。 */
export function ExplorationSidebar({ sections }: { sections: ExploreSectionCard[] }) {
  const items: SideNavItem[] = [
    { to: "/romy", label: "Overview", cn: "总览", icon: Compass, exact: true },
    ...sections.map((s) => ({
      to: `/romy/${s.id}`,
      label: s.name,
      icon: ICONS[s.icon ?? ""] ?? Compass,
      badge:
        s.frontCount > 0 ? (
          <span className="tnum rounded bg-surface-2 px-1 py-0.5 text-2xs text-brand-200">
            {s.frontCount}
          </span>
        ) : undefined,
    })),
  ];
  return (
    <SidebarFrame title="Romy" titleCn="前沿探索" badge="Frontier">
      <SidebarNav heading="Frontier Sections" items={items} />
    </SidebarFrame>
  );
}
