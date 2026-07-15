import { Activity, Gauge, Layers, TrendingUp, Waves } from "lucide-react";
import { Link } from "react-router-dom";
import { andy } from "../../lib/andy";
import { cn } from "../../lib/format";
import { Badge, Card, ScoreBar, SectionHeader, Sparkline } from "../../components/ui";
import { useAndy } from "../../components/andy/AndyLayout";
import { AndyContainer, AndyError, AndyLoading, useAsync } from "./_shared";
import type { FlowEvent, FlowStance } from "../../types-andy";

/** /andy/flow — 资金流策略页:大类资产流向 / 风格与广度 / 情绪与仓位 / 策略综合。
 * 数据 = 工人 flow 源日频落库的 flow.* 信号(alt_signals)+ 语义 flow 事件;
 * 全部读数经 as-of PIT 边界(useAndy)。规则式追踪,不做交易执行。 */

const fmtZ = (v: number | null | undefined) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}`;
const fmtPct = (v: number | null | undefined) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
const zTone = (v: number | null | undefined) =>
  v == null ? "text-brand-200" : v >= 0.8 ? "text-pos" : v <= -0.8 ? "text-neg" : "text-brand-700";

const STANCE_META: Record<FlowStance, { cn: string; cls: string }> = {
  overweight: { cn: "增配", cls: "bg-pos/10 text-pos ring-1 ring-inset ring-pos/30" },
  neutral: { cn: "中性", cls: "bg-surface-2 text-brand-500 ring-1 ring-inset ring-line" },
  underweight: { cn: "减配", cls: "bg-neg/10 text-neg ring-1 ring-inset ring-neg/30" },
  no_data: { cn: "无数据", cls: "bg-surface-2 text-brand-200 ring-1 ring-inset ring-line" },
};

function EventRow({ e }: { e: FlowEvent }) {
  const dot = e.polarity === "positive" ? "bg-pos" : e.polarity === "negative" ? "bg-neg" : "bg-brand-200";
  const itype = (e.attrs?.investor_type as string) || null;
  return (
    <li className="flex items-start gap-2 px-3 py-2">
      <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", dot)} aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-xs leading-snug text-brand-800">{e.summary}</p>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-brand-200">
          {e.date && <span className="tnum">{e.date}</span>}
          <span>{e.type === "flow_insight" ? "语义点评" : "量价信号"}</span>
          {itype && <span className="rounded bg-surface-2 px-1 ring-1 ring-inset ring-line">{itype}</span>}
          {e.company && <span className="truncate">{e.company}</span>}
        </div>
      </div>
    </li>
  );
}

export function AndyFlowPage() {
  const { asOf } = useAndy();
  const flowQ = useAsync(() => andy.flow(asOf), [asOf]);

  if (flowQ.loading) return <AndyLoading label="Loading money flow…" />;
  if (flowQ.error) return <AndyError error={flowQ.error} />;
  const d = flowQ.data;
  if (!d) return null;

  const hasData = d.assets.some((a) => a.composite != null);
  const riskOn = d.strategy.risk_on.value;

  return (
    <AndyContainer wide>
      <div className="flex flex-col gap-4">
        {!hasData && (
          <div className="rounded-lg border border-dashed border-warn/40 bg-warn-50/40 px-3 py-2 text-2xs text-warn-700">
            首轮资金流数据尚未落库 —— 工人 "flow" 源(日频)完成一轮后此页填充;可在 Jarvy → Fetchy 确认该源已开。
          </div>
        )}

        {/* (a) 策略综合:risk-on 表盘 + 每资产类倾斜 */}
        <Card>
          <SectionHeader
            title="Strategy Composite"
            titleCn="策略综合 · 资金开/关风险"
            icon={<Gauge size={15} strokeWidth={2} />}
            right={
              <span className="flex items-center gap-2 text-2xs text-brand-200">
                规则式追踪(z 加权),不构成交易指令
              </span>
            }
          />
          <div className="grid gap-3 p-3 lg:grid-cols-[220px_1fr]">
            <div className="rounded-lg border border-line bg-canvas px-3 py-3">
              <div className="text-2xs uppercase tracking-wide text-brand-200">Risk-on 综合分</div>
              <div className={cn("tnum mt-1 text-3xl font-semibold leading-none", zTone(riskOn == null ? null : riskOn * 3))}>
                {riskOn == null ? "—" : riskOn.toFixed(2)}
              </div>
              <div className="mt-1 text-2xs text-brand-200">
                {riskOn == null ? "等待首轮计算" : riskOn >= 0.25 ? "资金开风险(risk-on)" : riskOn <= -0.25 ? "资金关风险(risk-off)" : "中性/切换中"}
              </div>
              {d.strategy.risk_on.series.length > 1 && (
                <Sparkline data={d.strategy.risk_on.series.map((p) => p.v)} width={170} height={36} className="mt-2" />
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-xs">
                <thead>
                  <tr className="text-left text-2xs uppercase tracking-wide text-brand-200">
                    <th className="px-2 py-1.5 font-medium">资产类</th>
                    <th className="px-2 py-1.5 font-medium">倾斜</th>
                    <th className="px-2 py-1.5 font-medium">综合分</th>
                    <th className="px-2 py-1.5 font-medium">构成(ETF · composite)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {d.strategy.tilts.map((t) => (
                    <tr key={t.asset_class}>
                      <td className="px-2 py-1.5 font-medium text-brand-900">{t.label_cn}</td>
                      <td className="px-2 py-1.5">
                        <span className={cn("rounded px-1.5 py-0.5 text-2xs font-semibold", STANCE_META[t.stance].cls)}>
                          {STANCE_META[t.stance].cn}
                        </span>
                      </td>
                      <td className="w-40 px-2 py-1.5">
                        {t.score == null ? (
                          <span className="text-brand-200">—</span>
                        ) : (
                          <div className="flex items-center gap-2">
                            <ScoreBar value={t.score * 100} scheme="divergent" className="w-20" />
                            <span className={cn("tnum", zTone(t.score * 3))}>{t.score.toFixed(2)}</span>
                          </div>
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        <div className="flex flex-wrap gap-1">
                          {t.drivers.map((dr) => (
                            <span
                              key={dr.ticker}
                              title={`OBV z ${fmtZ(dr.obv_z)} · 63日动量 ${fmtPct(dr.mom_63d)}`}
                              className="tnum inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-2xs text-brand-700 ring-1 ring-inset ring-line"
                            >
                              {dr.ticker}
                              <span className={zTone(dr.composite == null ? null : dr.composite * 3)}>
                                {dr.composite == null ? "—" : dr.composite.toFixed(2)}
                              </span>
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Card>

        {/* (b) 大类资产流向矩阵 */}
        <Card>
          <SectionHeader
            title="Cross-Asset Flow Matrix"
            titleCn="大类资产流向 · ETF 量价矩阵"
            icon={<Waves size={15} strokeWidth={2} />}
            right={<Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">{d.assets.length} ETF</Badge>}
          />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-xs">
              <thead>
                <tr className="text-left text-2xs uppercase tracking-wide text-brand-200">
                  <th className="px-3 py-1.5 font-medium">资产</th>
                  <th className="px-2 py-1.5 font-medium" title="OBV 量能累积 20日增量 z:资金在买还是在卖">OBV z</th>
                  <th className="px-2 py-1.5 font-medium" title="20日均美元成交额 z:参与度/换手热度">成交额 z</th>
                  <th className="px-2 py-1.5 font-medium" title="63 交易日价格动量">63日动量</th>
                  <th className="px-2 py-1.5 font-medium" title="OBV z 与动量 z 的均值 /3,∈[-1,1]">综合</th>
                  <th className="px-2 py-1.5 font-medium">动量走势</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {d.assets.map((a) => (
                  <tr key={a.ticker} className="transition-colors hover:bg-canvas">
                    <td className="px-3 py-1.5">
                      <span className="tnum font-semibold text-brand-900">{a.ticker}</span>
                      <span className="ml-2 text-2xs text-brand-500">{a.label_cn}</span>
                    </td>
                    <td className={cn("tnum px-2 py-1.5", zTone(a.obv_z))}>{fmtZ(a.obv_z)}</td>
                    <td className={cn("tnum px-2 py-1.5", zTone(a.dollar_vol_z))}>{fmtZ(a.dollar_vol_z)}</td>
                    <td className={cn("tnum px-2 py-1.5", zTone(a.mom_z))}>{fmtPct(a.mom_63d)}</td>
                    <td className={cn("tnum px-2 py-1.5 font-semibold", zTone(a.composite == null ? null : a.composite * 3))}>
                      {a.composite == null ? "—" : a.composite.toFixed(2)}
                    </td>
                    <td className="px-2 py-1.5">
                      {a.spark.length > 1 && <Sparkline data={a.spark.map((p) => p.v)} width={90} height={20} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        {/* (c) 风格与广度 */}
        <Card>
          <SectionHeader
            title="Style & Breadth"
            titleCn="风格与广度 · 因子对相对强弱"
            icon={<Layers size={15} strokeWidth={2} />}
          />
          <div className="grid grid-cols-2 gap-2 p-3 md:grid-cols-3 xl:grid-cols-4">
            {d.styles.map((s) => (
              <div key={s.pair} title={s.rationale_zh} className="rounded-lg border border-line bg-canvas px-3 py-2.5">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="tnum text-xs font-semibold text-brand-900">{s.pair}</span>
                  <span className={cn("tnum text-sm font-semibold", zTone(s.z))}>{fmtZ(s.z)}</span>
                </div>
                <div className="mt-0.5 truncate text-2xs text-brand-500">{s.label_cn}</div>
                {s.series.length > 1 && (
                  <Sparkline data={s.series.map((p) => p.v)} width={150} height={26} className="mt-1.5" />
                )}
              </div>
            ))}
          </div>
        </Card>

        {/* (d) 情绪与仓位 */}
        <div className="grid gap-4 xl:grid-cols-2">
          <Card>
            <SectionHeader
              title="Sentiment & Positioning"
              titleCn="情绪与仓位 · P/C + 空头回补榜"
              icon={<Activity size={15} strokeWidth={2} />}
            />
            <div className="p-3">
              <div className="flex items-center gap-4 rounded-lg border border-line bg-canvas px-3 py-2.5">
                <div>
                  <div className="text-2xs uppercase tracking-wide text-brand-200" title="SPY 近月期权 Put/Call 比;>1 防御对冲盘主导,极端值常为反指">
                    SPY Put/Call {d.sentiment.pc.basis ? `(${d.sentiment.pc.basis})` : ""}
                  </div>
                  <div className="tnum mt-0.5 text-2xl font-semibold text-brand-900">
                    {d.sentiment.pc.value == null ? "—" : d.sentiment.pc.value.toFixed(2)}
                  </div>
                </div>
                {d.sentiment.pc.series.length > 1 && (
                  <Sparkline data={d.sentiment.pc.series.map((p) => p.v)} width={140} height={32} />
                )}
              </div>
              <div className="mt-3">
                <div className="mb-1 text-2xs uppercase tracking-wide text-brand-200" title="空头持仓 / 日均成交量:全部空头平仓所需天数(挤压弹药)">
                  空头回补天数榜(days to cover)
                </div>
                {d.sentiment.short_interest_top.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-line px-3 py-2 text-2xs text-brand-200">
                    空头数据未接入(Massive short-interest 端点未开通或首轮未跑)
                  </div>
                ) : (
                  <ul className="divide-y divide-line rounded-lg border border-line">
                    {d.sentiment.short_interest_top.map((r) => (
                      <li key={r.company_id} className="flex items-center gap-2 px-3 py-1.5 text-xs">
                        <Link to={`/genny/company/${r.company_id}`} className="min-w-0 flex-1 truncate text-brand-800 hover:text-accent-100">
                          {r.name} {r.ticker && <span className="tnum text-brand-200">({r.ticker})</span>}
                        </Link>
                        <span className="tnum font-semibold text-brand-900">{r.days_to_cover}天</span>
                        <span className="tnum text-2xs text-brand-200">{r.period_end}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </Card>

          <Card>
            <SectionHeader
              title="Flow Events"
              titleCn="资金流事件流 · 量价越阈 + 投行点评"
              icon={<TrendingUp size={15} strokeWidth={2} />}
            />
            {d.sentiment.flow_events.length === 0 ? (
              <div className="px-4 py-8 text-center text-xs text-brand-200">
                暂无资金流事件 —— 量价越阈信号与语义抽取(flow_insight)落库后出现
              </div>
            ) : (
              <ul className="max-h-[420px] divide-y divide-line overflow-y-auto scroll-thin">
                {d.sentiment.flow_events.map((e, i) => (
                  <EventRow key={`${e.date}-${i}`} e={e} />
                ))}
              </ul>
            )}
          </Card>
        </div>

        {/* (e) 主题净分:宏观 → 行业(Genny)交接把手 */}
        <Card>
          <SectionHeader
            title="Theme Net Flow"
            titleCn="行业主题资金流净分 · 深入 Genny"
            icon={<Waves size={15} strokeWidth={2} />}
          />
          <div className="flex flex-wrap gap-2 p-3">
            {d.themes.map((t) => (
              <Link
                key={t.theme}
                to={t.genny_link}
                title={t.as_of ? `as of ${t.as_of}` : "未计算"}
                className="flex items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-1.5 text-xs transition-colors hover:border-accent/40"
              >
                <span className="text-brand-800">{t.name_cn}</span>
                <span className={cn("tnum font-semibold", zTone(t.score == null ? null : t.score * 3))}>
                  {t.score == null ? "—" : t.score.toFixed(2)}
                </span>
              </Link>
            ))}
          </div>
        </Card>

        <div className="rounded-lg border border-dashed border-line px-3 py-1.5 text-2xs leading-relaxed text-brand-200">
          口径:量价 z 为 20 日增量对 120 日历史(clip ±3);综合分/净分 ∈ [-1,1];资金类型分布(HF/LO/retail)
          的硬数据仅 13F 机构持仓与空头持仓,细分主要来自语义抽取(标"语义点评"),非托管数据。
        </div>
      </div>
    </AndyContainer>
  );
}
