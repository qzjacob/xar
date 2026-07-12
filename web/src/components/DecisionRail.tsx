import { useState } from "react";
import {
  Check,
  Compass,
  Eye,
  Lightbulb,
  Plus,
  RefreshCw,
  Scissors,
  ShieldAlert,
  TrendingUp,
} from "lucide-react";
import type { ActionItem, Decision, Segment } from "../types";
import { cn, fmtScore, severityChip } from "../lib/format";
import { Badge } from "./ui";

/**
 * Fixed right rail — the "house" desk view distilled into one decision surface:
 * the standing house view, the highest-conviction opportunities (clickable into
 * their chain segment), the top risks, and a working action queue with local
 * check-off state. Hidden below xl; lives outside the scrolling main column.
 */
export function DecisionRail({
  decision,
  segments,
  onSelectSegment,
  inline = false,
}: {
  decision: Decision;
  segments: Segment[];
  onSelectSegment: (id: string | null) => void;
  /** When true, render as an in-flow card (used as the <xl fallback surface). */
  inline?: boolean;
}) {
  // local working state for the action checklist, seeded from the data.
  const [done, setDone] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(decision.actions.map((a) => [a.id, a.done])),
  );
  const toggle = (id: string) => setDone((d) => ({ ...d, [id]: !d[id] }));

  const segById = new Map(segments.map((s) => [s.id, s]));
  const doneCount = decision.actions.filter((a) => done[a.id]).length;

  return (
    <aside
      className={cn(
        "flex-col bg-surface",
        inline
          ? "flex w-full rounded-xl border border-line shadow-card"
          : "hidden w-80 shrink-0 flex-col border-l border-line xl:flex",
      )}
    >
      {/* header strip — matches TopBar height */}
      <div
        className={cn(
          "flex h-14 shrink-0 items-center justify-between gap-2 border-b border-line bg-surface px-4",
          inline ? "rounded-t-xl" : "sticky top-0 z-10",
        )}
      >
        <div className="flex min-w-0 items-center gap-2">
          <Compass size={15} strokeWidth={2} className="shrink-0 text-accent" />
          <h2 className="truncate text-sm font-semibold text-brand-900">Decision Rail</h2>
          <span className="truncate text-2xs text-brand-500">决策台</span>
        </div>
        <span className="shrink-0 rounded-md bg-brand-50 px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide text-brand-900">
          House
        </span>
      </div>

      {/* scrolling body */}
      <div
        className={cn(
          "scroll-thin space-y-5 overflow-y-auto px-4 py-4",
          inline ? "max-h-none" : "flex-1",
        )}
      >
        {/* 1) House View */}
        <section>
          <RailLabel icon={<Compass size={12} />}>House View · 观点</RailLabel>
          <div className="mt-2 border-l-2 border-accent pl-3">
            <p className="text-sm leading-relaxed text-brand-900">{decision.houseView}</p>
            <p className="mt-1.5 text-2xs leading-relaxed text-brand-200">{decision.houseViewCn}</p>
          </div>
        </section>

        <Divider />

        {/* 2) Top Opportunities */}
        <section>
          <RailLabel icon={<TrendingUp size={12} />} right={`${decision.opportunities.length}`}>
            Top Opportunities · 机会
          </RailLabel>
          <div className="mt-2 flex flex-col gap-2">
            {decision.opportunities.length === 0 ? (
              <EmptyRow>No opportunities flagged</EmptyRow>
            ) : (
              decision.opportunities.map((opp) => {
                const seg = opp.segmentId ? segById.get(opp.segmentId) : undefined;
                const clickable = Boolean(opp.segmentId);
                return (
                  <button
                    key={opp.id}
                    type="button"
                    disabled={!clickable}
                    onClick={() => opp.segmentId && onSelectSegment(opp.segmentId)}
                    className={cn(
                      "group w-full rounded-lg border border-line bg-canvas p-2.5 text-left transition",
                      clickable
                        ? "cursor-pointer hover:bg-surface hover:ring-1 hover:ring-accent/30"
                        : "cursor-default",
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="text-sm font-medium leading-snug text-brand-900">
                        {opp.title}
                      </div>
                      <Badge className="shrink-0 bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20">
                        <span className="tnum">score {fmtScore(opp.score)}</span>
                      </Badge>
                    </div>
                    <p className="mt-1 line-clamp-2 text-2xs leading-relaxed text-brand-200">
                      {opp.detail}
                    </p>
                    {(seg || opp.ticker) && (
                      <div className="mt-1.5 flex flex-wrap items-center gap-1">
                        {seg && (
                          <span className="inline-flex items-center gap-1 text-2xs text-brand-500">
                            <TrendingUp
                              size={11}
                              className="text-brand-700 transition group-hover:text-accent"
                            />
                            {seg.name}
                          </span>
                        )}
                        {opp.ticker && (
                          <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
                            <span className="tnum">{opp.ticker}</span>
                          </Badge>
                        )}
                      </div>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </section>

        <Divider />

        {/* 3) Top Risks */}
        <section>
          <RailLabel icon={<ShieldAlert size={12} />} right={`${decision.risks.length}`}>
            Top Risks · 风险
          </RailLabel>
          <div className="mt-2 flex flex-col gap-2">
            {decision.risks.length === 0 ? (
              <EmptyRow>No active risks</EmptyRow>
            ) : (
              decision.risks.map((risk) => (
                <div
                  key={risk.id}
                  className={cn(
                    "rounded-lg border border-line border-l-2 bg-canvas p-2.5",
                    risk.severity === "high"
                      ? "border-l-neg"
                      : risk.severity === "medium"
                        ? "border-l-warn"
                        : "border-l-brand-200",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="text-sm font-medium leading-snug text-brand-900">
                      {risk.title}
                    </div>
                    <Badge className={cn("shrink-0 capitalize", severityChip(risk.severity))}>
                      {risk.severity}
                    </Badge>
                  </div>
                  <p className="mt-1 text-2xs leading-relaxed text-brand-200">{risk.detail}</p>
                </div>
              ))
            )}
          </div>
        </section>

        <Divider />

        {/* 4) Action Queue */}
        <section>
          <RailLabel icon={<Lightbulb size={12} />} right={`${doneCount}/${decision.actions.length}`}>
            Action Queue · 待办
          </RailLabel>
          <div className="mt-2 flex flex-col">
            {decision.actions.length === 0 ? (
              <EmptyRow>Queue is clear</EmptyRow>
            ) : (
              decision.actions.map((action) => (
                <ActionRow
                  key={action.id}
                  action={action}
                  done={Boolean(done[action.id])}
                  onToggle={() => toggle(action.id)}
                />
              ))
            )}
          </div>
        </section>
      </div>
    </aside>
  );
}

/* ------------------------------------------------------------------------- */

function RailLabel({
  children,
  icon,
  right,
}: {
  children: React.ReactNode;
  icon?: React.ReactNode;
  right?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-brand-500">
        {icon && <span className="text-brand-500">{icon}</span>}
        {children}
      </div>
      {right != null && <span className="tnum text-2xs font-medium text-brand-700">{right}</span>}
    </div>
  );
}

function Divider() {
  return <div className="border-t border-line" />;
}

function EmptyRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-2.5 py-3 text-center text-2xs text-brand-500">
      {children}
    </div>
  );
}

const ACTION_ICON: Record<ActionItem["kind"], typeof Eye> = {
  review: Eye,
  add: Plus,
  rerate: RefreshCw,
  trim: Scissors,
};

function ActionRow({
  action,
  done,
  onToggle,
}: {
  action: ActionItem;
  done: boolean;
  onToggle: () => void;
}) {
  const KindIcon = ACTION_ICON[action.kind];
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={done}
      className="group flex w-full items-start gap-2.5 rounded-lg py-2 text-left transition hover:bg-canvas"
    >
      <span
        className={cn(
          "mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded border transition",
          done
            ? "border-accent bg-accent text-white"
            : "border-line bg-surface text-transparent group-hover:border-accent/50",
        )}
      >
        <Check size={11} strokeWidth={3} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <KindIcon
            size={12}
            strokeWidth={2}
            className={cn("shrink-0", done ? "text-brand-700" : "text-brand-500")}
          />
          <span
            className={cn(
              "min-w-0 text-xs leading-snug",
              done ? "text-brand-500 line-through" : "text-brand-900",
            )}
          >
            {action.label}
          </span>
        </span>
      </span>
      {action.ticker && (
        <Badge
          className={cn(
            "mt-px shrink-0 ring-1 ring-inset ring-line",
            done ? "bg-surface-2 text-brand-500" : "bg-surface-2 text-brand-500",
          )}
        >
          <span className="tnum">{action.ticker}</span>
        </Badge>
      )}
    </button>
  );
}
