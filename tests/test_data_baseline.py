from pawe_api.contracts import DataQuality, MarketState
from pawe_api.data.baseline import (
    canonical_payload_hash,
    deserialize_market_state_input,
    deserialize_rule_features,
    serialize_market_state_input,
    serialize_rule_features,
)
from pawe_api.rules.market_state import MarketStateInput, PoolMetrics
from pawe_api.rules.models import Board, Domain, RuleFeatures, StateFit, StockStatus


def test_v9_feature_payload_round_trips_and_hashes_deterministically() -> None:
    features = _features()
    payload = serialize_rule_features(features)

    assert payload["board"] == "main"
    assert payload["data_quality"] == "verified"
    assert deserialize_rule_features(payload) == features
    reordered = dict(reversed(payload.items()))
    assert canonical_payload_hash(payload) == canonical_payload_hash(reordered)


def test_market_state_input_round_trips() -> None:
    inputs = MarketStateInput(
        previous_state=MarketState.NORMAL,
        shanghai_close_return=0.01,
        gem_close_return=-0.01,
        star50_close_return=0.0,
        main_pool=PoolMetrics(0.11, 0.4, 0.6, 0.03),
        reserve_pool=PoolMetrics(0.08, 0.2, 0.55, 0.02),
        main_average_without_strongest=0.07,
        strong_reserve_positive_close_ratio=0.6,
        qualifying_recovery_sector_count=2,
    )

    payload = serialize_market_state_input(inputs)

    assert payload["previous_state"] == "NORMAL"
    assert deserialize_market_state_input(payload) == inputs


def _features() -> RuleFeatures:
    return RuleFeatures(
        stock_code="600000",
        stock_name="浦发银行",
        board=Board.MAIN,
        status=StockStatus.ACTIVE,
        primary_domain=Domain.EXTERNAL,
        primary_sector="银行",
        industry_chain_priority=1,
        near_month_unexhausted=True,
        low_crowding_exploration=False,
        high_elasticity_exploration=False,
        high_heat_direction=False,
        external_industry_strength_rank=1,
        external_industry_sync_count=3,
        global_base_rank=1,
        has_verifiable_external_evidence=True,
        listing_trading_days=1000,
        last_trade_suspended=False,
        has_key_market_data=True,
        code_valid=True,
        adjustment_valid=True,
        avg_amount_20d=1_000_000_000,
        return_5d=0.01,
        return_20d=0.03,
        return_60d=0.05,
        distance_high_20d=-0.02,
        volume_activity_5d=1.1,
        volatility_percentile=0.5,
        above_ma20=True,
        amount_anomaly_days=0,
        sector_up_ratio_5d=0.6,
        sector_positive_peer_count=5,
        sector_top20_peer_count=3,
        sector_volume_activity_median=1.0,
        sector_contributor_count=4,
        adjacent_segment_count=2,
        state_fit=StateFit.FULL,
        previous_close_positive=True,
        previous_week_high_return=None,
        previous_touch_drawdown=None,
        strong_reserve_promotion=False,
        previous_target_touched=False,
        has_new_confirmation=True,
        has_direct_catalyst=False,
        financial_not_deteriorating=True,
        independent_evidence_sources=2,
        data_quality=DataQuality.VERIFIED,
        trading_anomaly=False,
        single_anchor_crowded=False,
    )
