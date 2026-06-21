import { type ReactNode } from "react";
import {
  Workflow,
  Telescope,
  Network,
  Users,
  Swords,
  ShieldAlert,
  PenTool,
  ShieldCheck,
  UserCheck,
  Layers3,
  Calculator,
  ScanSearch,
  Share2,
  Sparkles,
  CircleDot,
} from "lucide-react";
import { ops } from "../../lib/ops";
import { cn } from "../../lib/format";
import type { Capability, Skill, SkillsInfo } from "../../types-ops";
import { Badge, Card, SectionHeader } from "../../components/ui";
import {
  OpsContainer,
  OpsError,
  OpsHeader,
  OpsLoading,
  useAsync,
} from "./_shared";

/** Stage metadata: label + icon for each of the 8 pipeline stages (in order). */
const STAGE_META: Record<number, { label: string; icon: ReactNode }> = {
  1: { label: "Scope", icon: <Telescope size={13} /> },
  2: { label: "Retrieve", icon: <Network size={13} /> },
  3: { label: "Analysts", icon: <Users size={13} /> },
  4: { label: "Debate", icon: <Swords size={13} /> },
  5: { label: "Risk", icon: <ShieldAlert size={13} /> },
  6: { label: "Editor", icon: <PenTool size={13} /> },
  7: { label: "Evidence Gate", icon: <ShieldCheck size={13} /> },
  8: { label: "Approval", icon: <UserCheck size={13} /> },
};

/** Tier → Badge tone. strong=accent, fast=slate, "-"=muted. */
function tierBadge(tier?: string): ReactNode {
  const t = tier ?? "-";
  if (t === "strong")
    return (
      <Badge className="bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20">strong</Badge>
    );
  if (t === "fast")
    return (
      <Badge className="bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200">fast</Badge>
    );
  return <Badge className="bg-slate-50 text-slate-400 ring-1 ring-inset ring-slate-200">—</Badge>;
}

/** Capability id → icon. */
const CAP_ICON: Record<string, ReactNode> = {
  hybrid_retrieval: <ScanSearch size={15} />,
  entity_resolution: <Network size={15} />,
  numeric_tieout: <Calculator size={15} />,
  kg_extract: <Layers3 size={15} />,
  signals_bridge: <Share2 size={15} />,
  embeddings: <Sparkles size={15} />,
};

export function SkillsPage() {
  const { data, loading, error } = useAsync<SkillsInfo>(() => ops.skills(), []);

  return (
    <OpsContainer>
      <OpsHeader
        title="Agent Skills"
        titleCn="智能体技能图"
        icon={<Workflow size={18} />}
        subtitle={
          data
            ? `${data.summary.stages} stages · ${data.summary.skills} skills · ${data.summary.capabilities} platform capabilities`
            : "Report DAG · controllable agent pipeline"
        }
      />

      {loading && <OpsLoading />}
      {error && <OpsError error={error} />}

      {data && (
        <div className="space-y-5">
          <PipelineSection skills={data.pipeline} />
          <CapabilitiesSection capabilities={data.capabilities} />
        </div>
      )}
    </OpsContainer>
  );
}

/* ===========================================================================
 * Report Pipeline — ordered, stage-numbered vertical DAG.
 * Skills are grouped by stage; each stage shows its number, label, and the
 * skill cards. A connector spine + arrows convey the sequence
 * scope → graph_retrieve → analysts → debate → risk → editor →
 * evidence_gate → approval.
 * ========================================================================= */
function PipelineSection({ skills }: { skills: Skill[] }) {
  // Group skills by stage, preserving the stage order (1..8).
  const stages = new Map<number, Skill[]>();
  for (const s of skills) {
    const st = s.stage ?? 0;
    if (!stages.has(st)) stages.set(st, []);
    stages.get(st)!.push(s);
  }
  const ordered = [...stages.entries()].sort((a, b) => a[0] - b[0]);

  return (
    <Card>
      <SectionHeader
        title="Report Pipeline"
        titleCn="可控报告 DAG"
        icon={<Workflow size={15} />}
        right={
          <span className="text-2xs text-slate-400">scope → retrieve → analysts → … → approval</span>
        }
      />
      <div className="px-4 py-4">
        <ol className="relative">
          {/* vertical spine */}
          <span
            aria-hidden
            className="absolute left-[15px] top-3 bottom-3 w-px bg-line"
          />
          {ordered.map(([stage, items], si) => (
            <li key={stage} className="relative mb-5 last:mb-0 pl-11">
              {/* stage number node on the spine */}
              <span className="absolute left-0 top-0 flex h-8 w-8 items-center justify-center rounded-full border border-line bg-surface text-xs font-semibold tnum text-brand-900 shadow-card">
                {stage}
              </span>

              {/* stage label row */}
              <div className="mb-2 flex items-center gap-2 pt-1">
                <span className="text-slate-400">{STAGE_META[stage]?.icon}</span>
                <span className="text-2xs font-semibold uppercase tracking-wide text-slate-500">
                  {STAGE_META[stage]?.label ?? `Stage ${stage}`}
                </span>
                {items.length > 1 && (
                  <span className="text-2xs text-slate-400">· {items.length} parallel skills</span>
                )}
                {si < ordered.length - 1 && (
                  <span className="ml-auto text-2xs text-slate-300">↓</span>
                )}
              </div>

              {/* skill cards for this stage */}
              <div
                className={cn(
                  "grid gap-2",
                  items.length > 1 ? "sm:grid-cols-2" : "grid-cols-1",
                )}
              >
                {items.map((sk) => (
                  <SkillCard key={sk.id} skill={sk} />
                ))}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </Card>
  );
}

function SkillCard({ skill }: { skill: Skill }) {
  const terms = (skill.query ?? "").split(/\s+/).filter(Boolean);
  return (
    <div className="rounded-lg border border-line bg-canvas/40 px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <CircleDot size={13} className="shrink-0 text-slate-300" />
          <span className="truncate text-xs font-semibold text-brand-900">{skill.name}</span>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {skill.numeric === true && (
            <Badge
              className="bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/20"
              title="Numeric tie-out required for this skill's outputs"
            >
              <Calculator size={10} /> numeric
            </Badge>
          )}
          {tierBadge(skill.tier)}
        </div>
      </div>
      <p className="mt-1.5 text-2xs leading-relaxed text-slate-500">{skill.desc}</p>
      {terms.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {terms.map((t, i) => (
            <span
              key={i}
              className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[10px] leading-none text-slate-500"
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ===========================================================================
 * Platform Capabilities — grid of capability cards.
 * ========================================================================= */
function CapabilitiesSection({ capabilities }: { capabilities: Capability[] }) {
  return (
    <Card>
      <SectionHeader
        title="Platform Capabilities"
        titleCn="平台能力"
        icon={<Layers3 size={15} />}
        right={<span className="text-2xs text-slate-400">{capabilities.length} primitives</span>}
      />
      <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
        {capabilities.map((c) => (
          <div
            key={c.id}
            className="rounded-lg border border-line bg-canvas/40 p-3.5 transition-colors hover:border-accent/40"
          >
            <div className="flex items-center gap-2">
              <span className="flex h-7 w-7 items-center justify-center rounded-md border border-line bg-surface text-accent">
                {CAP_ICON[c.id] ?? <Layers3 size={15} />}
              </span>
              <span className="min-w-0 truncate text-xs font-semibold text-brand-900">
                {c.name}
              </span>
            </div>
            <p className="mt-2 text-2xs leading-relaxed text-slate-500">{c.desc}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}
