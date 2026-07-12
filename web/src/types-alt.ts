// ===========================================================================
// Alternative-data high-frequency signals — mirrors the `alt` block on
// /api/ui/company/{cid} (and the standalone refetch /api/alt/company/{cid})
// plus the /api/ops/altdata/trackers operations payload.
//
// Each signal carries a z-score (-3..3, the headline) and a contribution
// (-1..1, which colors it). good_when=null signals are attention flags — they
// carry no directional read and must render neutral / gray. Every accessor is
// crash-proof: an unmapped signal key degrades to its bare suffix.
// ===========================================================================

export type SignalScope = "company" | "theme";
export type GoodWhen = "rising" | "falling" | null;

/** One high-frequency alt-data signal bound to a company (or its theme). */
export interface AltSignal {
  signal_key: string;
  name_cn: string;
  scope: SignalScope;
  theme?: string;
  good_when: GoodWhen;
  pillar_kinds: string[];
  contribution: number; // -1..1 — signed pull on the thesis (colors the row)
  latest: number; // latest raw reading
  z: number; // -3..3 — standardized headline
  momentum: number; // fraction, e.g. 0.65 = +65%
  n: number; // sample depth
  period_end: string; // ISO date of the latest observation
}

/** Aggregate alt-data read for one pillar kind (demand / technology / …). */
export interface AltPillarScore {
  score: number; // -1..1
  signals: AltSignal[];
}

/** The `alt` block: flat signal list + per-pillar-kind roll-ups. */
export interface AltData {
  signals: AltSignal[];
  pillar_scores: Record<string, AltPillarScore>;
}

/** Trimmed signal shape carried inside a health_v2 pillar entry. */
export interface PillarHealthSignal {
  signal_key: string;
  name_cn: string;
  z: number;
  momentum: number;
  contribution: number;
  period_end: string;
  scope: SignalScope;
}

// --- ops trackers (/api/ops/altdata/trackers) ------------------------------

export interface AltTrackerCoverage {
  companies: number;
  by_signal: Record<string, number>; // signal key -> # companies bound
  theme_signals: string[]; // keys that are theme-scoped
}
export interface AltTrackerSignal {
  key: string;
  name_cn: string;
  cadence: string;
  scope: SignalScope;
  good_when: GoodWhen;
  source: string;
}
export interface AltTrackerStock {
  signal_key: string;
  rows: number;
  companies: number;
  latest: string;
}
export interface AltTrackers {
  coverage: AltTrackerCoverage;
  signals: AltTrackerSignal[];
  stock: AltTrackerStock[];
}

// ===========================================================================
// Label / tone helpers (crash-proof)
// ===========================================================================

const SIGNAL_SHORT: Record<string, { short: string; en: string }> = {
  "alt.tw_monthly_revenue": { short: "月营收", en: "TW Rev" },
  "alt.kr_chip_exports": { short: "韩国出口", en: "KR Chip Exp" },
  "alt.semi_billings": { short: "全球出货", en: "SEMI B:B" },
  "alt.github_momentum": { short: "开源", en: "GitHub" },
  "alt.pkg_downloads": { short: "下载", en: "Downloads" },
  "alt.hiring_velocity": { short: "招聘", en: "Hiring" },
  "alt.wiki_attention": { short: "注意力", en: "Wiki Attn" },
};

/** Signal key -> short bilingual tag (falls back to the bare suffix). */
export function signalShort(key: string): { short: string; en: string } {
  const bare = key.replace(/^alt\./, "");
  return SIGNAL_SHORT[key] ?? { short: bare, en: bare };
}

export type SignalTone = "pos" | "neg" | "neutral";

/** Contribution sign -> tone (ignores good_when; used when it's unknown). */
export function contribTone(contribution: number): SignalTone {
  if (contribution > 0.05) return "pos";
  if (contribution < -0.05) return "neg";
  return "neutral";
}

/** Full tone: attention flags (good_when=null) are always neutral. */
export function signalTone(s: { good_when?: GoodWhen; contribution: number }): SignalTone {
  if (s.good_when == null) return "neutral";
  return contribTone(s.contribution);
}

/** Soft chip classes (bg + text + ring) for a signal tone. */
export function signalToneChip(tone: SignalTone): string {
  if (tone === "pos") return "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20";
  if (tone === "neg") return "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20";
  return "bg-surface-2 text-brand-500 ring-1 ring-inset ring-line";
}

/** Text-only color class for a signal tone. */
export function signalToneText(tone: SignalTone): string {
  if (tone === "pos") return "text-pos";
  if (tone === "neg") return "text-neg";
  return "text-brand-500";
}

/** ▲ / ▼ / • for a momentum reading (direction of the metric, not good/bad). */
export function momentumArrow(momentum: number): string {
  if (momentum > 0.005) return "▲";
  if (momentum < -0.005) return "▼";
  return "•";
}

export function scopeLabel(scope: SignalScope): { cn: string; en: string } {
  return scope === "theme" ? { cn: "链级", en: "Theme" } : { cn: "个股", en: "Company" };
}

/** good_when -> bilingual read (rising/falling = which direction is bullish). */
export function goodWhenLabel(g: GoodWhen): { cn: string; en: string } {
  if (g === "rising") return { cn: "上行为佳", en: "Higher better" };
  if (g === "falling") return { cn: "下行为佳", en: "Lower better" };
  return { cn: "注意力", en: "Attention" };
}
