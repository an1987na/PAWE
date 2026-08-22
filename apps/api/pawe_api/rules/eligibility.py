from dataclasses import dataclass

from pawe_api.contracts import DataQuality
from pawe_api.rules.models import Board, CandidateBucket, Domain, RuleFeatures, StockStatus


@dataclass(frozen=True, slots=True)
class HardConstraintConfig:
    allow_star_in_main_pool: bool = False
    minimum_listing_trading_days: int = 60
    minimum_average_amount_20d: float = 100_000_000
    overheat_return_20d: float = 0.40
    overheat_repair_return_5d: float = -0.08
    overheat_repair_distance_high_20d: float = -0.15


DEFAULT_HARD_CONSTRAINTS = HardConstraintConfig()


def evaluate_eligibility(
    features: RuleFeatures,
    config: HardConstraintConfig = DEFAULT_HARD_CONSTRAINTS,
) -> tuple[CandidateBucket, tuple[str, ...]]:
    reasons: list[str] = []
    if features.status is not StockStatus.ACTIVE:
        reasons.append(f"stock_status:{features.status.value}")
    if features.last_trade_suspended:
        reasons.append("last_trade_suspended")
    if features.listing_trading_days < config.minimum_listing_trading_days:
        reasons.append("listing_history_too_short")
    if features.avg_amount_20d < config.minimum_average_amount_20d:
        reasons.append("liquidity_below_threshold")
    if not features.has_key_market_data:
        reasons.append("key_market_data_missing")
    if not features.code_valid:
        reasons.append("stock_code_invalid")
    if not features.adjustment_valid:
        reasons.append("price_adjustment_invalid")
    if features.data_quality in {DataQuality.CONFLICTED, DataQuality.MISSING}:
        reasons.append(f"data_quality:{features.data_quality.value}")
    if reasons:
        return CandidateBucket.EXCLUDED, tuple(reasons)

    if features.board is Board.STAR and not config.allow_star_in_main_pool:
        return CandidateBucket.STAR_REFERENCE, ("star_board_main_pool_disabled",)

    if features.return_20d > config.overheat_return_20d:
        repaired = (
            features.return_5d <= config.overheat_repair_return_5d
            and features.distance_high_20d <= config.overheat_repair_distance_high_20d
        )
        if repaired:
            return CandidateBucket.HIGH_VOLATILITY_RESERVE, ("overheat_repaired_reserve_only",)
        return CandidateBucket.EXCLUDED, ("overheat_return_20d",)

    if features.primary_domain is Domain.EXTERNAL:
        if (
            features.external_industry_strength_rank is None
            or features.external_industry_strength_rank > 3
        ):
            reasons.append("external_industry_not_top3")
        if features.external_industry_sync_count < 2:
            reasons.append("external_industry_sync_insufficient")
        if features.global_base_rank is None or features.global_base_rank > 10:
            reasons.append("external_global_rank_not_top10")
        if not features.has_verifiable_external_evidence:
            reasons.append("external_evidence_missing")
        if reasons:
            return CandidateBucket.EXCLUDED, tuple(reasons)

    return CandidateBucket.ELIGIBLE, ()
