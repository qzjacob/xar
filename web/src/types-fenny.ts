// Fenny (structured-notes / options desk) — client types. Job results are loosely typed
// (`dict` on the backend); we type the request payloads precisely and treat results as
// records keyed by the fields the panels read.

export type JobStatus = "queued" | "running" | "done" | "error";

export interface Job {
  job_id: string;
  status: JobStatus;
  stage: string;
  partial: Record<string, unknown>;
  error: string | null;
}

export interface AssetMarketInput {
  ticker: string;
  spot: number;
  atm_vol: number;
  skew_slope?: number;
  skew_curv?: number;
  div_yield?: number;
  borrow?: number;
}

export interface PresetRequest {
  variant: "fcn" | "phoenix" | "snowball";
  tickers: string[];
  notional?: number;
  currency?: string;
  trade_date: string;
  strike_date: string;
  maturity: string;
  coupon_rate?: number | null;
  frequency?: string;
  autocall_barrier?: number;
  ki_barrier?: number;
  ki_style?: string;
  settlement?: string;
  coupon_barrier?: number;
  memory?: boolean;
}

export interface MarketInput {
  asof?: string;
  rate?: number;
  issuer_spread?: number;
  assets: AssetMarketInput[];
  rho?: number | null;
  source?: "manual" | "live";
}

export interface RankRequest {
  structure?: Record<string, unknown>;
  source?: "manual" | "live";
  rate?: number;
  top_n?: number;
  rank_by?: string;
  assets?: AssetMarketInput[] | null;
  tickers?: string[] | null;
}

export interface MarketReadRequest {
  indices?: string[];
  source?: "manual" | "live";
  rate?: number;
  lang?: "en" | "zh";
  assets?: AssetMarketInput[] | null;
}

export interface OptionsMarketInputs {
  source?: "manual" | "live";
  spot?: number | null;
  rate?: number;
  div_yield?: number;
  borrow?: number;
  atm_vol?: number;
  skew_slope?: number;
  skew_curv?: number;
  max_maturity_years?: number;
}

export interface AdvisorRequest extends OptionsMarketInputs {
  ticker: string;
  direction?: "bullish" | "bearish" | "neutral";
  horizon?: "weeks" | "months" | "years";
  conviction?: number;
  vol_view?: "rising" | "falling" | "stable" | "spiked" | "depressed";
  risk_budget_pct?: number;
  income_preference?: boolean;
  language?: "zh" | "en";
  free_text?: string | null;
}
