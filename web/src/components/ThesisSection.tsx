import { useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  Eye,
  Loader2,
  RefreshCw,
  Scale,
  ShieldAlert,
  Sparkles,
  Target,
  TrendingDown,
  TrendingUp,
  Wind,
} from "lucide-react";
import { api } from "../lib/api";
import { cn, fmtPct, signClass } from "../lib/format";
import { contribTone, signalShort, signalToneChip } from "../types-alt";
import {
  debateStatusMeta,
  evidenceChipClass,
  healthOverallMeta,
  pillarKindLabel,
  pillarStatusMeta,
  stanceMeta,
  valuationCaseMeta,
  type DebateHealth,
  type Thesis,
  type ThesisDebate,
  type ThesisEvidence,
  type ThesisHealthPillar,
  type ThesisPillar,
} from "../types-thesis";
import { Badge, Card, ScoreBar, SectionHeader } from "./ui";

/**
 * Thesis 360 — full-width structured investment-thesis section on the company
 * page. Renders the latest LLM-built thesis (pillars / bull-bear / risks /
 * valuation scenarios / watch list) with machine-checked health, or an
 * empty-state build card when no thesis exists yet (the 99% case).
 */
export function ThesisSection({
  cid,
  thesis,
  onRefetch,
}: {
  cid: string;
  thesis: Thesis | null;
  onRefetch: () => Promise<void>;
}) {
  const [building, setBuilding] = useState(false);
  const [buildError, setBuildError] = useState<string | null>(null);

  const build = async (force: boolean) => {
    setBuilding(true);
    setBuildError(null);
    try {
      const r = await api.buildThesis(cid, force);
      if (r.status === "built" || r.status === "skipped") {
        await onRefetch();
      } else {
        setBuildError(`${r.status}${r.reason ? ` · ${r.reason}` : ""}`);
      }
    } catch (e) {
      setBuildError(String(e));
    } finally {
      setBuilding(false);
    }
  };

  // ---------------------------------------------------------------- empty --
  if (!thesis) {
    return (
      <Card>
        <SectionHeader
          title="Investment Thesis"
          titleCn="投资论点 · Thesis 360"
          icon={<Target size={15} strokeWidth={2} />}
        />
        <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-accent-50 text-accent">
            <Sparkles size={20} strokeWidth={2} />
          </span>
          <div className="text-sm font-semibold text-brand-900">尚未生成投资论点</div>
          <div className="max-w-md text-xs leading-relaxed text-slate-400">
            No thesis built for this name yet.
            基于全库证据(事件、财务、供应链、专家洞见)生成带证伪条件的结构化多空论点,约需 60 秒。
          </div>
          <button
            type="button"
            onClick={() => void build(false)}
            disabled={building}
            className={cn(
              "mt-1 inline-flex items-center gap-1.5 rounded-lg border border-accent/40 bg-accent-50 px-3.5 py-1.5 text-xs font-semibold text-accent-700",
              "transition-colors hover:bg-accent-100",
              building && "cursor-not-allowed opacity-60",
            )}
          >
            {building ? (
              <Loader2 size={14} className="animate-spin" strokeWidth={2.5} />
            ) : (
              <Sparkles size={14} strokeWidth={2.5} />
            )}
            {building ? "生成中… Building" : "生成论点 Build thesis"}
          </button>
          {building && (
            <div className="text-2xs text-slate-400">正在阅读全库证据并撰写论点,可能需要约一分钟…</div>
          )}
          {buildError && (
            <div className="max-w-md text-2xs text-neg">构建失败 Build failed · {buildError}</div>
          )}
        </div>
      </Card>
    );
  }

  // ----------------------------------------------------------------- full --
  const c = thesis.content ?? ({} as Thesis["content"]);
  const pillars = c.pillars ?? [];
  const drivers = c.drivers ?? [];
  const risks = c.risks ?? [];
  const valuation = c.valuation ?? [];
  const watch = c.what_to_watch ?? [];
  const gaps = c.coverage_gaps_zh ?? [];
  const health = thesis.health;
  const healthByKey = new Map<string, ThesisHealthPillar>(
    (health?.pillars ?? []).map((p) => [p.key, p]),
  );
  const debateByKey = new Map<string, DebateHealth>(
    (health?.debates ?? []).map((d) => [d.key, d]),
  );
  const stance = stanceMeta(thesis.stance);
  const overall = health ? healthOverallMeta(health.overall) : null;
  const q = thesis.quality;

  return (
    <Card>
      <SectionHeader
        title="Investment Thesis"
        titleCn="投资论点 · Thesis 360"
        icon={<Target size={15} strokeWidth={2} />}
        right={
          <div className="flex items-center gap-2">
            {overall && health && (
              <Badge
                className={overall.chip}
                title={`健康检查 as of ${health.as_of} · v${health.thesis_version}`}
              >
                <span className={cn("h-1.5 w-1.5 rounded-full", overall.dot)} />
                {overall.cn} {overall.en}
              </Badge>
            )}
            <span className="tnum hidden text-2xs text-slate-400 sm:inline">
              v{thesis.version} · {thesis.as_of}
            </span>
            <button
              type="button"
              onClick={() => void build(true)}
              disabled={building}
              title="强制重建论点 Rebuild (force)"
              className={cn(
                "inline-flex items-center gap-1 rounded-md border border-line bg-canvas px-2 py-1 text-2xs font-medium text-slate-400",
                "transition-colors hover:border-accent/40 hover:text-accent",
                building && "cursor-not-allowed opacity-60",
              )}
            >
              {building ? (
                <Loader2 size={11} className="animate-spin" strokeWidth={2.5} />
              ) : (
                <RefreshCw size={11} strokeWidth={2.5} />
              )}
              {building ? "重建中…" : "重建"}
            </button>
          </div>
        }
      />

      <div className="flex flex-col gap-4 px-4 py-4">
        {/* ------------------------- header band ------------------------- */}
        <div>
          <div className="flex flex-wrap items-center gap-2.5">
            <Badge className={cn("px-2 py-1 text-xs", stance.chip)}>
              {thesis.stance === "bull" ? (
                <TrendingUp size={13} strokeWidth={2.5} />
              ) : thesis.stance === "bear" ? (
                <TrendingDown size={13} strokeWidth={2.5} />
              ) : (
                <Scale size={13} strokeWidth={2.5} />
              )}
              {stance.cn} {stance.en}
            </Badge>
            <ConvictionDots value={thesis.conviction} dot={stance.dot} />
            {thesis.changed_because && (
              <span
                className="truncate text-2xs text-slate-400"
                title={`本版变化原因 · ${thesis.changed_because}`}
              >
                Δ {thesis.changed_because}
              </span>
            )}
          </div>
          <p className="mt-2.5 text-base font-semibold leading-snug text-brand-900">
            {thesis.one_liner || c.one_liner_zh || "—"}
          </p>
          {c.narrative_zh && (
            <details className="group mt-2">
              <summary className="cursor-pointer select-none text-2xs text-slate-400 transition hover:text-slate-300">
                完整叙述 Narrative
              </summary>
              <p className="mt-1.5 whitespace-pre-line rounded-lg border border-line bg-canvas px-3 py-2.5 text-xs leading-relaxed text-slate-300">
                {c.narrative_zh}
              </p>
            </details>
          )}
        </div>

        {/* ------------------------- core debates ------------------------ */}
        {(c.debates?.length ?? 0) > 0 && (
          <div>
            <SubHead
              icon={<Scale size={13} strokeWidth={2} />}
              en="Core Debates"
              cn="核心争论"
              right={<span className="tnum text-2xs text-slate-400">{c.debates!.length}</span>}
            />
            <div className="grid grid-cols-1 gap-2.5">
              {c.debates!.map((d, i) => (
                <DebateCard key={d.key || i} debate={d} health={debateByKey.get(d.key)} />
              ))}
            </div>
          </div>
        )}

        {/* --------------------------- pillars --------------------------- */}
        {pillars.length > 0 && (
          <div>
            <SubHead
              icon={<Target size={13} strokeWidth={2} />}
              en="Pillars"
              cn="论点支柱"
              right={<span className="tnum text-2xs text-slate-400">{pillars.length}</span>}
            />
            <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-2">
              {pillars.map((p, i) => (
                <PillarCard key={p.key || i} pillar={p} health={healthByKey.get(p.key)} />
              ))}
            </div>
          </div>
        )}

        {/* ------------------------- bull / bear ------------------------- */}
        {(c.bull_case_zh || c.bear_case_zh) && (
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
            <CaseBox
              tone="bull"
              icon={<TrendingUp size={13} strokeWidth={2.5} />}
              label="多头情形 Bull Case"
              text={c.bull_case_zh}
            />
            <CaseBox
              tone="bear"
              icon={<TrendingDown size={13} strokeWidth={2.5} />}
              label="空头情形 Bear Case"
              text={c.bear_case_zh}
            />
          </div>
        )}

        {/* --------------------- variant perception ---------------------- */}
        {c.variant_perception_zh && (
          <div className="rounded-lg border border-explore-500/25 bg-explore-50 px-3 py-2.5">
            <div className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wide text-explore-700">
              <Eye size={12} strokeWidth={2.5} /> Variant Perception 变体认知
            </div>
            <p className="mt-1 text-xs leading-relaxed text-slate-300">{c.variant_perception_zh}</p>
          </div>
        )}

        {/* --------------------- risks | valuation+watch ------------------ */}
        {(risks.length > 0 || valuation.length > 0 || watch.length > 0) && (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {/* risks */}
            {risks.length > 0 && (
              <div className="min-w-0">
                <SubHead
                  icon={<ShieldAlert size={13} strokeWidth={2} />}
                  en="Risks"
                  cn="风险"
                  right={<span className="tnum text-2xs text-slate-400">{risks.length}</span>}
                />
                <ul className="flex flex-col gap-1.5">
                  {risks.map((r, i) => (
                    <li key={i} className="rounded-lg border border-line bg-canvas px-3 py-2">
                      <div className="flex items-center gap-2">
                        <Badge className="bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20">
                          {r.type || "risk"}
                        </Badge>
                        <span className="text-2xs text-slate-400">严重度</span>
                        <div className="w-16 sm:w-24">
                          <ScoreBar value={clamp01(r.severity) * 100} scheme="good-low" height={5} />
                        </div>
                        <span className="tnum text-2xs font-semibold text-slate-400">
                          {Math.round(clamp01(r.severity) * 100)}
                        </span>
                      </div>
                      <p className="mt-1.5 text-xs leading-relaxed text-slate-300">{r.desc_zh}</p>
                      {r.watch_zh && (
                        <p className="mt-1 flex items-start gap-1 text-2xs text-slate-400">
                          <Eye size={11} strokeWidth={2} className="mt-0.5 shrink-0" />
                          观察 {r.watch_zh}
                        </p>
                      )}
                      <EvidenceChips evidence={r.evidence} className="mt-1.5" />
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="flex min-w-0 flex-col gap-4">
              {/* valuation scenarios */}
              {valuation.length > 0 && (
                <div>
                  <SubHead icon={<Scale size={13} strokeWidth={2} />} en="Valuation" cn="估值情景" />
                  <div className="overflow-x-auto rounded-lg border border-line">
                    <table className="w-full min-w-[420px] text-xs">
                      <thead>
                        <tr className="border-b border-line bg-canvas text-left text-2xs uppercase tracking-wide text-slate-400">
                          <th className="px-2.5 py-1.5 font-medium">情景</th>
                          <th className="px-2.5 py-1.5 font-medium">方法</th>
                          <th className="px-2.5 py-1.5 font-medium">假设</th>
                          <th className="px-2.5 py-1.5 font-medium">隐含观点</th>
                        </tr>
                      </thead>
                      <tbody>
                        {valuation.map((v, i) => {
                          const meta = valuationCaseMeta(v.case);
                          return (
                            <tr key={i} className="border-b border-line/60 align-top last:border-b-0">
                              <td className="px-2.5 py-2">
                                <Badge className={meta.chip}>
                                  {meta.cn} {meta.en}
                                </Badge>
                              </td>
                              <td className="px-2.5 py-2 text-slate-300">{v.method_zh || "—"}</td>
                              <td className="px-2.5 py-2 text-slate-400">{v.assumption_zh || "—"}</td>
                              <td className="px-2.5 py-2 font-medium text-brand-900">
                                {v.implied_view_zh || "—"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* what to watch */}
              {watch.length > 0 && (
                <div>
                  <SubHead
                    icon={<CalendarClock size={13} strokeWidth={2} />}
                    en="What to Watch"
                    cn="关键观察"
                    right={<span className="tnum text-2xs text-slate-400">{watch.length}</span>}
                  />
                  <ol className="flex flex-col">
                    {watch.map((w, i) => (
                      <li
                        key={i}
                        className="relative flex items-start gap-2.5 border-l border-line py-1.5 pl-3.5 last:pb-0"
                      >
                        <span className="absolute -left-[3.5px] top-[13px] h-1.5 w-1.5 rounded-full bg-accent" />
                        <span className="tnum w-16 shrink-0 pt-px text-2xs text-slate-400">
                          {w.when || "—"}
                        </span>
                        <span className="min-w-0 text-xs leading-relaxed text-slate-300">
                          {w.what_zh}
                          {w.direction_zh && (
                            <span className="ml-1.5 text-2xs text-slate-400">→ {w.direction_zh}</span>
                          )}
                        </span>
                        {w.pillar_key && (
                          <Badge
                            className="ml-auto shrink-0 bg-surface-2 text-slate-400 ring-1 ring-inset ring-line"
                            title="关联支柱"
                          >
                            {w.pillar_key}
                          </Badge>
                        )}
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
          </div>
        )}

        {/* --------------------------- drivers --------------------------- */}
        {drivers.length > 0 && (
          <div>
            <SubHead icon={<Wind size={13} strokeWidth={2} />} en="Drivers" cn="顺风 / 逆风" />
            <div className="flex flex-wrap gap-1.5">
              {drivers.map((d, i) => (
                <span
                  key={i}
                  title={d.note_zh}
                  className={cn(
                    "inline-flex items-center gap-1 rounded-md px-2 py-1 text-2xs font-medium ring-1 ring-inset",
                    d.direction === "headwind"
                      ? "bg-neg-50 text-neg-700 ring-neg/20"
                      : "bg-pos-50 text-pos-700 ring-pos/20",
                  )}
                >
                  {d.direction === "headwind" ? "▼" : "▲"} {d.name}
                  <span className="tnum opacity-60">{Math.round(clamp01(d.weight) * 100)}%</span>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* --------------------------- footer ----------------------------- */}
        {(gaps.length > 0 || q) && (
          <div className="flex flex-col gap-2.5 border-t border-line pt-3 lg:flex-row lg:items-start lg:justify-between">
            {gaps.length > 0 ? (
              <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                <span className="flex items-center gap-1 text-2xs text-warn-700">
                  <AlertTriangle size={11} strokeWidth={2.5} /> 覆盖缺口
                </span>
                {gaps.map((g, i) => (
                  <Badge key={i} className="bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/20">
                    {g}
                  </Badge>
                ))}
              </div>
            ) : (
              <span />
            )}
            {q && (
              <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-slate-400">
                <span title="有证据锚点的论断占比">
                  证据覆盖{" "}
                  <b className="tnum font-semibold text-slate-300">
                    {Math.round(clamp01(q.evidence_coverage) * 100)}%
                  </b>
                </span>
                <span title="含具体数值的支柱占比">
                  数值锚定{" "}
                  <b className="tnum font-semibold text-slate-300">
                    {Math.round(clamp01(q.numeric_grounding) * 100)}%
                  </b>
                </span>
                <span title="证据锚点总数">
                  锚点 <b className="tnum font-semibold text-slate-300">{q.evidence_anchors}</b>
                </span>
                <span title="喂给论点生成器的档案事实数">
                  档案事实 <b className="tnum font-semibold text-slate-300">{q.dossier_facts}</b>
                </span>
              </div>
            )}
          </div>
        )}

        {buildError && (
          <div className="text-2xs text-neg">重建失败 Rebuild failed · {buildError}</div>
        )}
      </div>
    </Card>
  );
}

// ===========================================================================
// Sub-components
// ===========================================================================

function clamp01(v: number | null | undefined): number {
  return Math.max(0, Math.min(1, v ?? 0));
}

/** 1..5 filled-dot conviction scale, colored by stance. */
function ConvictionDots({ value, dot }: { value: number; dot: string }) {
  const n = Math.max(0, Math.min(5, Math.round(value)));
  return (
    <span
      className="inline-flex items-center gap-1"
      title={`信念强度 Conviction ${n}/5`}
      aria-label={`Conviction ${n} of 5`}
    >
      {Array.from({ length: 5 }, (_, i) => (
        <span
          key={i}
          className={cn(
            "h-2 w-2 rounded-full",
            i < n ? dot : "bg-surface-2 ring-1 ring-inset ring-line",
          )}
        />
      ))}
      <span className="tnum ml-0.5 text-2xs text-slate-400">{n}/5</span>
    </span>
  );
}

/** Mini section label inside the thesis card. */
function SubHead({
  icon,
  en,
  cn: cnLabel,
  right,
}: {
  icon: ReactNode;
  en: string;
  cn: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-2 flex items-center gap-1.5">
      <span className="text-slate-400">{icon}</span>
      <span className="text-2xs font-medium uppercase tracking-wide text-slate-500">{en}</span>
      <span className="text-2xs text-slate-400">{cnLabel}</span>
      {right && <span className="ml-auto">{right}</span>}
    </div>
  );
}

/** `event:261`-style mono evidence chips, kind-colored, quote in title. */
function EvidenceChips({
  evidence,
  className,
}: {
  evidence: ThesisEvidence[] | undefined;
  className?: string;
}) {
  const list = evidence ?? [];
  if (list.length === 0) return null;
  return (
    <div className={cn("flex flex-wrap gap-1", className)}>
      {list.map((e, i) => (
        <span
          key={i}
          title={e.quote || `${e.kind}:${String(e.ref_id)}`}
          className={cn(
            "tnum cursor-help rounded px-1 py-0.5 font-mono text-2xs leading-none",
            evidenceChipClass(e.kind),
          )}
        >
          {e.kind}:{String(e.ref_id)}
        </span>
      ))}
    </div>
  );
}

/** One core debate: question + bull/bear steelman + a lean gauge (authored vs
 * current evidence) + VP readings and top confirming/falsifying facts. */
function DebateCard({ debate: d, health }: { debate: ThesisDebate; health?: DebateHealth }) {
  const st = health ? debateStatusMeta(health.status) : null;
  const authored = Math.max(-1, Math.min(1, d.lean ?? 0));
  const now = health ? Math.max(-1, Math.min(1, health.lean_now)) : authored;
  // gauge: -1 (bear) … 0 … +1 (bull) → 0..100% left offset
  const pct = (v: number) => ((v + 1) / 2) * 100;
  return (
    <div className="rounded-xl border border-line bg-canvas p-3">
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium leading-snug text-slate-200">{d.question_zh}</p>
        {st && (
          <Badge className={cn("shrink-0 text-2xs", st.chip)} title={`${st.cn} ${st.en}`}>
            {st.cn}
          </Badge>
        )}
      </div>
      {/* lean gauge: authored (hollow) vs now (solid) on a bear↔bull scale */}
      <div className="relative mt-2.5 h-1.5 rounded-full bg-gradient-to-r from-neg/40 via-slate-600/40 to-pos/40">
        <div
          className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border border-slate-300 bg-canvas"
          style={{ left: `${pct(authored)}%` }}
          title={`作者态 lean ${authored >= 0 ? "+" : ""}${authored.toFixed(2)}`}
        />
        {health && (
          <div
            className={cn("absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full", st?.dot ?? "bg-slate-400")}
            style={{ left: `${pct(now)}%` }}
            title={`当前 lean ${now >= 0 ? "+" : ""}${now.toFixed(2)}（${health.n_facts} 证据）`}
          />
        )}
      </div>
      <div className="mt-1 flex justify-between text-3xs text-slate-500">
        <span>空方 Bear</span>
        <span>多方 Bull</span>
      </div>
      <div className="mt-2 grid grid-cols-1 gap-1.5 md:grid-cols-2">
        <div className="rounded-lg border border-pos/20 bg-pos-50/40 px-2 py-1.5">
          <div className="text-3xs font-semibold text-pos-700">多方 Bull</div>
          <p className="mt-0.5 text-2xs leading-relaxed text-slate-300">{d.bull_zh}</p>
        </div>
        <div className="rounded-lg border border-neg/20 bg-neg-50/40 px-2 py-1.5">
          <div className="text-3xs font-semibold text-neg-700">空方 Bear</div>
          <p className="mt-0.5 text-2xs leading-relaxed text-slate-300">{d.bear_zh}</p>
        </div>
      </div>
      {/* verification points: latest reading vs thresholds */}
      {d.verification_points.length > 0 && (
        <div className="mt-2 space-y-1">
          {d.verification_points.map((vp) => {
            const r = health?.vp_readings.find((x) => x.metric === vp.metric);
            return (
              <div key={vp.key} className="flex items-center justify-between gap-2 text-2xs">
                <span className="truncate text-slate-400" title={vp.question_zh}>
                  {vp.metric || vp.question_zh}
                </span>
                <span className="shrink-0 tnum text-slate-500">
                  {vp.bear_threshold != null && vp.bull_threshold != null
                    ? `空≤${vp.bear_threshold} · 多≥${vp.bull_threshold}`
                    : "事件型"}
                  {r && <span className={cn("ml-1.5", signClass(r.verdict === "confirms_bull" ? 1 : r.verdict === "confirms_bear" ? -1 : 0))}>· {r.verdict}</span>}
                </span>
              </div>
            );
          })}
        </div>
      )}
      {/* top confirming/falsifying facts */}
      {(health?.top_facts?.length ?? 0) > 0 && (
        <details className="group mt-1.5">
          <summary className="cursor-pointer select-none text-3xs text-slate-500 hover:text-slate-400">
            近期证据 {health!.top_facts.length}
          </summary>
          <ul className="mt-1 space-y-0.5">
            {health!.top_facts.map((f, i) => (
              <li key={i} className="text-2xs text-slate-400">
                <span className={cn(signClass(f.verdict === "confirms_bull" ? 1 : -1))}>{f.verdict}</span>
                {" · "}
                {f.rationale_zh}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/** One thesis pillar: kind + title + health dot, weight/score bars, claim,
 * falsifier warn-box, evidence chips, watch metrics. */
function PillarCard({ pillar: p, health }: { pillar: ThesisPillar; health?: ThesisHealthPillar }) {
  const kind = pillarKindLabel(p.kind);
  const status = health ? pillarStatusMeta(health.status) : null;
  const score = Math.max(-1, Math.min(1, p.score ?? 0));
  const watchMetrics = p.watch_metrics ?? [];
  // health_v2 alt-data roll-up (rare): a "challenging" pillar driven by a
  // negative signal_score reads as signal-driven and gets a distinct ring.
  const hasSignal = typeof health?.signal_score === "number";
  const sig = hasSignal ? Math.max(-1, Math.min(1, health!.signal_score as number)) : 0;
  const signalDriven = hasSignal && health?.status === "challenging";
  const pillarSignals = health?.signals ?? [];
  return (
    <div
      className={cn(
        "flex min-w-0 flex-col rounded-lg border bg-canvas p-3",
        signalDriven
          ? sig < 0
            ? "border-neg/45 ring-1 ring-inset ring-neg/25"
            : "border-warn/45 ring-1 ring-inset ring-warn/25"
          : "border-line",
      )}
    >
      <div className="flex items-center gap-2">
        <Badge className="shrink-0 bg-brand-50 text-brand-200 ring-1 ring-inset ring-brand-100">
          {kind.cn} {kind.en}
        </Badge>
        <span className="truncate text-sm font-semibold text-brand-900">{p.title_zh}</span>
        {status && health && (
          <span
            title={`${status.cn} ${status.en} · 新事实 ${health.new_facts} · 净极性 ${health.net_polarity > 0 ? "+" : ""}${health.net_polarity}`}
            className={cn("ml-auto h-2 w-2 shrink-0 cursor-help rounded-full", status.dot)}
          />
        )}
      </div>

      <div className="mt-2.5 grid grid-cols-2 gap-3">
        <div>
          <div className="mb-1 flex items-center justify-between text-2xs text-slate-400">
            <span>权重 Weight</span>
            <span className="tnum">{Math.round(clamp01(p.weight) * 100)}%</span>
          </div>
          <div className="h-[5px] w-full overflow-hidden rounded-full bg-surface-2">
            <div
              className="h-full rounded-full bg-accent"
              style={{ width: `${clamp01(p.weight) * 100}%` }}
            />
          </div>
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between text-2xs text-slate-400">
            <span>评分 Score</span>
            <span className={cn("tnum font-semibold", signClass(score))}>
              {score > 0 ? "+" : ""}
              {score.toFixed(2)}
            </span>
          </div>
          <ScoreBar value={score * 100} scheme="divergent" height={5} />
        </div>
      </div>

      {hasSignal && (
        <div className="mt-2.5 rounded-md border border-line bg-surface-2/40 px-2.5 py-2">
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1 text-2xs font-medium text-slate-400">
              <Activity size={11} strokeWidth={2} /> 信号 Signal
            </span>
            <div className="flex-1">
              <ScoreBar value={sig * 100} scheme="divergent" height={5} />
            </div>
            <span className={cn("tnum text-2xs font-semibold", signClass(sig))}>
              {sig > 0 ? "+" : ""}
              {sig.toFixed(2)}
            </span>
          </div>
          {pillarSignals.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {pillarSignals.slice(0, 4).map((sg, i) => (
                <span
                  key={sg.signal_key + i}
                  title={`${signalShort(sg.signal_key).short} · z ${sg.z.toFixed(2)} · 动量 ${fmtPct(sg.momentum * 100)} · ${sg.period_end}`}
                  className={cn(
                    "tnum inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs",
                    signalToneChip(contribTone(sg.contribution)),
                  )}
                >
                  {sg.name_cn}
                  <b className="font-semibold">
                    {sg.z > 0 ? "+" : ""}
                    {sg.z.toFixed(1)}
                  </b>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <p className="mt-2 text-xs leading-relaxed text-slate-300">{p.claim_zh}</p>

      {p.falsifier_zh && (
        <div className="mt-2 rounded-md border border-dashed border-warn/40 bg-warn-50 px-2.5 py-1.5 text-2xs leading-relaxed text-warn-700">
          <span className="font-semibold">证伪条件</span> {p.falsifier_zh}
        </div>
      )}

      <EvidenceChips evidence={p.evidence} className="mt-2" />

      {watchMetrics.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1 text-2xs text-slate-400">
          <Eye size={11} strokeWidth={2} className="shrink-0" />
          {watchMetrics.map((m, i) => (
            <span key={i} className="rounded bg-surface-2 px-1 py-0.5 ring-1 ring-inset ring-line">
              {m}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Tinted bull / bear case box. */
function CaseBox({
  tone,
  icon,
  label,
  text,
}: {
  tone: "bull" | "bear";
  icon: ReactNode;
  label: string;
  text: string;
}) {
  const cls =
    tone === "bull"
      ? "border-pos/20 bg-pos-50 text-pos-700"
      : "border-neg/20 bg-neg-50 text-neg-700";
  return (
    <div className={cn("rounded-lg border px-3 py-2.5", cls)}>
      <div className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wide">
        {icon} {label}
      </div>
      <p className="mt-1 text-xs leading-relaxed text-slate-300">{text || "—"}</p>
    </div>
  );
}
