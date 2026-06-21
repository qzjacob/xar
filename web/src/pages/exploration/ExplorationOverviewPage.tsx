import { Activity, Atom, Brain, Compass, Cpu, Globe, Sigma, Telescope, type LucideIcon } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Card } from "../../components/ui";
import { exploration } from "../../lib/exploration";
import { cn } from "../../lib/format";
import { OpsError, OpsLoading } from "../ops/_shared";
import { maturityChip, MomentumBar, useAsync } from "./_shared";

const ICONS: Record<string, LucideIcon> = {
  brain: Brain, atom: Atom, sigma: Sigma, cpu: Cpu, activity: Activity, globe: Globe,
};

export function ExplorationOverviewPage() {
  const nav = useNavigate();
  const { data, loading, error } = useAsync(() => exploration.overview());

  if (loading) return <OpsLoading />;
  if (error) return <OpsError error={error} />;
  if (!data) return null;

  return (
    <div className="mx-auto max-w-[1200px]">
      {/* hero */}
      <div className="mb-6 flex items-start gap-3">
        <span className="mt-0.5 flex h-10 w-10 items-center justify-center rounded-xl bg-explore text-white shadow-card">
          <Telescope size={20} strokeWidth={2.1} />
        </span>
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <h1 className="text-xl font-semibold tracking-tight text-brand-900">Frontier of Knowledge</h1>
            <span className="text-sm text-slate-400">前沿探索</span>
          </div>
          <p className="mt-1 max-w-3xl text-sm leading-snug text-slate-500">
            The leading edge of human understanding — synthesized from arXiv preprints and expert
            voices into forward-looking research fronts. Long-horizon direction, not trades.
            <span className="text-slate-400"> 从 arXiv 与专家声音中综合人类认知前沿，以长期方向性为主。</span>
          </p>
        </div>
      </div>

      {/* section cards (AI first) */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {data.sections.map((s, i) => {
          const Icon = ICONS[s.icon ?? ""] ?? Compass;
          return (
            <Card
              key={s.id}
              className={cn(
                "cursor-pointer p-4 transition hover:shadow-pop",
                i === 0 && "ring-1 ring-explore/30",
              )}
              onClick={() => nav(`/explore/${s.id}`)}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-explore-50 text-explore-700">
                    <Icon size={18} strokeWidth={2} />
                  </span>
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-brand-900">{s.name}</div>
                    <div className="truncate text-2xs text-slate-400">{s.nameCn}</div>
                  </div>
                </div>
                {i === 0 && (
                  <span className="rounded bg-explore-50 px-1.5 py-0.5 text-2xs font-semibold uppercase text-explore-700 ring-1 ring-inset ring-explore/20">
                    Live
                  </span>
                )}
              </div>

              <p className="mt-3 line-clamp-2 text-xs leading-snug text-slate-500">{s.headline}</p>

              <div className="mt-3">
                <div className="mb-1 flex items-center justify-between text-2xs text-slate-400">
                  <span>momentum</span>
                  <span className="tnum font-semibold text-explore-700">{s.momentum}</span>
                </div>
                <MomentumBar value={s.momentum} />
              </div>

              <div className="mt-3 flex flex-wrap gap-1">
                {s.topFronts.slice(0, 3).map((f) => (
                  <span
                    key={f.title}
                    className={cn("truncate rounded px-1.5 py-0.5 text-2xs", maturityChip(f.maturity))}
                    title={f.title}
                  >
                    {f.title}
                  </span>
                ))}
                {s.topFronts.length === 0 && (
                  <span className="text-2xs text-slate-300">no fronts synthesized yet</span>
                )}
              </div>

              <div className="mt-3 flex items-center gap-3 border-t border-line pt-2 text-2xs text-slate-400">
                <span className="tnum">{s.frontCount} fronts</span>
                <span className="tnum">{s.paperCount} preprints</span>
                <span className="tnum">{s.articleCount} articles</span>
                <span className="tnum">{s.voiceCount} voices</span>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
