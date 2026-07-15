import { Waves } from "lucide-react";
import { Link } from "react-router-dom";
import { cn } from "../lib/format";
import { Card, SectionHeader, Sparkline } from "./ui";
import type { CompanyFlow, FlowStat, ThemeFlow } from "../types";

/** 资金流 section(Genny):theme 形态 = 主题净分 + 成员资金流榜(SegmentPage);
 * company 形态 = 个股资金面卡(CompanyPage)。数据缺位(null)时整体隐藏 —— 与
 * AltDataPanel 同约;资金类型细分标注"13F/空头为硬数据,其余语义推断"。 */

const fmtZ = (v: number | null | undefined) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}`;
const zTone = (v: number | null | undefined) =>
  v == null ? "text-brand-200" : v >= 0.8 ? "text-pos" : v <= -0.8 ? "text-neg" : "text-brand-800";

function StatTile({ label, hint, value, sub, tone }: {
  label: string;
  hint?: string;
  value: string;
  sub?: string;
  tone?: string;
}) {
  return (
    <div className="rounded-lg border border-line/60 bg-canvas px-3 py-2" title={hint}>
      <div className="text-2xs uppercase tracking-wide text-brand-200">{label}</div>
      <div className={cn("tnum mt-0.5 text-lg font-semibold leading-tight", tone ?? "text-brand-900")}>
        {value}
      </div>
      {sub && <div className="tnum text-2xs text-brand-200">{sub}</div>}
    </div>
  );
}

export function ThemeFlowSection({ flow }: { flow?: ThemeFlow | null }) {
  if (!flow) return null;
  return (
    <Card>
      <SectionHeader
        title="Theme Money Flow"
        titleCn="主题资金流"
        icon={<Waves size={15} strokeWidth={2} />}
        right={
          flow.net_score != null && (
            <span className={cn("tnum text-sm font-semibold", zTone(flow.net_score * 3))}>
              净分 {flow.net_score > 0 ? "+" : ""}
              {flow.net_score.toFixed(2)}
            </span>
          )
        }
      />
      <div className="grid gap-3 p-3 lg:grid-cols-[240px_1fr]">
        <div className="rounded-lg border border-line/60 bg-canvas px-3 py-2.5">
          <div
            className="text-2xs uppercase tracking-wide text-brand-200"
            title="主题成员的富途主力净流入 z ⊕ 美股成员 OBV 复合 ⊕ 空头持仓变动的加权净分 ∈ [-1,1]"
          >
            净流入分(近 60 日)
          </div>
          {flow.series.length > 1 ? (
            <Sparkline data={flow.series.map((p) => p.v)} width={190} height={40} className="mt-2" />
          ) : (
            <div className="mt-2 text-2xs text-brand-200">序列积累中(工人日频落库)</div>
          )}
        </div>
        <div className="overflow-x-auto">
          {flow.movers.length === 0 ? (
            <div className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-2xs text-brand-200">
              成员资金流数据尚未落库
            </div>
          ) : (
            <table className="w-full min-w-[460px] text-xs">
              <thead>
                <tr className="text-left text-2xs uppercase tracking-wide text-brand-200">
                  <th className="px-2 py-1 font-medium">成员</th>
                  <th className="px-2 py-1 font-medium" title="富途主力(超大单+大单)日度净流入 z">主力 z</th>
                  <th className="px-2 py-1 font-medium" title="OBV 量能累积 20日增量 z">OBV z</th>
                  <th className="px-2 py-1 font-medium" title="空头持仓 z(升 = 做空压力增)">空头 z</th>
                  <th className="px-2 py-1 font-medium">综合</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {flow.movers.map((m) => (
                  <tr key={m.company_id} className="transition-colors hover:bg-canvas">
                    <td className="max-w-[220px] px-2 py-1.5">
                      <Link to={`/genny/company/${m.company_id}`} className="block truncate text-brand-800 hover:text-accent-100">
                        {m.name} {m.ticker && <span className="tnum text-2xs text-brand-200">({m.ticker})</span>}
                      </Link>
                    </td>
                    <td className={cn("tnum px-2 py-1.5", zTone(m.futu_flow_z))}>{fmtZ(m.futu_flow_z)}</td>
                    <td className={cn("tnum px-2 py-1.5", zTone(m.obv_z))}>{fmtZ(m.obv_z)}</td>
                    <td className={cn("tnum px-2 py-1.5", zTone(m.short_interest_z == null ? null : -m.short_interest_z))}>
                      {fmtZ(m.short_interest_z)}
                    </td>
                    <td className={cn("tnum px-2 py-1.5 font-semibold", zTone((m.score ?? null) == null ? null : (m.score as number) * 3))}>
                      {m.score == null ? "—" : m.score.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </Card>
  );
}

export function CompanyFlowSection({ flow }: { flow?: CompanyFlow | null }) {
  if (!flow) return null;
  const s = (k: keyof CompanyFlow) => flow[k] as FlowStat | undefined;
  const si = s("short_interest");
  const dtc = s("days_to_cover");
  const mom = s("mom_63d");
  return (
    <Card>
      <SectionHeader
        title="Money Flow"
        titleCn="资金面 · 量价/空头/期权/机构"
        icon={<Waves size={15} strokeWidth={2} />}
        right={
          flow.futu_flow && flow.futu_flow.series.length > 1 ? (
            <span className="flex items-center gap-2 text-2xs text-brand-200">
              主力净流入(30日)
              <Sparkline data={flow.futu_flow.series.map((p) => p.v)} width={90} height={20} />
            </span>
          ) : undefined
        }
      />
      <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3 xl:grid-cols-4">
        {flow.futu_flow && (
          <StatTile label="主力资金 z" hint="富途主力(超大单+大单)日度净流入的 z-score"
            value={fmtZ(flow.futu_flow.z)} tone={zTone(flow.futu_flow.z)} />
        )}
        {s("obv_z") && (
          <StatTile label="OBV 量能 z" hint="OBV 累积 20日增量对 120日历史的 z:资金在买还是在卖"
            value={fmtZ(s("obv_z")!.value)} tone={zTone(s("obv_z")!.value)}
            sub={s("obv_z")!.period_end} />
        )}
        {mom && (
          <StatTile label="63日动量" hint="一季度价格动量(z 为对自身历史)"
            value={`${mom.value > 0 ? "+" : ""}${(mom.value * 100).toFixed(1)}%`}
            tone={zTone(mom.z)} sub={mom.z != null ? `z ${fmtZ(mom.z)}` : undefined} />
        )}
        {s("dollar_vol_z") && (
          <StatTile label="成交额 z" hint="20日均美元成交额 z:参与度/换手热度(方向不定)"
            value={fmtZ(s("dollar_vol_z")!.value)} sub={s("dollar_vol_z")!.period_end} />
        )}
        {s("pc_ratio") && (
          <StatTile label="期权 P/C" hint="近月期权 Put/Call 比;>1 防御对冲盘主导,极端值常为反指"
            value={s("pc_ratio")!.value.toFixed(2)} sub={s("pc_ratio")!.period_end} />
        )}
        {si && (
          <StatTile label="空头持仓" hint="FINRA 双周空头持仓(股数);z 升 = 做空压力增"
            value={Intl.NumberFormat("en", { notation: "compact" }).format(si.value)}
            tone={zTone(si.z == null ? null : -si.z)}
            sub={si.z != null ? `z ${fmtZ(si.z)} · ${si.period_end}` : si.period_end} />
        )}
        {dtc && (
          <StatTile label="回补天数 DTC" hint="空头持仓/日均成交量 = 全部空头平仓所需天数(挤压弹药)"
            value={`${dtc.value.toFixed(1)}天`} sub={dtc.period_end} />
        )}
        {s("inst_own_delta") && (
          <StatTile label="机构持仓 Δ" hint="13F 机构持仓市值季度环比(LO/HF 合计的低频硬数据)"
            value={`${s("inst_own_delta")!.value > 0 ? "+" : ""}${s("inst_own_delta")!.value.toFixed(1)}%`}
            tone={zTone(s("inst_own_delta")!.value / 3)} sub={s("inst_own_delta")!.period_end} />
        )}
      </div>
      <div className="border-t border-line/60 px-3 py-1.5 text-2xs text-brand-200">
        资金类型分布:13F 机构与空头持仓为托管硬数据;HF/LO/retail 细分来自语义抽取(见信号流
        flow_insight),非托管数据。
      </div>
    </Card>
  );
}
