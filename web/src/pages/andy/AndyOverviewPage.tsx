import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, ChevronDown, ChevronRight, Lamp, Link2 } from "lucide-react";
import { andy } from "../../lib/andy";
import { cn } from "../../lib/format";
import { Badge, Card, SectionHeader } from "../../components/ui";
import { useAndy } from "../../components/andy/AndyLayout";
import {
  AnchorChip,
  HARDNESS_META,
  VerdictLamp,
  verdictMeta,
  WindowCountdown,
  hardnessMeta,
} from "../../components/andy/constants";
import { AndyContainer, AndyError, AndyLoading, LinkUnavailable, useAsync } from "./_shared";
import type { Hardness } from "../../types-andy";

const HARDNESS_ORDER: Hardness[] = ["hard", "medium", "soft", "wall"];

/** /andy — Overview 总览: hardness distribution, the 9-claim verdict lamp wall,
 * theory-anchor strip (A1–A8 + META) and the 勾稽 theme mini-matrix. One screen,
 * dense, terminal-grade. */
export function AndyOverviewPage() {
  const { asOf, withAsOf } = useAndy();
  const metricsQ = useAsync(() => andy.metrics(), []);
  const claimsQ = useAsync(() => andy.overclaims(0), []);
  const anchorsQ = useAsync(() => andy.anchors(), []);
  const linksQ = useAsync(() => andy.linkThemes(), []);

  const counts = useMemo(() => {
    const c: Record<Hardness, number> = { hard: 0, medium: 0, soft: 0, wall: 0 };
    for (const m of metricsQ.data?.metrics ?? []) {
      if (m.hardness in c) c[m.hardness] += 1;
    }
    return c;
  }, [metricsQ.data]);

  const [openAnchor, setOpenAnchor] = useState<string | null>(null);
  const anchorDetail = anchorsQ.data?.anchors.find((a) => a.anchor_key === openAnchor) ?? null;

  if (metricsQ.loading && claimsQ.loading) return <AndyLoading label="Loading Andy…" />;
  if (metricsQ.error && claimsQ.error) return <AndyError error={metricsQ.error} />;

  return (
    <AndyContainer wide>
      <div className="flex flex-col gap-4">
        {/* (a) hardness distribution KPI tiles */}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {HARDNESS_ORDER.map((h) => {
            const m = HARDNESS_META[h];
            const to = h === "wall" ? withAsOf("/andy/walls") : withAsOf(`/andy/metrics?hardness=${h}`);
            return (
              <Link
                key={h}
                to={to}
                className="group rounded-xl border border-line bg-surface px-4 py-3 shadow-card transition-colors hover:border-andy/40"
              >
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-brand-500">
                    <span className={cn("h-2 w-2 rounded-full", m.dot)} aria-hidden="true" />
                    {m.en}
                  </span>
                  <span className={cn("text-2xs", m.text)}>{m.cn}</span>
                </div>
                <div className="tnum mt-1.5 text-2xl font-semibold leading-none text-brand-900">
                  {metricsQ.data ? counts[h] : "—"}
                </div>
                <div className="mt-1 text-2xs text-brand-200">
                  {h === "hard" && "物理/会计事实 · 可直接读取"}
                  {h === "medium" && "逻辑推论 · value/slope 可判"}
                  {h === "soft" && "待识别假说 · 相关≠因果"}
                  {h === "wall" && "不可量化边界 · value 恒 NULL"}
                </div>
              </Link>
            );
          })}
        </div>

        {/* (b) overclaim verdict lamp wall */}
        <Card>
          <SectionHeader
            title="Overclaim Verdict Wall"
            titleCn="过度宣称 · 判定灯墙"
            icon={<Lamp size={15} strokeWidth={2} />}
            right={
              <Link
                to={withAsOf("/andy/overclaims")}
                className="text-2xs font-medium text-andy-500 transition-colors hover:text-andy-600"
              >
                登记簿 →
              </Link>
            }
          />
          {claimsQ.loading ? (
            <div className="px-4 py-8 text-center text-xs text-brand-500">Loading…</div>
          ) : claimsQ.error ? (
            <div className="px-4 py-8 text-center text-xs text-brand-200">登记簿暂不可用 · {claimsQ.error}</div>
          ) : (
            <div className="grid grid-cols-1 gap-2 p-3 md:grid-cols-2 xl:grid-cols-3">
              {(claimsQ.data?.claims ?? []).map((c) => (
                <Link
                  key={c.claim_key}
                  to={withAsOf("/andy/overclaims")}
                  className="flex flex-col gap-1.5 rounded-lg border border-line bg-canvas px-3 py-2.5 transition-colors hover:border-andy/40"
                >
                  <div className="flex items-center justify-between gap-2">
                    <VerdictLamp status={c.status} />
                    <WindowCountdown
                      windowStart={c.window_start}
                      decisionWindow={c.decision_window}
                      asOf={asOf}
                    />
                  </div>
                  <p className="line-clamp-2 text-xs leading-snug text-brand-800">{c.claim_text_zh}</p>
                  <div className="flex items-center gap-1.5">
                    <span className="truncate font-mono text-2xs text-brand-200">{c.claim_key}</span>
                    {c.needs_identification && (
                      <span className="shrink-0 rounded border border-dashed border-warn/50 bg-warn-50 px-1 py-px text-2xs text-warn-700">
                        未识别
                      </span>
                    )}
                  </div>
                </Link>
              ))}
              {(claimsQ.data?.claims ?? []).length === 0 && (
                <div className="col-span-full px-2 py-6 text-center text-xs text-brand-200">
                  登记簿为空 · no claims registered
                </div>
              )}
            </div>
          )}
        </Card>

        {/* (c) theory anchor strip */}
        <Card>
          <SectionHeader
            title="Theory Anchors"
            titleCn="理论锚点 A1–A8 + META"
            icon={<Anchor size={15} strokeWidth={2} />}
            right={
              anchorsQ.data && (
                <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
                  {anchorsQ.data.count}
                </Badge>
              )
            }
          />
          <div className="p-3">
            {anchorsQ.loading ? (
              <div className="py-4 text-center text-xs text-brand-500">Loading…</div>
            ) : anchorsQ.error ? (
              <div className="py-4 text-center text-xs text-brand-200">锚点暂不可用</div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-1.5">
                  {(anchorsQ.data?.anchors ?? []).map((a) => (
                    <AnchorChip
                      key={a.anchor_key}
                      anchor={a.anchor_key}
                      title={a.title}
                      active={openAnchor === a.anchor_key}
                      onClick={() =>
                        setOpenAnchor((cur) => (cur === a.anchor_key ? null : a.anchor_key))
                      }
                    />
                  ))}
                  <span className="ml-1 text-2xs text-brand-200">
                    {openAnchor ? (
                      <ChevronDown size={12} className="inline" />
                    ) : (
                      <ChevronRight size={12} className="inline" />
                    )}{" "}
                    点击展开硅基重述
                  </span>
                </div>
                {anchorDetail && (
                  <div className="mt-3 rounded-lg border border-andy/25 bg-andy-50/60 px-3 py-2.5">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                      <span className="font-mono text-2xs text-andy-500">{anchorDetail.anchor_key}</span>
                      <span className="text-sm font-semibold text-brand-900">{anchorDetail.title}</span>
                      <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
                        verdict · {anchorDetail.verdict}
                      </Badge>
                    </div>
                    <div className="mt-2 grid gap-2 text-xs leading-relaxed md:grid-cols-2">
                      <div>
                        <div className="text-2xs uppercase tracking-wide text-brand-200">
                          工业时代假设 Industrial assumption
                        </div>
                        <p className="mt-0.5 text-brand-700">{anchorDetail.industrial_assumption}</p>
                      </div>
                      <div>
                        <div className="text-2xs uppercase tracking-wide text-andy-500">
                          硅基重述 Silicon restatement
                        </div>
                        <p className="mt-0.5 text-brand-800">{anchorDetail.silicon_restatement}</p>
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </Card>

        {/* (d) 勾稽 mini-matrix (crosswalk themes → linked metrics) */}
        <Card>
          <SectionHeader
            title="Crosswalk Matrix"
            titleCn="勾稽 · 主题 × 宏观指标"
            icon={<Link2 size={15} strokeWidth={2} />}
            right={
              linksQ.data && (
                <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
                  {linksQ.data.themes.length} themes · {linksQ.data.platform_metrics.length} platform
                </Badge>
              )
            }
          />
          {linksQ.loading || linksQ.error || !linksQ.data ? (
            <div className="p-3">
              <LinkUnavailable loading={linksQ.loading} />
            </div>
          ) : (
            <ul className="divide-y divide-line">
              {linksQ.data.themes.map((t) => (
                <li key={t.theme}>
                  <Link
                    to={withAsOf(`/andy/metrics?theme=${encodeURIComponent(t.theme)}`)}
                    className="flex items-center gap-3 px-4 py-2 transition-colors hover:bg-canvas"
                  >
                    <div className="w-44 min-w-0 shrink-0">
                      <div className="truncate text-xs font-semibold text-brand-900">{t.name_cn}</div>
                      <div className="truncate text-2xs text-brand-200">{t.name}</div>
                    </div>
                    <Badge
                      className={cn(
                        "shrink-0",
                        t.kind === "chain"
                          ? "bg-andy-50 text-andy-500 ring-1 ring-inset ring-andy/25"
                          : "bg-explore-50 text-explore-500 ring-1 ring-inset ring-explore/25",
                      )}
                    >
                      {t.kind === "chain" ? "产业链" : "周期"}
                    </Badge>
                    <span className="tnum w-14 shrink-0 text-right text-xs font-semibold text-brand-900">
                      {t.metrics.length}
                      <span className="ml-0.5 font-normal text-brand-200">指标</span>
                    </span>
                    <div className="hidden min-w-0 flex-1 items-center gap-1.5 overflow-hidden md:flex">
                      {t.metrics.slice(0, 3).map((m) => (
                        <span
                          key={m.metric_key}
                          className="inline-flex shrink-0 items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-2xs text-brand-700 ring-1 ring-inset ring-line"
                          title={m.metric_key}
                        >
                          <span
                            className={cn("h-1.5 w-1.5 rounded-full", hardnessMeta(m.hardness).dot)}
                            aria-hidden="true"
                          />
                          {m.display_name_zh}
                        </span>
                      ))}
                      {t.metrics.length > 3 && (
                        <span className="text-2xs text-brand-200">+{t.metrics.length - 3}</span>
                      )}
                    </div>
                    <div className="ml-auto flex shrink-0 items-center gap-1">
                      {t.overclaims.map((oc) => (
                        <span
                          key={oc.claim_key}
                          title={`${oc.claim_key} · ${verdictMeta(oc.status).cn}`}
                          className={cn("h-2 w-2 rounded-full", verdictMeta(oc.status).lamp)}
                        />
                      ))}
                    </div>
                  </Link>
                </li>
              ))}
              {linksQ.data.themes.length === 0 && (
                <li className="px-4 py-6 text-center text-xs text-brand-200">暂无勾稽主题</li>
              )}
            </ul>
          )}
        </Card>

        {/* discipline footer */}
        {metricsQ.data?.disclaimer && (
          <div className="rounded-lg border border-dashed border-line px-3 py-1.5 text-2xs leading-relaxed text-brand-200">
            {metricsQ.data.disclaimer}
          </div>
        )}
      </div>
    </AndyContainer>
  );
}
