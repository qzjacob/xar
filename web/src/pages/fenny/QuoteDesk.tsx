import { useState } from "react";
import {
  ChevronDown, Copy, Loader2, Lock, Play, Plus, Sliders, Trash2, X,
} from "lucide-react";
import { fennyApi } from "../../lib/fenny";
import { Card } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { cn } from "../../lib/format";
import type { Job } from "../../types-fenny";
import { InfoDot } from "./InfoDot";
import { QuoteResultDrawer, type QuoteResult } from "./QuoteResultDrawer";

// ── Fenny Quotation Desk — reference (Extramile) row-grid RFQ builder. Each row is one FCN
// quote; Submit prices every row via the live Monte-Carlo pricer. Column set + defaults mirror
// the reference exactly. Every visible cell either moves the priced number or is marked
// record-only / coming-soon — no silent no-ops. ──────────────────────────────────────────────

const TODAY = new Date().toISOString().slice(0, 10);
const FREQ: Record<number, string> = { 1: "monthly", 3: "quarterly", 6: "semiannual", 12: "annual" };
const CCYS = ["USD", "HKD", "EUR", "CNH", "JPY", "SGD", "AUD", "GBP"];

// product tabs — live keys map to backend variants; the rest render disabled (the pricer supports
// fcn / step-down(snowball) / phoenix; sharkfin/booster exist but aren't in this grid's scope).
const TABS: { key: string; label: string; variant?: string }[] = [
  { key: "fcn", label: "FCN", variant: "fcn" },
  { key: "snowball", label: "Step-down FCN", variant: "snowball" },
  { key: "phoenix", label: "Phoenix", variant: "phoenix" },
  { key: "sharkfin", label: "Sharkfin" }, { key: "ben", label: "BEN" }, { key: "dcn", label: "DCN" },
  { key: "eln", label: "ELN" }, { key: "wra", label: "WRA" }, { key: "scn", label: "Step-down SCN" },
  { key: "aq", label: "AQ" }, { key: "dq", label: "DQ" }, { key: "van", label: "VAN" },
  { key: "others", label: "Others" },
];

const SOLVE_OPTS: { key: SolveFor; label: string; live: boolean }[] = [
  { key: "coupon", label: "Coupon p.a. (%)", live: true },  // input strike → output coupon
  { key: "strike", label: "Strike (%)", live: true },       // input coupon → output strike
];

type SolveFor = "coupon" | "strike";

// rec = record-only: the cell is stored/labels the note but does NOT move the indicative price
// (single-curve pricing / scale-free barriers), marked so the desk isn't misled.
interface Col { key: string; label: string; cn: string; tip: string; w: string; solvable?: boolean; rec?: boolean }
// header meta (single source for labels + ⓘ tooltips + the blue-S solvable markers)
const COLS: Col[] = [
  { key: "currency", label: "Currency", cn: "币种", w: "w-[68px]", rec: true, tip: "票据币种;当前定价单曲线,USD 为主,改币种不改变指示报价(仅登记)。" },
  { key: "underlying", label: "Underlying", cn: "标的", w: "w-[184px]", tip: "挂钩 1–4 只股票;多只时由表现最差的一只决定结果(worst-of)。" },
  { key: "solveFor", label: "Solve For", cn: "求解目标", w: "w-[132px]", tip: "选择让引擎反解的参数:票息(支持)、发行价(支持)、行权价(即将支持)。其余为输入。" },
  { key: "strikePct", label: "Strike (%)", cn: "行权价", w: "w-[76px]", solvable: true, tip: "行权价,占标的最新市价%(100=当前价)。到期最差股票低于此价按跌幅承受下行。与票息双向互解:Solve For=Coupon 时为输入,=Strike 时由引擎反解。" },
  { key: "koType", label: "KO Type", cn: "敲出观察", w: "w-[116px]", tip: "敲出(提前收回)观察方式。Period End = 每个观察日期末观察(离散);当前仅支持此方式。" },
  { key: "koPct", label: "KO (%)", cn: "敲出线", w: "w-[72px]", tip: "敲出/提前收回线,占初始%(100=回到初始即收回)。" },
  { key: "couponPct", label: "Coupon p.a. (%)", cn: "年化票息", w: "w-[92px]", solvable: true, tip: "年化票息%。Solve For=Coupon 时由引擎反解;否则为输入。" },
  { key: "grossMarginPct", label: "Gross Margin (%)", cn: "毛利率", w: "w-[92px]", tip: "券商毛利率%。与发行价一起决定引擎定价目标:PV=(发行价−毛利)/100。" },
  { key: "notePricePct", label: "Note Price (%)", cn: "发行价", w: "w-[88px]", tip: "发行/认购价,占面值%(99=1 点折价)。与毛利一起定出引擎定价目标 PV=(发行价−毛利)/100。" },
  { key: "tenorM", label: "Tenor (m)", cn: "期限(月)", w: "w-[72px]", tip: "票据期限,月。到期日 = 定价日 + 期限。" },
  { key: "barrierType", label: "Barrier Type", cn: "敲入类型", w: "w-[116px]", tip: "敲入(下行保护)观察方式:NONE=无独立敲入线(按行权价结算);European=仅到期观察;American=存续期内每日观察。" },
  { key: "kiPct", label: "KI (%)", cn: "敲入线", w: "w-[72px]", tip: "敲入线,占初始%(如 65)。Barrier Type=NONE 时不适用。" },
  { key: "obsFreqM", label: "Obs. Freq (m)", cn: "观察频率(月)", w: "w-[104px]", tip: "敲出/派息观察频率,月。1=每月,3=每季,6=每半年,12=每年。" },
  { key: "effOffset", label: "Eff. Date Offset", cn: "起息偏移(日)", w: "w-[112px]", rec: true, tip: "成交日到定价(起息)日的营业日间隔;因存续期从定价日起算、障碍为相对水平,改此值不改变指示报价(仅登记)。" },
  { key: "tags", label: "Tags", cn: "标签", w: "w-[104px]", rec: true, tip: "自定义标签,仅登记,不影响定价。" },
];

interface Row {
  id: number;
  currency: string;
  tickers: string[];
  draft: string;
  solveFor: SolveFor;
  strikePct: number;
  koType: "period_end" | "continuous";
  koPct: number;
  couponPct: number;
  grossMarginPct: number;
  notePricePct: number;
  tenorM: number;
  barrierType: "NONE" | "european" | "american";
  kiPct: string;
  obsFreqM: number;
  effOffset: number;
  tags: string[];
  status: "idle" | "pricing" | "done" | "error";
  stage?: string;
  result?: QuoteResult | null;
  error?: string | null;
  open?: boolean;
}

let _rid = 1;
function makeRow(seed?: Partial<Row>): Row {
  // id LAST so a seed (e.g. from dupRow spreading the source row) can never override the fresh
  // id — a shared id would collide React keys and make patch()/delRow() hit both rows.
  return {
    // default to a PROTECTED FCN (European KI 65%) — the unprotected Barrier-NONE (KI at the strike)
    // prices to a shockingly high coupon; a 65% protection barrier is the realistic FCN default.
    currency: "USD", tickers: [], draft: "", solveFor: "coupon", strikePct: 100,
    koType: "period_end", koPct: 100, couponPct: 12, grossMarginPct: 0.7, notePricePct: 99,
    tenorM: 6, barrierType: "european", kiPct: "65", obsFreqM: 1, effOffset: 10, tags: [],
    status: "idle", ...seed, id: _rid++,
  };
}

function isoPlusMonths(base: string, months: number): string {
  const d = new Date(base + "T00:00:00Z");
  d.setUTCMonth(d.getUTCMonth() + months);
  return d.toISOString().slice(0, 10);
}
function addBizDays(base: string, n: number): string {
  const d = new Date(base + "T00:00:00Z");
  let added = 0;
  while (added < n) {
    d.setUTCDate(d.getUTCDate() + 1);
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) added++;
  }
  return d.toISOString().slice(0, 10);
}

interface Assumptions { notional: number; rate: number; rho: number; atmVol: number; live: boolean }

// row → live pricer. Uses build_termsheet then overrides strike + knock_in (NONE) on the full
// TermSheet so Strike% and Barrier=NONE are honest without touching the preset schema.
async function priceOne(
  row: Row, variant: string, a: Assumptions, onStage: (s: string) => void,
): Promise<QuoteResult> {
  const tickers = row.tickers.slice(0, 4);
  if (!tickers.length) throw new Error("先填至少一个标的");
  const strikeDate = addBizDays(TODAY, row.effOffset);
  const maturity = isoPlusMonths(strikeDate, row.tenorM);
  const strikeFrac = row.strikePct / 100;
  // Downside is priced only via the knock-in. Barrier NONE = no *separate* protection barrier,
  // so the downside starts at the strike, observed at maturity (European KI at the strike) — an
  // unprotected FCN. European/American use the explicit KI level (a protection buffer below strike).
  const kiFrac = row.barrierType === "NONE"
    ? strikeFrac
    : row.kiPct === "" ? 0.65 : Number(row.kiPct) / 100;
  const kiStyle = row.barrierType === "american" ? "american" : "european";
  const preset = {
    variant, tickers, notional: a.notional, currency: row.currency,
    trade_date: TODAY, strike_date: strikeDate, maturity,
    coupon_rate: row.solveFor === "coupon" ? null : row.couponPct / 100,
    frequency: FREQ[row.obsFreqM] ?? "quarterly",
    autocall_barrier: row.koPct / 100, ki_barrier: kiFrac,
    coupon_barrier: 0.7, memory: variant === "phoenix", ki_style: kiStyle,
  };
  const ts = (await fennyApi.presetTermsheet(preset)) as Record<string, unknown>;
  ts.underlyings = ((ts.underlyings as Record<string, unknown>[]) ?? []).map((u) => ({
    ...u, strike: strikeFrac,
  }));
  ts.knock_in = { barrier: kiFrac, style: kiStyle, settlement: "cash" };
  const assumeAsset = (t: string) => ({
    ticker: t, spot: 100, atm_vol: a.atmVol, skew_slope: -0.4, skew_curv: 0.3,
  });
  let market: Record<string, unknown>;
  let dataNote: string | undefined;
  if (a.live) {
    // real spot + realized vol + correlation from FMP (no manual input). Merge PER NAME: keep every
    // resolved name's real vol, fill ONLY the names FMP couldn't price with the stated assumption.
    // (Discarding the whole basket on one miss silently mispriced worst-of — a lower flat vol read
    // as a LOWER coupon for MORE names, the exact reversal a partial resolve used to produce.)
    onStage("fetching real market data");
    const rm = await fennyApi.resolveMarket(tickers);
    const rmAssets = (rm.assets as Record<string, unknown>[]) ?? [];
    const byTicker = new Map(rmAssets.map((x) => [String(x.ticker), x]));
    const missing = tickers.filter((t) => !byTicker.has(t));
    const assets = tickers.map((t) => byTicker.get(t) ?? assumeAsset(t));
    market = {
      source: "manual", rate: (rm.rate as number) ?? a.rate, rho: a.rho, assets,
      // the correlation matrix from resolve_market only spans resolved names; use it only when the
      // whole basket resolved, otherwise fall back to the uniform ρ so the matrix size stays valid.
      correlation: missing.length === 0 ? ((rm.correlation as number[][] | null) ?? undefined) : undefined,
    };
    if (missing.length) {
      dataNote = `${missing.join("、")} 无实时行情，已用假设波动率 ${(a.atmVol * 100).toFixed(0)}%（其余为实时数据）`;
    }
  } else {
    market = { source: "manual", rate: a.rate, rho: a.rho, assets: tickers.map(assumeAsset) };
  }
  const onP = (j: Job) => onStage(j.stage || "pricing");
  // both note_price + gross_margin define the reoffer target PV=(note_price-margin)/100 that the
  // solve (of coupon OR strike) prices to. coupon mode: ts.coupon.rate=null → solve coupon.
  // strike mode: ts.coupon.rate=couponPct → solve the fair strike (couple KI to strike for NONE).
  const body: Record<string, unknown> = {
    termsheet: ts, market, mc: { n_paths: 40_000 }, include_greeks: true, include_scenario: true,
    gross_margin_pct: row.grossMarginPct, note_price_pct: row.notePricePct,
  };
  if (row.solveFor === "strike") {
    body.solve_for = "strike";
    body.couple_ki_to_strike = row.barrierType === "NONE";
  }
  const res = (await fennyApi.solve(body, onP)) as unknown as QuoteResult;
  return dataNote ? { ...res, data_note: dataNote } : res;
}

const INPUT =
  "w-full rounded-md border border-line bg-surface-2 px-1.5 py-1 text-xs text-brand-900 outline-none focus:border-accent-500";
const NUM = INPUT + " tnum text-right";

function NumCell({ value, onChange, step, disabled, placeholder }: {
  value: number | string; onChange: (v: string) => void; step?: string; disabled?: boolean; placeholder?: string;
}) {
  return (
    <input type="number" step={step} value={value} disabled={disabled} placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className={cn(NUM, disabled && "cursor-not-allowed opacity-40")} />
  );
}

export function QuoteDesk() {
  const [audience, setAudience] = useState<"pi" | "nonpi">("pi");
  const [custodian, setCustodian] = useState("GSL EAM HK");
  const [tab, setTab] = useState("fcn");
  const [rows, setRows] = useState<Row[]>([makeRow()]);
  const [remark, setRemark] = useState("");
  const [showAssume, setShowAssume] = useState(false);
  // default to LIVE (real spot + realized vol from FMP) so quotes reflect real market vol, not a
  // flat assumption — the flat-30% assumption was the main driver of unrealistically-large coupons.
  const [assume, setAssume] = useState<Assumptions>({
    notional: 1_000_000, rate: 0.045, rho: 0.5, atmVol: 0.30, live: true,
  });
  const [running, setRunning] = useState(false);

  const variant = TABS.find((t) => t.key === tab)?.variant ?? "fcn";
  const liveTab = !!TABS.find((t) => t.key === tab)?.variant;

  // editing any PRICE-AFFECTING cell invalidates a shown result (a disabled output cell must
  // never display a number priced from different inputs); internal writes (stage/status/result/
  // open/draft/tags) don't reset.
  const PRICE_KEYS = new Set<keyof Row>([
    "currency", "tickers", "solveFor", "strikePct", "koType", "koPct", "couponPct",
    "grossMarginPct", "notePricePct", "tenorM", "barrierType", "kiPct", "obsFreqM", "effOffset",
  ]);
  function patch(id: number, p: Partial<Row>) {
    const invalidates = Object.keys(p).some((k) => PRICE_KEYS.has(k as keyof Row));
    const reset = invalidates ? { status: "idle" as const, result: null, error: null, open: false } : {};
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...p, ...reset } : r)));
  }
  function addRow() { setRows((rs) => [...rs, makeRow()]); }
  function dupRow(id: number) {
    setRows((rs) => {
      const src = rs.find((r) => r.id === id);
      if (!src) return rs;
      return [...rs, makeRow({ ...src, tickers: [...src.tickers], tags: [...src.tags],
        status: "idle", result: null, error: null, open: false, draft: "" })];
    });
  }
  function delRow(id: number) { setRows((rs) => (rs.length <= 1 ? rs : rs.filter((r) => r.id !== id))); }
  const RESET = { status: "idle" as const, result: null, error: null, open: false };
  function addTicker(id: number, raw: string) {
    const t = raw.trim().toUpperCase().replace(/,$/, "");
    if (!t) return;
    setRows((rs) => rs.map((r) => {
      if (r.id !== id) return r;
      if (r.tickers.includes(t) || r.tickers.length >= 4) return { ...r, draft: "" };
      return { ...r, tickers: [...r.tickers, t], draft: "", ...RESET };
    }));
  }
  function rmTicker(id: number, t: string) {
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, tickers: r.tickers.filter((x) => x !== t), ...RESET } : r)));
  }

  async function submitAll() {
    if (!liveTab) return;
    setRunning(true);
    const targets = rows.filter((r) => r.tickers.length > 0);
    setRows((rs) => rs.map((r) => (r.tickers.length ? { ...r, status: "pricing", stage: "pricing", error: null } : r)));
    // price rows with limited concurrency (3 at a time)
    const queue = [...targets];
    async function worker() {
      for (;;) {
        const row = queue.shift();
        if (!row) return;
        try {
          const res = await priceOne(row, variant, assume, (s) => patch(row.id, { stage: s }));
          patch(row.id, { status: "done", result: res, error: null });
        } catch (e) {
          patch(row.id, { status: "error", error: e instanceof Error ? e.message : String(e) });
        }
      }
    }
    await Promise.all([worker(), worker(), worker()]);
    setRunning(false);
  }

  const priced = rows.filter((r) => r.status === "done").length;
  const errored = rows.filter((r) => r.status === "error").length;

  return (
    <div className="p-4">
      <Card>
        {/* ── header: audience toggle + custodian + scenario ─────────────────────── */}
        <div className="border-b border-line px-4 py-3">
          <div className="mb-3 flex items-center gap-3">
            <h2 className="text-sm font-semibold text-brand-900">Add New Quotation</h2>
            <span className="text-2xs text-brand-200">新建报价</span>
            <div className="ml-2 inline-flex rounded-lg border border-line p-0.5">
              <button type="button" onClick={() => setAudience("pi")}
                className={cn("rounded-md px-3 py-1 text-2xs font-semibold", audience === "pi" ? "bg-accent-600 text-white" : "text-brand-500")}>
                Quote for PI · 专业投资者
              </button>
              <button type="button" disabled title="仅专业投资者可报价"
                className="inline-flex cursor-not-allowed items-center gap-1 rounded-md px-3 py-1 text-2xs font-semibold text-brand-200">
                <Lock size={10} /> Non-PI
              </button>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-2xs text-brand-500">
            <span>Custodian <span className="text-neg">*</span> 托管方</span>
            <input value={custodian} onChange={(e) => setCustodian(e.target.value)}
              className="w-52 rounded-md border border-line bg-surface-2 px-2 py-1 text-xs text-brand-900 outline-none focus:border-accent-500" />
            <span className="ml-3">Scenario 情景</span>
            <Badge className="bg-accent-600/15 text-accent-100">General</Badge>
            <span className="ml-2 inline-flex cursor-not-allowed items-center gap-1 text-brand-200" title="尚未开放">
              <Lock size={10} /> Combo Generator
            </span>
          </div>
        </div>

        {/* ── product-type tabs ──────────────────────────────────────────────────── */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-2">
          {TABS.map((t) => {
            const live = !!t.variant;
            return (
              <button key={t.key} type="button" disabled={!live} onClick={() => live && setTab(t.key)}
                title={live ? undefined : "该结构暂未接入定价引擎"}
                className={cn("relative pb-1 text-xs font-medium transition-colors",
                  tab === t.key ? "text-accent-100" : live ? "text-brand-500 hover:text-brand-900" : "cursor-not-allowed text-brand-200")}>
                {t.label}
                {tab === t.key && <span className="absolute inset-x-0 -bottom-[9px] h-0.5 rounded bg-accent-500" />}
              </button>
            );
          })}
        </div>

        {/* ── pricing basis (our one deviation: the pricer needs a vol assumption) ─── */}
        <div className="border-b border-line px-4 py-2">
          <button type="button" onClick={() => setShowAssume((s) => !s)}
            className="flex items-center gap-1.5 text-2xs text-brand-500 hover:text-accent-100">
            <Sliders size={12} />
            定价假设 Pricing basis
            <span className="text-brand-200">
              · {assume.live ? "实时行情 Live" : `指示性:ATM 波动 ${(assume.atmVol * 100).toFixed(0)}%`} · 利率 {(assume.rate * 100).toFixed(1)}% · ρ {assume.rho}
            </span>
            <ChevronDown size={12} className={cn("transition-transform", showAssume && "rotate-180")} />
          </button>
          {showAssume && (
            <div className="mt-2 flex flex-wrap items-end gap-3">
              <label className="text-2xs text-brand-500">
                市场 <InfoDot tip="指示性 = 用统一 ATM 波动率假设定价(无需实时行情 key);Live = 拉取实时现价与波动率(需 MASSIVE_API_KEY)。" />
                <div className="mt-0.5 inline-flex rounded-md border border-line p-0.5">
                  <button type="button" onClick={() => setAssume((a) => ({ ...a, live: false }))}
                    className={cn("rounded px-2 py-0.5 text-2xs", !assume.live ? "bg-accent-600 text-white" : "text-brand-500")}>指示性</button>
                  <button type="button" onClick={() => setAssume((a) => ({ ...a, live: true }))}
                    className={cn("rounded px-2 py-0.5 text-2xs", assume.live ? "bg-accent-600 text-white" : "text-brand-500")}>实时 Live</button>
                </div>
              </label>
              <label className="text-2xs text-brand-500">ATM 波动率
                <input type="number" step="0.01" value={assume.atmVol} disabled={assume.live}
                  onChange={(e) => setAssume((a) => ({ ...a, atmVol: +e.target.value }))}
                  className={cn(NUM, "mt-0.5 w-20", assume.live && "opacity-40")} />
              </label>
              <label className="text-2xs text-brand-500">无风险利率
                <input type="number" step="0.005" value={assume.rate}
                  onChange={(e) => setAssume((a) => ({ ...a, rate: +e.target.value }))} className={cn(NUM, "mt-0.5 w-20")} />
              </label>
              <label className="text-2xs text-brand-500">相关性 ρ
                <input type="number" step="0.05" value={assume.rho}
                  onChange={(e) => setAssume((a) => ({ ...a, rho: +e.target.value }))} className={cn(NUM, "mt-0.5 w-20")} />
              </label>
              <label className="text-2xs text-brand-500">名义本金
                <input type="number" step="100000" value={assume.notional}
                  onChange={(e) => setAssume((a) => ({ ...a, notional: +e.target.value }))} className={cn(NUM, "mt-0.5 w-28")} />
              </label>
            </div>
          )}
        </div>

        {/* ── the grid ───────────────────────────────────────────────────────────── */}
        <div className="overflow-x-auto">
          <div className="min-w-max">
            {/* header row */}
            <div className="flex items-stretch gap-1.5 border-b border-line bg-surface-2/60 px-3 py-2 text-2xs font-medium uppercase tracking-wide text-brand-200">
              <div className="flex w-[52px] shrink-0 items-center gap-1">
                <button type="button" onClick={addRow} className="text-pos hover:opacity-80" title="新增一行"><Plus size={15} /></button>
                <span className="text-[10px] normal-case">Import</span>
              </div>
              <div className="w-7 shrink-0 text-center">No.</div>
              {COLS.map((c) => (
                <div key={c.key} className={cn(c.w, "shrink-0")}>
                  <div className="flex items-center gap-0.5 leading-tight">
                    <span className="truncate normal-case text-brand-500">{c.label}</span>
                    {c.solvable && <span className="grid h-3 w-3 place-items-center rounded-full bg-accent-600/30 text-[8px] font-bold text-accent-100" title="可求解 Solvable">S</span>}
                    {c.rec && <span className="rounded bg-surface-2/50 px-1 text-[8px] font-normal normal-case text-brand-500" title="仅登记 · 不影响指示报价">记</span>}
                    <InfoDot tip={c.tip} down />
                  </div>
                  <div className="truncate text-[9px] normal-case text-brand-200">{c.cn}</div>
                </div>
              ))}
            </div>

            {/* data rows */}
            {rows.map((r, idx) => (
              <div key={r.id} className="border-b border-line">
                <div className="flex items-center gap-1.5 px-3 py-1.5">
                  {/* controls */}
                  <div className="flex w-[52px] shrink-0 items-center gap-1">
                    <button type="button" onClick={() => delRow(r.id)} disabled={rows.length <= 1}
                      className="text-neg hover:opacity-80 disabled:opacity-30" title="删除该行"><Trash2 size={13} /></button>
                    <button type="button" onClick={() => dupRow(r.id)} className="text-brand-200 hover:text-brand-900" title="复制该行"><Copy size={12} /></button>
                  </div>
                  <div className="w-7 shrink-0 text-center text-xs text-brand-500">{idx + 1}</div>
                  {/* currency */}
                  <div className={cn(COLS[0].w, "shrink-0")}>
                    <select value={r.currency} onChange={(e) => patch(r.id, { currency: e.target.value })} className={INPUT}>
                      {CCYS.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </div>
                  {/* underlying chips */}
                  <div className={cn(COLS[1].w, "shrink-0")}>
                    <div className="flex flex-wrap items-center gap-1 rounded-md border border-line bg-surface-2 px-1 py-0.5">
                      {r.tickers.map((t) => (
                        <span key={t} className="inline-flex items-center gap-0.5 rounded bg-accent-600/20 px-1 text-[10px] text-accent-100">
                          {t}<button type="button" onClick={() => rmTicker(r.id, t)}><X size={9} /></button>
                        </span>
                      ))}
                      {r.tickers.length < 4 && (
                        <input value={r.draft} placeholder={r.tickers.length ? "+代码" : "1~4 只 · TICKER"}
                          onChange={(e) => patch(r.id, { draft: e.target.value })}
                          onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addTicker(r.id, r.draft); } }}
                          onBlur={() => addTicker(r.id, r.draft)}
                          className="min-w-[52px] flex-1 bg-transparent text-xs uppercase text-brand-900 outline-none placeholder:normal-case placeholder:text-brand-200" />
                      )}
                    </div>
                  </div>
                  {/* solve for */}
                  <div className={cn(COLS[2].w, "shrink-0")}>
                    <select value={r.solveFor} onChange={(e) => patch(r.id, { solveFor: e.target.value as SolveFor })} className={INPUT}>
                      {SOLVE_OPTS.map((o) => <option key={o.key} value={o.key} disabled={!o.live}>{o.label}{o.live ? "" : " (即将)"}</option>)}
                    </select>
                  </div>
                  {/* strike % — input when solving coupon; solved output when solving strike */}
                  <div className={cn(COLS[3].w, "shrink-0")}>
                    <NumCell value={r.solveFor === "strike" && r.result?.solved_strike != null ? +((r.result.solved_strike * 100).toFixed(1)) : r.strikePct}
                      step="1" onChange={(v) => patch(r.id, { strikePct: +v })} disabled={r.solveFor === "strike"} />
                  </div>
                  {/* KO type */}
                  <div className={cn(COLS[4].w, "shrink-0")}>
                    <select value={r.koType} onChange={(e) => patch(r.id, { koType: e.target.value as Row["koType"] })} className={INPUT}>
                      <option value="period_end">Period End</option>
                      <option value="continuous" disabled>Continuous (即将)</option>
                    </select>
                  </div>
                  {/* KO % */}
                  <div className={cn(COLS[5].w, "shrink-0")}><NumCell value={r.koPct} step="1" onChange={(v) => patch(r.id, { koPct: +v })} /></div>
                  {/* coupon */}
                  <div className={cn(COLS[6].w, "shrink-0")}>
                    <NumCell value={r.solveFor === "coupon" && r.result ? +(((r.result.coupon_rate ?? 0) * 100).toFixed(2)) : r.couponPct}
                      step="0.25" onChange={(v) => patch(r.id, { couponPct: +v })} disabled={r.solveFor === "coupon"} />
                  </div>
                  {/* gross margin */}
                  <div className={cn(COLS[7].w, "shrink-0")}><NumCell value={r.grossMarginPct} step="0.05" onChange={(v) => patch(r.id, { grossMarginPct: +v })} /></div>
                  {/* note price — always an input (with gross margin, sets the reoffer target) */}
                  <div className={cn(COLS[8].w, "shrink-0")}>
                    <NumCell value={r.notePricePct} step="0.5" onChange={(v) => patch(r.id, { notePricePct: +v })} />
                  </div>
                  {/* tenor */}
                  <div className={cn(COLS[9].w, "shrink-0")}><NumCell value={r.tenorM} step="1" onChange={(v) => patch(r.id, { tenorM: Math.max(1, +v) })} /></div>
                  {/* barrier type */}
                  <div className={cn(COLS[10].w, "shrink-0")}>
                    <select value={r.barrierType} onChange={(e) => patch(r.id, { barrierType: e.target.value as Row["barrierType"] })} className={INPUT}>
                      <option value="NONE">NONE</option>
                      <option value="european">European</option>
                      <option value="american">American</option>
                    </select>
                  </div>
                  {/* KI % */}
                  <div className={cn(COLS[11].w, "shrink-0")}>
                    <NumCell value={r.kiPct} step="1" placeholder={r.barrierType === "NONE" ? "—" : "65"}
                      onChange={(v) => patch(r.id, { kiPct: v })} disabled={r.barrierType === "NONE"} />
                  </div>
                  {/* obs freq */}
                  <div className={cn(COLS[12].w, "shrink-0")}>
                    <select value={r.obsFreqM} onChange={(e) => patch(r.id, { obsFreqM: +e.target.value })} className={INPUT}>
                      <option value={1}>1 · 每月</option><option value={3}>3 · 每季</option>
                      <option value={6}>6 · 半年</option><option value={12}>12 · 每年</option>
                    </select>
                  </div>
                  {/* eff date offset */}
                  <div className={cn(COLS[13].w, "shrink-0")}>
                    <select value={r.effOffset} onChange={(e) => patch(r.id, { effOffset: +e.target.value })} className={INPUT}>
                      {[2, 3, 5, 7, 10, 15].map((d) => <option key={d} value={d}>{d}</option>)}
                    </select>
                  </div>
                  {/* tags */}
                  <div className={cn(COLS[14].w, "shrink-0")}>
                    <input value={r.tags.join(",")} placeholder="标签"
                      onChange={(e) => patch(r.id, { tags: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} className={INPUT} />
                  </div>
                </div>

                {/* per-row result strip */}
                {r.status !== "idle" && (
                  <div className="bg-surface-2/40 px-3 pb-2">
                    {r.status === "pricing" && (
                      <div className="flex items-center gap-1.5 py-1 text-2xs text-brand-500">
                        <Loader2 size={12} className="animate-spin" /> 计算中… <span className="text-accent-100">{r.stage}</span>
                      </div>
                    )}
                    {r.status === "error" && <div className="py-1 text-2xs text-neg">出错: {r.error}</div>}
                    {r.status === "done" && r.result && (
                      <>
                        <button type="button" onClick={() => patch(r.id, { open: !r.open })}
                          className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 py-1.5 text-left text-2xs">
                          <ChevronDown size={13} className={cn("text-brand-500 transition-transform", r.open && "rotate-180")} />
                          <ResStat label={r.solveFor === "strike" ? "行权价" : "票息"}
                            value={r.solveFor === "strike" ? ((r.result.solved_strike ?? 0) * 100).toFixed(1) + "%" : ((r.result.coupon_rate ?? 0) * 100).toFixed(2) + "% p.a."} tone="pos" />
                          <ResStat label="公平价值" value={r.result.pricing.price_pct.toFixed(2) + "%"} />
                          <ResStat label="提前收回" value={(r.result.pricing.prob_autocall * 100).toFixed(1) + "%"} tone="pos" />
                          <ResStat label={r.barrierType === "NONE" ? "本金亏损" : "触及保护线"} value={(r.result.pricing.prob_knock_in * 100).toFixed(1) + "%"} tone="warn" />
                          <ResStat label="预计存续" value={r.result.pricing.expected_life.toFixed(1) + " 年"} />
                          {r.result.pricing.expected_life < 0.5 && (
                            <span className="text-[10px] text-warn-100" title="存续期很短:年化票息只在这段时间内派发,p.a. 数字会显得偏高">
                              ⚠ 存续短·年化偏高
                            </span>
                          )}
                        </button>
                        {r.solveFor === "coupon" && r.result.infeasible && (
                          <div className="py-0.5 text-[10px] text-neg">⚠ 结构在此发行价/毛利下不可行(公平票息≤0),已置 0</div>
                        )}
                        {r.solveFor === "strike" && r.result.strike_bracketed === false && (
                          <div className="py-0.5 text-[10px] text-warn-100">⚠ 求解行权价触及区间边界 [50%,120%],结果为夹逼值</div>
                        )}
                        {r.result.data_note && (
                          <div className="py-0.5 text-[10px] text-warn-100">⚠ {r.result.data_note}</div>
                        )}
                        {r.open && <QuoteResultDrawer result={r.result} ccy={r.currency} variant={variant} barrierNone={r.barrierType === "NONE"} />}
                      </>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* ── footer ─────────────────────────────────────────────────────────────── */}
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative">
            <input value={remark} maxLength={100} placeholder="备注 Remark here"
              onChange={(e) => setRemark(e.target.value)}
              className="w-72 rounded-md border border-line bg-surface-2 px-2 py-1.5 text-xs text-brand-900 outline-none focus:border-accent-500" />
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-brand-200">{remark.length}/100</span>
          </div>
          <button type="button" disabled title="模板保存即将开放"
            className="cursor-not-allowed rounded-md border border-line px-3 py-1.5 text-xs text-brand-200">Save as Template</button>
          <button type="button" onClick={submitAll} disabled={running || !liveTab}
            className="inline-flex items-center gap-1.5 rounded-md bg-accent-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-accent-500 disabled:opacity-50">
            {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            Submit · 报价
          </button>
          {(priced > 0 || errored > 0) && (
            <Badge className="bg-surface-2 text-brand-500">{priced} 已定价{errored ? ` · ${errored} 出错` : ""}</Badge>
          )}
          {!liveTab && <span className="text-2xs text-brand-200">该结构暂未接入定价引擎</span>}
          <span className="ml-auto cursor-not-allowed text-xs text-brand-200" title="模板库即将开放">See more Templates</span>
        </div>
      </Card>
    </div>
  );
}

function ResStat({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" | "warn" }) {
  const t = tone === "pos" ? "text-pos" : tone === "neg" ? "text-neg" : tone === "warn" ? "text-warn-100" : "text-brand-900";
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="text-brand-200">{label}</span>
      <span className={cn("font-semibold tnum", t)}>{value}</span>
    </span>
  );
}
