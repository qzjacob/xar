// ===========================================================================
// Company 360 / Investment Thesis domain model — mirrors the extended
// /api/ui/company/{cid} payload (thesis / coverage / estimates / holdings /
// calendar blocks in xar/api/dashboard.py) plus /api/thesis/{cid}/build and
// /api/ops/coverage (xar/ontology/coverage360.py).
//
// Every accessor here is crash-proof: unmapped backend enums degrade to the
// raw key instead of white-screening the terminal.
// ===========================================================================

// --- thesis ----------------------------------------------------------------

export type ThesisStance = "bull" | "neutral" | "bear";

export type PillarKind =
  | "demand"
  | "moat"
  | "supply_chain"
  | "technology"
  | "financials"
  | "valuation"
  | "policy"
  | "cyclical";

export type EvidenceKind =
  | "event"
  | "edge"
  | "chunk"
  | "insight"
  | "fundamental"
  | "estimate"
  | "registry";

export type ThesisHealthOverall = "confirming" | "challenged" | "quiet";
export type PillarHealthStatus = "confirming" | "challenging" | "mixed" | "quiet";

export interface ThesisEvidence {
  kind: EvidenceKind;
  ref_id: string | number;
  quote: string;
}

export interface ThesisPillar {
  key: string;
  kind: PillarKind;
  title_zh: string;
  claim_zh: string;
  weight: number; // 0..1 — share of the thesis this pillar carries
  score: number; // -1..1 — current read (diverging)
  evidence: ThesisEvidence[];
  watch_metrics: string[];
  watch_event_types: string[];
  falsifier_zh: string;
}

export interface ThesisDriver {
  name: string;
  direction: "tailwind" | "headwind";
  weight: number; // 0..1
  note_zh: string;
}

export interface ThesisRisk {
  type: string;
  desc_zh: string;
  severity: number; // 0..1
  watch_zh: string;
  evidence: ThesisEvidence[];
}

export interface ThesisValuationCase {
  case: "bull" | "base" | "bear";
  method_zh: string;
  assumption_zh: string;
  implied_view_zh: string;
}

export interface ThesisWatchItem {
  what_zh: string;
  when: string;
  pillar_key: string;
  direction_zh: string;
}

export interface ThesisContent {
  one_liner_zh: string;
  narrative_zh: string;
  stance: ThesisStance;
  conviction: number; // 1..5
  pillars: ThesisPillar[];
  drivers: ThesisDriver[];
  bull_case_zh: string;
  bear_case_zh: string;
  variant_perception_zh: string;
  risks: ThesisRisk[];
  valuation: ThesisValuationCase[];
  what_to_watch: ThesisWatchItem[];
  coverage_gaps_zh: string[];
}

export interface ThesisQuality {
  evidence_coverage: number; // 0..1 — share of claims with anchors
  numeric_grounding: number; // 0..1 — share of pillars with numbers
  evidence_anchors: number; // total anchor count
  dossier_facts: number; // facts fed to the builder
}

export interface ThesisHealthPillar {
  key: string;
  title_zh: string;
  new_facts: number;
  net_polarity: number;
  status: PillarHealthStatus;
}

export interface ThesisHealth {
  thesis_version: number;
  as_of: string;
  overall: ThesisHealthOverall;
  pillars: ThesisHealthPillar[];
}

export interface Thesis {
  version: number;
  as_of: string;
  stance: ThesisStance;
  conviction: number; // 1..5
  one_liner: string;
  quality: ThesisQuality;
  changed_because: string;
  content: ThesisContent;
  health: ThesisHealth | null; // machine-checked drift vs new facts
}

/** POST /api/thesis/{cid}/build result (sync; takes seconds). */
export interface ThesisBuildResult {
  status: "built" | "skipped" | "rejected" | "no_data";
  company_id?: string;
  version?: number;
  reason?: string;
  [k: string]: unknown;
}

// --- company 360 coverage ---------------------------------------------------

export interface CoverageDimCell {
  n: number; // rows found
  score: number; // 0..1 fill vs target
}

export interface CompanyCoverage {
  dims: Record<string, CoverageDimCell>;
  composite: number; // 0..1 weighted composite
}

/** The 16 coverage dimensions, in backend order (ontology/coverage360.py). */
export const COVERAGE_DIMS: { key: string; en: string; cn: string }[] = [
  { key: "identity", en: "Identity & classification", cn: "身份与分类" },
  { key: "documents", en: "Filings & documents", cn: "公告与文档" },
  { key: "catalysts", en: "Dated catalysts (past)", cn: "已发生催化剂" },
  { key: "forward", en: "Forward calendar", cn: "前瞻日历" },
  { key: "guidance", en: "Guidance / forward claims", cn: "指引与前瞻声明" },
  { key: "fundamentals", en: "Financial snapshot", cn: "财务快照" },
  { key: "fin_series", en: "Financial time series", cn: "财务时序" },
  { key: "estimates", en: "Analyst estimates", cn: "分析师预期" },
  { key: "ratings", en: "Ratings & price targets", cn: "评级与目标价" },
  { key: "prices", en: "Market prices", cn: "行情" },
  { key: "ownership", en: "Institutional ownership", cn: "机构持仓" },
  { key: "insider", en: "Insider activity", cn: "内部人交易" },
  { key: "supply_chain", en: "Supply-chain edges", cn: "供应链关系" },
  { key: "sentiment", en: "Social & expert voice", cn: "社媒与专家声音" },
  { key: "insights", en: "Expert insights", cn: "专家洞见" },
  { key: "thesis", en: "Investment thesis", cn: "投资论点" },
];

// --- estimates / holdings / calendar ----------------------------------------

export interface EstimateRow {
  metric: string;
  period: string;
  value: number;
  high: number | null;
  low: number | null;
  n_analysts: number | null;
  as_of: string;
}

export interface HoldingRow {
  holder: string;
  shares: number | null;
  value_usd: number | null;
  as_of: string;
}

export interface CalendarRow {
  event_type: string;
  event_date: string;
  title: string;
  status: string | null;
}

// ===========================================================================
// Label / tone maps (crash-proof accessors)
// ===========================================================================

const PILLAR_KIND_LABEL: Record<PillarKind, { en: string; cn: string }> = {
  demand: { en: "Demand", cn: "需求" },
  moat: { en: "Moat", cn: "护城河" },
  supply_chain: { en: "Supply Chain", cn: "供应链" },
  technology: { en: "Technology", cn: "技术" },
  financials: { en: "Financials", cn: "财务" },
  valuation: { en: "Valuation", cn: "估值" },
  policy: { en: "Policy", cn: "政策" },
  cyclical: { en: "Cyclical", cn: "周期" },
};

export function pillarKindLabel(kind: string): { en: string; cn: string } {
  return (
    (PILLAR_KIND_LABEL as Record<string, { en: string; cn: string }>)[kind] ?? {
      en: kind,
      cn: kind,
    }
  );
}

/** Stance -> bilingual label + chip/dot tones (bull=pos, bear=neg, neutral=slate). */
export function stanceMeta(stance: string): {
  en: string;
  cn: string;
  chip: string;
  dot: string;
} {
  switch (stance) {
    case "bull":
      return { en: "Bull", cn: "多头", chip: "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20", dot: "bg-pos" };
    case "bear":
      return { en: "Bear", cn: "空头", chip: "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20", dot: "bg-neg" };
    default:
      return {
        en: "Neutral",
        cn: "中性",
        chip: "bg-surface-2 text-slate-300 ring-1 ring-inset ring-line",
        dot: "bg-slate-400",
      };
  }
}

/** Overall thesis-health -> bilingual label + chip tone. */
export function healthOverallMeta(overall: string): { en: string; cn: string; chip: string; dot: string } {
  switch (overall) {
    case "confirming":
      return {
        en: "Confirming",
        cn: "论点获证实",
        chip: "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20",
        dot: "bg-pos",
      };
    case "challenged":
      return {
        en: "Challenged",
        cn: "论点受挑战",
        chip: "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20",
        dot: "bg-neg",
      };
    default:
      return {
        en: "Quiet",
        cn: "静默",
        chip: "bg-surface-2 text-slate-400 ring-1 ring-inset ring-line",
        dot: "bg-slate-400",
      };
  }
}

/** Per-pillar health status -> dot tone + bilingual label. */
export function pillarStatusMeta(status: string): { en: string; cn: string; dot: string } {
  switch (status) {
    case "confirming":
      return { en: "Confirming", cn: "获证实", dot: "bg-pos" };
    case "challenging":
      return { en: "Challenging", cn: "受挑战", dot: "bg-neg" };
    case "mixed":
      return { en: "Mixed", cn: "多空交织", dot: "bg-warn" };
    default:
      return { en: "Quiet", cn: "静默", dot: "bg-slate-500" };
  }
}

/** Evidence-kind -> mono chip tone (event=amber, edge=teal, insight=indigo…). */
const EVIDENCE_CHIP: Record<EvidenceKind, string> = {
  event: "bg-accent-50 text-accent-700 ring-accent/20",
  edge: "bg-andy-50 text-andy-700 ring-andy-500/20",
  chunk: "bg-surface-2 text-slate-400 ring-line",
  insight: "bg-explore-50 text-explore-700 ring-explore-500/20",
  fundamental: "bg-pos-50 text-pos-700 ring-pos/20",
  estimate: "bg-brand-50 text-brand-200 ring-brand-100",
  registry: "bg-surface-2 text-slate-500 ring-line",
};

export function evidenceChipClass(kind: string): string {
  const tone = (EVIDENCE_CHIP as Record<string, string>)[kind] ?? "bg-surface-2 text-slate-400 ring-line";
  return `ring-1 ring-inset ${tone}`;
}

/** Valuation scenario -> chip tone. */
export function valuationCaseMeta(c: string): { en: string; cn: string; chip: string } {
  switch (c) {
    case "bull":
      return { en: "Bull", cn: "乐观", chip: "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20" };
    case "bear":
      return { en: "Bear", cn: "悲观", chip: "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20" };
    default:
      return { en: "Base", cn: "基准", chip: "bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20" };
  }
}
