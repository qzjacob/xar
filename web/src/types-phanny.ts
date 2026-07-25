/** Phanny (季报多空事件交易) 前端类型 — 与 /api/phanny/* 对齐。 */
export interface PhannyTrade {
  company_id: string;
  event_date: string;
  direction?: "long" | "short";
  conviction?: number;
  size_pct?: number | null;
  ensemble_status?: string;
  version?: number;
  verdict?: null;
}

export interface PhannyDistribution {
  ok: boolean;
  n: number;
  reason: string;
  mean?: number;
  std?: number;
  skew?: number;
  shapiro_p?: number | null;
  high_ratio?: number;
  buckets?: Record<string, number>;
}

export interface PhannyPortfolio {
  trades: PhannyTrade[];
  n: number;
  distribution: PhannyDistribution;
  histogram: Record<string, number>;
}

export interface PhannyCalBucket {
  n: number;
  decided: number;
  hit_rate: number | null;
  avg_reaction_pct: number | null;
}

export interface PhannySchedule {
  run_id: string;
  status: string;
}
