from dataclasses import dataclass
from enum import StrEnum

from pawe_api.contracts import DataQuality


class Domain(StrEnum):
    MAIN = "main"
    SUPPLEMENTARY = "supplementary"
    EXTERNAL = "external"


class Board(StrEnum):
    MAIN = "main"
    GEM = "gem"
    STAR = "star"
    BSE = "bse"
    OTHER = "other"


class StockStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ST = "st"
    DELISTING = "delisting"
    DELISTED = "delisted"


class StateFit(StrEnum):
    FULL = "full"
    SECONDARY = "secondary"
    NEUTRAL = "neutral"
    INCOMPATIBLE = "incompatible"


class CandidateBucket(StrEnum):
    ELIGIBLE = "eligible"
    HIGH_VOLATILITY_RESERVE = "high_volatility_reserve"
    STAR_REFERENCE = "star_reference"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class RuleFeatures:
    stock_code: str
    stock_name: str
    board: Board
    status: StockStatus
    primary_domain: Domain
    primary_sector: str
    industry_chain_priority: int
    near_month_unexhausted: bool
    low_crowding_exploration: bool
    high_elasticity_exploration: bool
    high_heat_direction: bool
    external_industry_strength_rank: int | None
    external_industry_sync_count: int
    global_base_rank: int | None
    has_verifiable_external_evidence: bool
    listing_trading_days: int
    last_trade_suspended: bool
    has_key_market_data: bool
    code_valid: bool
    adjustment_valid: bool
    avg_amount_20d: float
    return_5d: float
    return_20d: float
    return_60d: float
    distance_high_20d: float
    volume_activity_5d: float
    volatility_percentile: float
    above_ma20: bool
    amount_anomaly_days: int
    sector_up_ratio_5d: float
    sector_positive_peer_count: int
    sector_top20_peer_count: int
    sector_volume_activity_median: float
    sector_contributor_count: int
    adjacent_segment_count: int
    state_fit: StateFit
    previous_close_positive: bool
    previous_week_high_return: float | None
    previous_touch_drawdown: float | None
    strong_reserve_promotion: bool
    previous_target_touched: bool
    has_new_confirmation: bool
    has_direct_catalyst: bool
    financial_not_deteriorating: bool
    independent_evidence_sources: int
    data_quality: DataQuality
    trading_anomaly: bool
    single_anchor_crowded: bool


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    features: RuleFeatures
    rule_score: float
    bucket: CandidateBucket
    exclusion_reasons: tuple[str, ...]
    score_breakdown: dict[str, float]
