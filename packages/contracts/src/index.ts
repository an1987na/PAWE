export type Confidence = "high" | "medium" | "low";
export type DailyRiskStatus = "on_track" | "watch" | "risk_triggered" | "data_degraded";
export type MarketState = "NORMAL" | "ANCHOR_DISTORTED" | "SYSTEMIC_RETREAT" | "BREADTH_RECOVERY" | "RECOVERY_CONFIRMED" | "RECOVERY_FAILED";

export interface WeeklyDecisionItem {
  stock_code: string;
  stock_name: string;
  rank: number;
  target_return: number;
  confidence: Confidence;
  summary: string;
  primary_risk: string;
  primary_sector: string | null;
  rule_score: number | null;
  selection_reasons: string[];
  score_breakdown: Record<string, number>;
}

export interface WeekSummary {
  week_id: string;
  status: "published";
  market_state: MarketState;
  decision_version: number;
  confidence: Confidence;
  shortage: boolean;
  shortage_reason: string | null;
  items: WeeklyDecisionItem[];
}

export interface DailyBriefItem {
  stock_code: string;
  stock_name: string;
  daily_return: number;
  week_to_date_return: number;
  week_high_return: number;
  drawdown_from_week_high: number;
  distance_to_target: number;
  volume_activity: number | null;
  risk_status: DailyRiskStatus;
  summary: string;
  evidence_ids?: string[];
}

export interface DailyBrief {
  week_id: string;
  trade_date: string;
  decision_version: number;
  as_of: string;
  fetched_at: string;
  quality: "verified" | "single_source" | "degraded" | "conflicted" | "missing";
  ai_degraded: boolean;
  items: DailyBriefItem[];
}

export type ReplayStage = "weekly_selection" | "daily_brief" | "weekly_review";

export type AICapability = "weekly_selection" | "weekly_review" | "error_attribution" | "rule_evolution";
export type AttributionTaxonomy = "market_state_error" | "rotation_lag" | "continuation_overreach" | "overheat_filter_loose" | "overheat_filter_strict" | "stock_selection_error" | "catalyst_error" | "confirmation_insufficient" | "data_anomaly" | "candidate_coverage_insufficient" | "anchor_distortion" | "ai_swap_error" | "human_override_error";
export interface ErrorAttribution {
  id: string;
  week_id: string;
  review_id: string | null;
  taxonomy: AttributionTaxonomy;
  confidence: string;
  facts: Record<string, unknown>;
  proposed_hypothesis: string;
  counterfactual_allowed: boolean;
  input_fingerprint: string;
  status: "proposed" | "confirmed" | "rejected";
  created_at: string;
  updated_at: string;
}

export interface ReplayStageRun {
  id: string;
  stage: ReplayStage;
  trade_date: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "skipped";
  information_cutoff: string;
  actual_run_at: string | null;
  input_fingerprint: string;
  warnings: string[];
  error_code: string | null;
  error_message: string | null;
  details: Record<string, unknown>;
  items?: Array<Record<string, unknown>>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ReplayRun {
  id: string;
  week_id: string;
  requested_stage: ReplayStage;
  trade_date: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  rule_version: string;
  effective_rule_version: string;
  information_cutoff: string;
  simulated_selection_at: string | null;
  simulated_review_at: string | null;
  simulated_trade_date: string | null;
  actual_run_at: string;
  input_fingerprint: string;
  warnings: string[];
  details: Record<string, unknown>;
  stages: ReplayStageRun[];
}

export interface WatchlistItem {
  id: string;
  stock_code: string;
  stock_name: string;
  exchange: string;
  board: string;
  added_at: string;
  effective_from: string;
}

export interface StockSearchResult {
  stock_code: string;
  stock_name: string;
  exchange: string;
  board: string;
  already_followed: boolean;
}

export interface WatchlistDailyBrief {
  week_id: string;
  trade_date: string;
  items: DailyBriefItem[];
}

export interface WeeklyReviewItem {
  stock_code: string;
  stock_name: string;
  rank: number;
  entry_price: number;
  week_high_return: number;
  week_close_return: number;
  max_drawdown_from_entry: number;
  max_peak_to_trough_drawdown: number;
  target_touched: boolean;
  target_touch_date: string | null;
  drawdown_before_touch: number | null;
  accessible_at_entry: boolean;
  benchmark_return: number | null;
  benchmark_excess: number | null;
  industry_return: number | null;
  industry_excess: number | null;
}

export interface WeeklyReview {
  id: string;
  week_id: string;
  source_type: "rule" | "ai" | "published" | "historical_replay";
  source_version: number;
  rule_version: string;
  status: "completed" | "degraded" | "failed";
  entry_trade_date: string;
  final_trade_date: string;
  as_of: string;
  generated_at: string;
  quality: "verified" | "single_source" | "degraded" | "conflicted" | "missing";
  aggregate: Record<string, unknown>;
  summary: string;
  warnings: string[];
  items: WeeklyReviewItem[];
}

export interface WatchlistWeeklyReview {
  week_id: string;
  generated_at: string;
  items: WeeklyReviewItem[];
}
