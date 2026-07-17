// ===========================================================================
// XAR Andy — macro-indicator module domain model. Mirrors the vendored slx
// API (/api/andy/{metrics,registry,overclaims}) plus the XAR-native 勾稽
// (crosswalk) contract under /api/andy/link/* (frozen contract; may 404 while
// the backend lands — every consumer must degrade gracefully).
// ===========================================================================

/** Epistemic hardness ladder: physical/accounting fact → logic → hypothesis → wall. */
export type Hardness = "hard" | "medium" | "soft" | "wall";

export type IdentificationStatus =
  | "identified"
  | "partially_identified"
  | "unidentified"
  | "not_quantified";

/** Watermark block attached to every metric / claim — the discipline layer.
 * soft → unidentified, ALWAYS; is_causal_claim is永远 false by contract. */
export interface AndyIdentification {
  hardness: Hardness | null;
  identification_status: IdentificationStatus;
  identification_strategy: string | null;
  is_causal_claim: false;
  caveat: string | null;
  watermark: string;
}

/** metric_registry row (ontology catalog — no readings). */
export interface AndyRegistry {
  metric_key: string;
  display_name_zh: string;
  family: string;
  theory_anchor: string[];
  binding_scarcity: string | null;
  phase: string | null;
  mechanism: string | null;
  hardness: Hardness;
  identification_strategy: string | null;
  falsification_condition: string | null;
  decision_window: string | null;
  source_grade: string | null;
  caveat: string | null;
  is_quantifiable: boolean;
  unit: string | null;
  geo_scope: string | null;
  status: string | null;
  identification: AndyIdentification;
}

export interface AndyMetricsList {
  count: number;
  metrics: AndyRegistry[];
  disclaimer: string;
}

export interface AndySeriesPoint {
  valid_time: string; // YYYY-MM-DD
  value: number;
}

/** GET /metrics/{key}?as_of= — point-in-time reading (knowledge_time <= as_of). */
export interface AndyMetricReading {
  metric_key: string;
  as_of: string;
  unit: string | null;
  registry: AndyRegistry;
  identification: AndyIdentification;
  point_in_time: true;
  value: number | null; // walls → always null BY DESIGN
  series: AndySeriesPoint[] | null;
  slope: number | null;
  note?: string;
}

/** theory_anchor row: A1..A8 axioms + META_migration / META_conservation. */
export interface AndyAnchor {
  anchor_key: string;
  title: string;
  industrial_assumption: string;
  silicon_restatement: string;
  verdict: string;
}

export interface AndyAnchorsList {
  count: number;
  anchors: AndyAnchor[];
}

// --- overclaim registry ------------------------------------------------------
export type ClaimStatus =
  | "open"
  | "fixation_triggered"
  | "falsified"
  | "expired"
  | "inconclusive";

export interface AndyEvalLogEntry {
  evaluated_at: string | null;
  as_of_date: string;
  verdict: string;
  verdict_note?: string;
  metric_readings: unknown;
  triggered: boolean;
}

export interface AndyClaim {
  claim_key: string;
  claim_text_zh: string;
  related_metrics: string[];
  hardness: Hardness;
  decision_window: string; // e.g. "24m"
  window_start: string; // YYYY-MM-DD
  fixation_rule: string;
  falsify_rule: string;
  status: ClaimStatus;
  verdict_note: string;
  last_evaluated: string | null;
  evidence_snapshot: unknown;
  owner: string | null;
  identification: AndyIdentification;
  needs_identification: boolean;
  recent_eval_log?: AndyEvalLogEntry[];
}

export interface AndyClaimsList {
  count: number;
  claims: AndyClaim[];
  disclaimer: string;
}

export interface AndyEvaluateResult {
  as_of: string;
  evaluated: number;
  results: { claim_key: string; verdict: string; verdict_note?: string }[];
  disclaimer: string;
}

// --- data sources — /api/andy/sources -----------------------------------------
export interface AndyConnectorRun {
  status: "ok" | "error" | "running";
  started_at: string;
  finished_at: string | null;
  rows_written: number | null;
  error: string;
}

export interface AndyConnector {
  source_id: string;
  is_primary: boolean;
  /** Env var holding the API key; null = keyless connector (零key). */
  key_env: string | null;
  key_present: boolean;
  last_run: AndyConnectorRun | null;
  observations: number;
  metrics: string[];
}

export interface AndyMetricFreshness {
  metric_key: string;
  display_name_zh: string;
  hardness: Hardness;
  observations: number;
  latest_valid_time: string | null;
  latest_knowledge_time: string | null;
}

export interface AndySourcesResponse {
  connectors: AndyConnector[];
  metrics_freshness: AndyMetricFreshness[];
}

// --- XAR-native 勾稽 (crosswalk) contract — /api/andy/link/* ------------------
export interface LinkMetric {
  metric_key: string;
  display_name_zh: string;
  hardness: Hardness;
  family: string;
  scope: "chain" | "platform";
  /** Which direction is bullish for the linked theme (null = no stance). */
  good_when: "rising" | "falling" | null;
  rationale_zh: string;
  segments: string[];
  tech_routes: string[];
}

export interface LinkThemesResponse {
  themes: {
    theme: string;
    name: string;
    name_cn: string;
    kind: "chain" | "cycle";
    metrics: LinkMetric[];
    overclaims: { claim_key: string; status: ClaimStatus }[];
  }[];
  platform_metrics: LinkMetric[];
}

export interface LinkThemeMetric extends LinkMetric {
  unit: string | null;
  identification: AndyIdentification;
  value: number | null;
  slope: number | null;
  valid_time: string | null;
  series: AndySeriesPoint[];
}

export interface LinkThemeResponse {
  theme: string;
  name: string;
  name_cn: string;
  as_of: string;
  metrics: LinkThemeMetric[];
  overclaims: {
    claim_key: string;
    claim_text_zh: string;
    status: ClaimStatus;
    polarity_on_fixation: string;
    polarity_on_falsified: string;
  }[];
}

export interface LinkMetricDetail {
  metric_key: string;
  display_name_zh: string;
  hardness: Hardness;
  scope: "chain" | "platform";
  good_when: "rising" | "falling" | null;
  rationale_zh: string;
  themes: { theme: string; name: string; name_cn: string; genny_link: string }[];
  segments: { id: string; name: string; name_cn: string; theme: string; genny_link: string }[];
  tech_routes: { id: string; name: string; name_cn: string }[];
  companies: { id: string; name: string; ticker: string; theme: string; genny_link: string }[];
  recent_events: { summary: string; event_date: string; polarity: string; theme: string }[];
}

// ── Money flow strategy panel (/api/andy/flow) ────────────────────────────────
export interface FlowSeriesPoint {
  d: string;
  v: number;
}

export interface FlowAsset {
  ticker: string;
  label: string;
  label_cn: string;
  asset_class: string;
  obv_z: number | null;
  dollar_vol_z: number | null;
  mom_63d: number | null;
  mom_z: number | null;
  composite: number | null;
  as_of: string | null;
  spark: FlowSeriesPoint[];
}

export interface FlowStyle {
  pair: string;
  label: string;
  label_cn: string;
  rationale_zh: string;
  z: number | null;
  series: FlowSeriesPoint[];
}

export interface FlowEvent {
  type: "flow_signal" | "flow_insight";
  date: string | null;
  polarity: "positive" | "negative" | "neutral";
  summary: string;
  company: string | null;
  theme: string | null;
  attrs: Record<string, unknown>;
}

export type FlowStance = "overweight" | "neutral" | "underweight" | "no_data";

export interface FlowTilt {
  asset_class: string;
  label_cn: string;
  score: number | null;
  stance: FlowStance;
  drivers: { ticker: string; composite: number | null; obv_z: number | null; mom_63d: number | null }[];
}

export interface AndyFlowResponse {
  as_of: string;
  assets: FlowAsset[];
  styles: FlowStyle[];
  sentiment: {
    pc: { value: number | null; basis: string | null; series: FlowSeriesPoint[] };
    short_interest_top: {
      company_id: string;
      name: string;
      ticker: string | null;
      days_to_cover: number;
      period_end: string;
    }[];
    flow_events: FlowEvent[];
  };
  strategy: {
    risk_on: { value: number | null; series: FlowSeriesPoint[] };
    tilts: FlowTilt[];
  };
  themes: { theme: string; name_cn: string; score: number | null; as_of: string | null; genny_link: string }[];
}

// ── Macro database console (/api/andy/macro + /api/andy/link/chain) ───────────
export interface MacroSeriesPoint {
  t: string;
  v: number;
}

export interface MacroMetricRow {
  metric_key: string;
  name_cn: string;
  hardness: Hardness;
  unit: string | null;
  good_when: "rising" | "falling" | null;
  value: number | null;
  valid_time: string | null;
  slope: number | null;
  series: MacroSeriesPoint[];
  has_chain: boolean;
}

export interface MacroTransmissionEdge {
  from: string;
  to: string;
  sign: "+" | "-" | "±";
  lag_hint: string;
  rationale_zh: string;
}

export interface AndyMacroResponse {
  as_of: string;
  families: { family: string; metrics: MacroMetricRow[] }[];
  transmissions: MacroTransmissionEdge[];
  /** 传导链端点中文名（硅基核心 metric + theme:/flow: 哨兵，后端权威）。 */
  labels: Record<string, string>;
  silicon_families: { family: string; count: number }[];
}

export interface ChainNode {
  kind: "metric" | "theme" | "flow";
  key: string;
  name_cn: string;
  family?: string | null;
  hardness?: string | null;
  unit?: string | null;
  value?: number | null;
  valid_time?: string | null;
  link: string;
}

export interface AndyChainResponse {
  root: string;
  as_of: string;
  depth: number;
  nodes: Record<string, ChainNode>;
  upstream: MacroTransmissionEdge[];
  downstream: MacroTransmissionEdge[];
}
