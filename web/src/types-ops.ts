// Control-plane (operations console) types — mirror src/xar/api/ops.py outputs.

export interface OntologyNodeType {
  type: string;
  schemaIri: string;
  fiboIri: string;
  count: number;
}
export interface OntologyEdgeType {
  type: string;
  iri: string;
  count: number;
}
export interface OntologyCatalystType {
  type: string;
  label: string;
  count: number;
}
export interface OntologyMetric {
  metric: string;
  isRatio: boolean;
  providers: string[];
  count: number;
}
export interface OntologyInfo {
  nodeTypes: OntologyNodeType[];
  edgeTypes: OntologyEdgeType[];
  catalystTypes: OntologyCatalystType[];
  finMetrics: OntologyMetric[];
  signalMap: Record<string, string>;
  standards: { fibo: string; schema: string };
  totals: { nodes: number; edges: number; events: number; aliases: number };
}

export interface SourceInfo {
  id: string;
  name: string;
  category: string;
  permission: string;
  keyEnv: string | null;
  runnable: boolean;
  desc: string;
  available: boolean;
  rows: number;
  table: string;
  lastRun: string | null;
}
export interface SourcesInfo {
  sources: SourceInfo[];
  categories: string[];
  summary: { total: number; available: number; rows: number };
}

export interface LlmVendor {
  id: string;
  name: string;
  configured: boolean;
  keyEnv: string;
  models: string[];
}
export interface LlmUsageRow {
  model: string;
  calls: number;
  inTok: number;
  outTok: number;
  usd: number;
}
export interface LlmInfo {
  vendors: LlmVendor[];
  routing: {
    fast: string;
    strong: string;
    effort: string;
    budgetUsdPerRun: number;
    embedModel: string;
    embedDim: number;
  };
  prices: { model: string; inUsd: number; outUsd: number }[];
  usage: { total: { calls: number; inTok: number; outTok: number; usd: number }; byModel: LlmUsageRow[] };
  configured: boolean;
}

export interface OutboundConnector {
  id: string;
  name: string;
  baseUrl: string;
  auth: string;
  configured: boolean;
  mcp: boolean;
  category: string;
}
export interface InboundGroup {
  group: string;
  desc: string;
  endpoints: string[];
}
export interface ConnectorsInfo {
  outbound: OutboundConnector[];
  inbound: InboundGroup[];
  mcpNote: string;
  summary: { outbound: number; configured: number; inboundGroups: number };
}

export interface Skill {
  id: string;
  name: string;
  stage?: number;
  tier?: string;
  desc: string;
  numeric?: boolean;
  query?: string;
}
export interface Capability {
  id: string;
  name: string;
  desc: string;
}
export interface SkillsInfo {
  pipeline: Skill[];
  capabilities: Capability[];
  summary: { stages: number; skills: number; capabilities: number };
}

export interface DataLakeBySource {
  source: string;
  docs: number;
  parsed: number;
  chunks: number;
}
export interface DataLakeInfo {
  totals: { documents: number; chunks: number; parsed: number; extracted: number };
  bySource: DataLakeBySource[];
  byPermission: { permission: string; c: number }[];
  pending: number;
}
export interface LakeDocument {
  id: string;
  company_id: string | null;
  source: string;
  doc_type: string | null;
  title: string | null;
  url: string | null;
  permission: string;
  license_tag: string | null;
  published_at: string | null;
  ingested_at: string | null;
  chars: number | null;
  chunks: number;
  extracted: boolean;
}
export interface LakeDocsPage {
  total: number;
  limit: number;
  offset: number;
  documents: LakeDocument[];
}

export interface SelfTestCheck {
  id: string;
  group: string;
  status: string;
  detail: string;
}
export interface SelfTest {
  checks: SelfTestCheck[];
  summary: Record<string, number>;
  ranAt: string;
}

export interface ExpertStats {
  totals: { processed: number; kept: number; pending: number; expertEvents: number };
  bySource: { source: string; processed: number; kept: number; avgQuality: number }[];
  qualityMin: number;
}
export interface ExpertInsightRow {
  companyId: string | null;
  company: string | null;
  source: string;
  stance: string;
  polarity: string;
  catalystType: string;
  thesis: string;
  signalQuality: number;
  techRoute: string | null;
  ts: string | null;
}
export interface AltDataInfo {
  stats: ExpertStats;
  insights: ExpertInsightRow[];
}

export interface HealthInfo {
  ok: boolean;
  llm_configured: boolean;
  embed_model: string;
  model_strong: string;
  model_fast: string;
  data_posture: string;
  providers: Record<string, boolean>;
}

export interface ActionResult {
  status: string;
  source?: string;
}
export interface LlmTestResult {
  ok: boolean;
  model?: string;
  reply?: string;
  detail?: string;
}

// --- coverage 360 dashboard (/api/ops/coverage) ----------------------------
export interface OpsCoverageDimension {
  key: string;
  name: string;
  name_cn: string;
  weight: number; // dimension weights sum to 1.0
}
export interface OpsCoverageTheme {
  theme: string;
  name: string;
  name_cn: string;
  companies: number;
  avg_composite: number; // 0..1 mean weighted composite across members
  dims: Record<string, number>; // dim key -> 0..1 fill rate (share of names with score >= 0.34)
}
export interface OpsCoverageInfo {
  dimensions: OpsCoverageDimension[];
  themes: OpsCoverageTheme[];
}

// ── Fetchy:glmworker 管理面(/api/ops/fetchy)─────────────────────────────────
export interface FetchyConfig {
  enabled: boolean;
  model: string;
  sources: Record<string, boolean>;
  stages: Record<string, boolean>;
}
export interface FetchySource {
  key: string;
  label: string;
  hours: number | null;
  last: string | null; // cadence 最近一次运行(ISO)
}
export interface FetchyStage {
  key: string;
  label: string;
}
export interface FetchyModel {
  id: string;
  provider: string;
  billing: string; // "subscription" | "token"
  preferred: boolean;
  notes: string;
}
export interface FetchyInfo {
  config: FetchyConfig;
  defaults: FetchyConfig;
  sources: FetchySource[];
  stages: FetchyStage[];
  models: FetchyModel[];
  status: {
    quota: { status?: string; reason?: string } | null;
    counters: { cycles?: number; docs_extracted?: number; last_cycle_at?: string };
    backlog_docs: number | null;
    pin: string[];
  };
}
