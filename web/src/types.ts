// ===========================================================================
// XAR front-end domain model — the single source of truth every component and
// the mock/API layer share. Keep this in sync with the backend ontology
// (catalyst taxonomy, chain segments, permission/source tags).
// ===========================================================================

import type {
  CalendarRow,
  CompanyCoverage,
  EstimateRow,
  HoldingRow,
  Thesis,
} from "./types-thesis";
import type { AltData } from "./types-alt";

export type Market = "ALL" | "US" | "CN" | "JP" | "KR" | "HK";
export const MARKETS: Market[] = ["ALL", "US", "CN", "JP", "KR", "HK"];

export type Period = "1W" | "1M" | "3M" | "YTD";
export const PERIODS: Period[] = ["1W", "1M", "3M", "YTD"];

export type Polarity = "positive" | "negative" | "neutral";

/** Where a signal came from (mirrors XAR ingestion sources). */
export type SignalSource =
  | "filing"
  | "wechat"
  | "prediction_market"
  | "estimate"
  | "insider"
  | "news";

/** The 10-type catalyst taxonomy (mirrors xar.ontology.catalysts). */
export type CatalystType =
  | "capex_guidance"
  | "order"
  | "qualification"
  | "product_ramp"
  | "accelerator_launch"
  | "capacity_expansion"
  | "supply_constraint"
  | "earnings"
  | "equity_investment"
  | "tech_substitution"
  // whole-economy expansion (P0/P1) — must mirror ontology/catalysts.py CATALYST_TYPES
  | "guidance_change"
  | "mna"
  | "partnership"
  | "contract_win"
  | "pricing_change"
  | "management_change"
  | "buyback"
  | "dividend"
  | "regulatory_action"
  | "litigation"
  | "index_inclusion"
  | "short_report"
  | "macro_print"
  | "stock_split"
  | "secondary_offering";

/** Segment-level cycle phase along the chain. */
export type SegmentRegime =
  | "accelerating"
  | "expansion"
  | "peaking"
  | "cooling"
  | "trough";

export interface Theme {
  id: string;
  name: string;
  nameCn: string;
  active: boolean;
  kind?: "chain" | "cycle"; // organizing axis: supply-chain tier vs economic-cycle position
  segmentCount?: number;
}

/** Economic-cycle position of a segment/company (consumer cycle themes). */
export interface CycleInfo {
  position: string; // early_cycle | mid_cycle | late_cycle | defensive | counter_cyclical
  cyclicality: string; // cyclical | defensive | counter_cyclical
  sensitivity: number; // beta hint
  label: string; // EN label
  labelCn: string; // CN label
  short: string; // EC | MC | LC | DEF | CC
  rank: number; // 1 (early) .. 5 (counter-cyclical)
  note?: string;
  noteCn?: string;
}

export interface Segment {
  id: string;
  name: string; // English label
  nameCn: string; // Chinese label
  tier: number; // chain themes: chain order (1=upstream); cycle themes: cycle rank (1=early..5=counter)
  axis?: "chain" | "cycle"; // which axis `tier` encodes
  cycle?: CycleInfo | null; // present for cycle-theme segments
  alpha: number; // 0..100 opportunity score
  momentum: number; // -100..100
  changeW: number; // Δ 1W, %
  changeM: number; // Δ 1M, %
  valuationPctile: number; // 0..100 (higher = richer / more expensive)
  crowding: number; // 0..100 (higher = more crowded)
  supplyTightness: number; // 0..100 (higher = tighter supply)
  earningsRevision: number; // -100..100 (consensus revision breadth)
  companies: number; // # covered names
  regime: SegmentRegime;
  spark: number[]; // small price/score series
  markets: Market[]; // markets the segment spans
  note?: string; // one-line thesis
  thesisCn?: string; // AI-adoption-wave thesis (why this segment benefits when it does)
}

export interface Company {
  id: string;
  ticker: string;
  name: string;
  nameCn?: string;
  segmentId: string;
  market: Market; // primary listing
  marketCap: number; // USD bn
  priceChange: number; // Δ price %, selected period
  revGrowth: number; // YoY revenue growth %
  grossMargin: number; // %
  estRevision: number; // -100..100 recent consensus revision
  conviction: number; // 1..5 internal conviction
  watched: boolean;
  signals: CatalystType[]; // recent signal badges
  spark: number[];
  role: string; // chain role label
}

export interface Signal {
  id: string;
  type: CatalystType;
  polarity: Polarity;
  source: SignalSource;
  companyId?: string;
  ticker?: string;
  segmentId: string;
  title: string;
  magnitude?: string;
  ts: string; // ISO timestamp
  confidence: number; // 0..1
}

export interface Catalyst {
  id: string;
  date: string; // ISO date (YYYY-MM-DD)
  type: CatalystType;
  polarity: Polarity;
  title: string;
  ticker?: string;
  segmentId?: string;
  importance: 1 | 2 | 3; // 3 = high
}

export interface RegimeDriver {
  label: string;
  polarity: Polarity;
}

export interface Regime {
  label: string;
  labelCn: string;
  phase: SegmentRegime;
  score: number; // 0..100 composite cycle score
  trend: number; // Δ vs last period
  breadth: number; // 0..100 % of segments expanding
  drivers: RegimeDriver[];
  updatedAt: string; // ISO
}

export interface Opportunity {
  id: string;
  title: string;
  detail: string;
  segmentId?: string;
  ticker?: string;
  score: number; // 0..100 conviction
}

export interface RiskItem {
  id: string;
  title: string;
  detail: string;
  severity: "high" | "medium" | "low";
}

export interface ActionItem {
  id: string;
  label: string;
  kind: "review" | "add" | "rerate" | "trim";
  ticker?: string;
  done: boolean;
}

export interface Decision {
  houseView: string;
  houseViewCn: string;
  opportunities: Opportunity[];
  risks: RiskItem[];
  actions: ActionItem[];
}

export interface CoverageMeta {
  themes: Theme[];
  companyCount: number;
  segmentCount: number;
  updatedAt: string; // ISO
}

// --- composite + detail payloads (from /api/ui/*) --------------------------
export interface Overview {
  regime: Regime;
  segments: Segment[];
  decision: Decision;
  coverage: CoverageMeta;
}

export interface PriceBar {
  d: string;
  close: number;
}

export interface FundamentalRow {
  metric: string;
  value: number;
  unit: string;
}

export interface SupplyEdge {
  id: string;
  name: string;
  rel: string;
  confidence: number;
}

export interface SupplyChain {
  suppliers: SupplyEdge[];
  customers: SupplyEdge[];
  invests_in: SupplyEdge[];
  tech_routes: SupplyEdge[];
  single_source_risks: { src: string | null; dst: string | null }[];
}

export interface CompanyDetail {
  company: Company;
  segment: { id: string; name: string; nameCn: string };
  cycle?: CycleInfo | null; // economic-cycle position (consumer cycle themes)
  prices: PriceBar[];
  fundamentals: FundamentalRow[];
  signals: Signal[];
  supplyChain: SupplyChain;
  // --- Company 360 blocks (optional: older backends omit them; most names
  // have no thesis yet — every consumer must degrade gracefully) -----------
  thesis?: Thesis | null;
  coverage?: CompanyCoverage | null;
  estimates?: EstimateRow[];
  holdings?: HoldingRow[];
  calendar?: CalendarRow[];
  // High-frequency alternative-data signals — null for the ~99% of names with
  // no bindings yet; consumers hide the panel entirely when null.
  alt?: AltData | null;
  // Pre-earnings event-trading block — only for EARNINGS_UNIVERSE US names; null otherwise.
  earnings?: EarningsBlock | null;
}

// --- pre-earnings event-trading (mirrors dashboard._earnings_block) ---------
export interface EarningsVerdictSummary {
  direction: "long" | "short" | "no_trade";
  conviction: number;               // 0-10
  version: number;
  asOf: string;
  model?: string | null;
  impliedDriftPp?: number | null;   // 锁后 implied-move drift (percentage points)
}

export interface EarningsOutcome {
  date: string;
  direction: string;
  conviction: number;
  hit?: boolean | "abstain" | null;
  reactionPct?: number | null;
}

export interface EarningsBlock {
  event?: { date: string; session?: string | null; daysTo: number } | null;
  impliedMove?: number | null;      // straddle/spot ratio
  verdict?: EarningsVerdictSummary | null;
  beat?: {
    n: number; beat_rate: number | null; streak: number;
    avg_abs_surprise_pct: number | null;
    rows: { date: string; surprise_pct: number }[];
  } | null;
  recentOutcomes?: EarningsOutcome[];
}

export interface SegmentDetail {
  segment: Segment;
  companies: Company[];
  signals: Signal[];
}

/** Human-readable labels for catalyst types (EN + CN). */
export const CATALYST_LABEL: Record<CatalystType, { en: string; cn: string }> = {
  capex_guidance: { en: "Capex Guidance", cn: "资本开支指引" },
  order: { en: "Order", cn: "订单" },
  qualification: { en: "Qualification", cn: "客户认证" },
  product_ramp: { en: "Product Ramp", cn: "新品放量" },
  accelerator_launch: { en: "Accelerator Launch", cn: "加速器发布" },
  capacity_expansion: { en: "Capacity Expansion", cn: "产能扩张" },
  supply_constraint: { en: "Supply Constraint", cn: "供给约束" },
  earnings: { en: "Earnings", cn: "业绩/指引" },
  equity_investment: { en: "Equity Investment", cn: "股权投资" },
  tech_substitution: { en: "Tech Substitution", cn: "技术替代" },
  guidance_change: { en: "Guidance Change", cn: "指引变更" },
  mna: { en: "M&A", cn: "并购" },
  partnership: { en: "Partnership", cn: "合作" },
  contract_win: { en: "Contract Win", cn: "中标/合同" },
  pricing_change: { en: "Pricing Change", cn: "价格变动" },
  management_change: { en: "Management Change", cn: "管理层变动" },
  buyback: { en: "Buyback", cn: "回购" },
  dividend: { en: "Dividend", cn: "分红" },
  regulatory_action: { en: "Regulatory Action", cn: "监管行动" },
  litigation: { en: "Litigation", cn: "诉讼" },
  index_inclusion: { en: "Index Inclusion", cn: "指数纳入" },
  short_report: { en: "Short Report", cn: "做空报告" },
  macro_print: { en: "Macro Print", cn: "宏观数据" },
  stock_split: { en: "Stock Split", cn: "拆股" },
  secondary_offering: { en: "Secondary Offering", cn: "增发" },
};

/** Crash-proof label lookups: an unmapped value (a backend enum the frontend doesn't
 * know yet) degrades to showing the raw key instead of white-screening the whole
 * terminal (`MAP[x].en` on an undefined entry throws). */
export function catalystLabel(t: string): { en: string; cn: string } {
  return (CATALYST_LABEL as Record<string, { en: string; cn: string }>)[t] ?? { en: t, cn: t };
}
export function regimeLabel(r: string): { en: string; cn: string } {
  return (REGIME_LABEL as Record<string, { en: string; cn: string }>)[r] ?? { en: r, cn: r };
}

/** Labels for signal sources. */
export const SOURCE_LABEL: Record<SignalSource, string> = {
  filing: "Filing",
  wechat: "公众号",
  prediction_market: "Prediction Mkt",
  estimate: "Estimate",
  insider: "Insider",
  news: "News",
};

/** Labels for segment regime phases (EN + CN). */
export const REGIME_LABEL: Record<SegmentRegime, { en: string; cn: string }> = {
  accelerating: { en: "Accelerating", cn: "加速" },
  expansion: { en: "Expansion", cn: "扩张" },
  peaking: { en: "Peaking", cn: "见顶" },
  cooling: { en: "Cooling", cn: "降温" },
  trough: { en: "Trough", cn: "筑底" },
};
