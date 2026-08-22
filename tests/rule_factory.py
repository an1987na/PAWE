from dataclasses import replace

from pawe_api.contracts import DataQuality
from pawe_api.rules.models import (
    Board,
    CandidateBucket,
    Domain,
    RuleFeatures,
    ScoredCandidate,
    StateFit,
    StockStatus,
)


def rule_features(**changes: object) -> RuleFeatures:
    base = RuleFeatures(
        stock_code="000001",
        stock_name="平安银行",
        board=Board.MAIN,
        status=StockStatus.ACTIVE,
        primary_domain=Domain.MAIN,
        primary_sector="ai_server",
        industry_chain_priority=2,
        near_month_unexhausted=True,
        low_crowding_exploration=False,
        high_elasticity_exploration=False,
        high_heat_direction=False,
        external_industry_strength_rank=None,
        external_industry_sync_count=0,
        global_base_rank=None,
        has_verifiable_external_evidence=False,
        listing_trading_days=1000,
        last_trade_suspended=False,
        has_key_market_data=True,
        code_valid=True,
        adjustment_valid=True,
        avg_amount_20d=1_000_000_000,
        return_5d=0.06,
        return_20d=0.20,
        return_60d=0.30,
        distance_high_20d=-0.12,
        volume_activity_5d=1.20,
        volatility_percentile=0.50,
        above_ma20=True,
        amount_anomaly_days=0,
        sector_up_ratio_5d=0.70,
        sector_positive_peer_count=3,
        sector_top20_peer_count=2,
        sector_volume_activity_median=1.20,
        sector_contributor_count=3,
        adjacent_segment_count=2,
        state_fit=StateFit.FULL,
        previous_close_positive=True,
        previous_week_high_return=0.10,
        previous_touch_drawdown=-0.04,
        strong_reserve_promotion=False,
        previous_target_touched=False,
        has_new_confirmation=True,
        has_direct_catalyst=True,
        financial_not_deteriorating=True,
        independent_evidence_sources=2,
        data_quality=DataQuality.VERIFIED,
        trading_anomaly=False,
        single_anchor_crowded=False,
    )
    return replace(base, **changes)


def scored_candidate(
    code: str,
    score: float,
    *,
    domain: Domain = Domain.MAIN,
    sector: str = "ai_server",
    bucket: CandidateBucket = CandidateBucket.ELIGIBLE,
) -> ScoredCandidate:
    return ScoredCandidate(
        features=rule_features(
            stock_code=code,
            stock_name=f"样本{code}",
            primary_domain=domain,
            primary_sector=sector,
        ),
        rule_score=score,
        bucket=bucket,
        exclusion_reasons=(),
        score_breakdown={},
    )
