from dataclasses import replace

import pytest
from pawe_api.contracts import DataQuality, MarketState
from pawe_api.rules.eligibility import evaluate_eligibility
from pawe_api.rules.market_state import (
    MarketStateInput,
    PoolMetrics,
    determine_market_state,
)
from pawe_api.rules.models import Board, CandidateBucket, Domain, StockStatus
from pawe_api.rules.portfolio import build_portfolio
from pawe_api.rules.scoring import score_candidate
from rule_factory import rule_features, scored_candidate


def test_hard_constraints_exclude_st_and_low_liquidity() -> None:
    bucket, reasons = evaluate_eligibility(
        rule_features(status=StockStatus.ST, avg_amount_20d=50_000_000)
    )
    assert bucket is CandidateBucket.EXCLUDED
    assert "stock_status:st" in reasons
    assert "liquidity_below_threshold" in reasons


def test_star_is_reference_when_main_pool_is_disabled() -> None:
    bucket, reasons = evaluate_eligibility(rule_features(board=Board.STAR))
    assert bucket is CandidateBucket.STAR_REFERENCE
    assert reasons == ("star_board_main_pool_disabled",)


def test_overheat_repair_is_reserve_only() -> None:
    bucket, _ = evaluate_eligibility(
        rule_features(return_20d=0.45, return_5d=-0.09, distance_high_20d=-0.16)
    )
    assert bucket is CandidateBucket.HIGH_VOLATILITY_RESERVE


def test_external_candidate_must_pass_exploration_thresholds() -> None:
    rejected, reasons = evaluate_eligibility(rule_features(primary_domain=Domain.EXTERNAL))
    assert rejected is CandidateBucket.EXCLUDED
    assert "external_industry_not_top3" in reasons

    accepted, accepted_reasons = evaluate_eligibility(
        rule_features(
            primary_domain=Domain.EXTERNAL,
            external_industry_strength_rank=3,
            external_industry_sync_count=2,
            global_base_rank=10,
            has_verifiable_external_evidence=True,
        )
    )
    assert accepted is CandidateBucket.ELIGIBLE
    assert accepted_reasons == ()


def test_scoring_is_bounded_and_explainable() -> None:
    candidate = score_candidate(rule_features())
    assert 0 <= candidate.rule_score <= 100
    assert candidate.rule_score == pytest.approx(sum(candidate.score_breakdown.values()), abs=0.001)
    assert set(candidate.score_breakdown) == {
        "price_structure",
        "sector_strength",
        "liquidity",
        "market_fit",
        "history",
        "fundamentals",
        "risk_quality",
    }


def test_portfolio_publishes_actual_count_without_filling() -> None:
    candidates = [
        scored_candidate("000001", 90, sector="ai_server"),
        scored_candidate("000002", 89, sector="ai_server"),
        scored_candidate("000003", 88, sector="robotics"),
        scored_candidate("000004", 87, sector="semiconductor"),
    ]
    result = build_portfolio(candidates)
    assert len(result.items) == 4
    assert result.shortage is True
    assert result.low_confidence is True


def test_portfolio_preserves_main_domain_quota_and_external_cap() -> None:
    candidates = [
        scored_candidate("000010", 99, domain=Domain.EXTERNAL, sector="bank"),
        scored_candidate("000011", 98, domain=Domain.EXTERNAL, sector="consumer"),
        scored_candidate("000012", 97, domain=Domain.SUPPLEMENTARY, sector="medicine"),
        scored_candidate("000001", 90, sector="ai"),
        scored_candidate("000002", 89, sector="robotics"),
        scored_candidate("000003", 88, sector="semiconductor"),
        scored_candidate("000004", 87, sector="energy"),
    ]
    result = build_portfolio(candidates)
    assert len(result.items) == 5
    assert sum(item.features.primary_domain is Domain.MAIN for item in result.items) >= 3
    assert sum(item.features.primary_domain is Domain.EXTERNAL for item in result.items) <= 1


def test_portfolio_limits_each_sector_to_two() -> None:
    candidates = [
        scored_candidate(f"00000{index}", 100 - index, sector="ai_server") for index in range(1, 6)
    ] + [
        scored_candidate("000006", 80, sector="robotics"),
        scored_candidate("000007", 79, sector="semiconductor"),
        scored_candidate("000008", 78, sector="energy"),
    ]
    result = build_portfolio(candidates)
    assert len(result.items) == 5
    assert sum(item.features.primary_sector == "ai_server" for item in result.items) == 2


def test_overheated_universe_requires_two_unexhausted_items() -> None:
    candidates = [
        replace(
            scored_candidate(f"00000{index}", 100 - index, sector=f"sector{index}"),
            features=rule_features(
                stock_code=f"00000{index}",
                primary_sector=f"sector{index}",
                near_month_unexhausted=index >= 4,
            ),
        )
        for index in range(1, 7)
    ]
    result = build_portfolio(candidates, candidate_overheat_ratio=0.60)
    assert len(result.items) == 5
    assert sum(item.features.near_month_unexhausted for item in result.items) >= 2


def test_breadth_recovery_reserves_two_promotions_and_one_exploration() -> None:
    candidates = [
        replace(
            scored_candidate("000001", 99, sector="ai"),
            features=rule_features(
                stock_code="000001", primary_sector="ai", strong_reserve_promotion=True
            ),
        ),
        replace(
            scored_candidate("000002", 98, sector="semi"),
            features=rule_features(
                stock_code="000002", primary_sector="semi", strong_reserve_promotion=True
            ),
        ),
        replace(
            scored_candidate("000003", 97, sector="robot"),
            features=rule_features(
                stock_code="000003", primary_sector="robot", low_crowding_exploration=True
            ),
        ),
        scored_candidate("000004", 96, sector="energy"),
        scored_candidate("000005", 95, sector="medicine"),
    ]
    result = build_portfolio(candidates, market_state=MarketState.BREADTH_RECOVERY)
    assert len(result.items) == 5
    assert sum(item.features.strong_reserve_promotion for item in result.items) == 2
    assert sum(item.features.low_crowding_exploration for item in result.items) >= 1


def _pool(
    high: float,
    touch: float,
    positive: float,
    median_close: float,
    coverage: float = 1.0,
) -> PoolMetrics:
    return PoolMetrics(high, touch, positive, median_close, coverage)


def _state_input(**changes: object) -> MarketStateInput:
    base = MarketStateInput(
        previous_state=MarketState.NORMAL,
        shanghai_close_return=0.01,
        gem_close_return=0.02,
        star50_close_return=0.03,
        main_pool=_pool(0.12, 0.60, 0.60, 0.03),
        reserve_pool=_pool(0.11, 0.40, 0.60, 0.02),
        main_average_without_strongest=0.11,
        strong_reserve_positive_close_ratio=0.60,
        qualifying_recovery_sector_count=3,
        previous_retreat_main_median_close=-0.04,
        previous_retreat_reserve_median_close=-0.03,
    )
    return replace(base, **changes)


def test_systemic_retreat_has_highest_priority() -> None:
    decision = determine_market_state(
        _state_input(
            shanghai_close_return=-0.01,
            gem_close_return=-0.09,
            star50_close_return=-0.05,
            main_pool=_pool(0.05, 0.0, 0.40, -0.03),
            reserve_pool=_pool(0.06, 0.0, 0.40, -0.02),
        )
    )
    assert decision.state is MarketState.SYSTEMIC_RETREAT


def test_breadth_recovery_requires_three_sectors() -> None:
    decision = determine_market_state(
        _state_input(
            previous_state=MarketState.SYSTEMIC_RETREAT,
            main_pool=_pool(0.07, 0.20, 0.40, -0.01),
            reserve_pool=_pool(0.09, 0.20, 0.60, 0.01),
        )
    )
    assert decision.state is MarketState.BREADTH_RECOVERY


def test_recovery_confirmation_uses_retreat_improvement() -> None:
    decision = determine_market_state(_state_input(previous_state=MarketState.BREADTH_RECOVERY))
    assert decision.state is MarketState.RECOVERY_CONFIRMED


def test_anchor_distortion_uses_precomputed_ex_anchor_mean() -> None:
    decision = determine_market_state(
        _state_input(
            main_pool=_pool(0.12, 0.40, 0.60, 0.01),
            main_average_without_strongest=0.08,
        )
    )
    assert decision.state is MarketState.ANCHOR_DISTORTED


def test_state_data_gap_retains_previous_state() -> None:
    decision = determine_market_state(
        _state_input(
            previous_state=MarketState.SYSTEMIC_RETREAT,
            star50_close_return=None,
            main_pool=_pool(0.05, 0.0, 0.40, -0.03, coverage=0.70),
        )
    )
    assert decision.state is MarketState.SYSTEMIC_RETREAT
    assert decision.flags == ("STATE_DATA_DEGRADED",)


def test_conflicted_data_never_becomes_eligible() -> None:
    candidate = score_candidate(rule_features(data_quality=DataQuality.CONFLICTED))
    assert candidate.bucket is CandidateBucket.EXCLUDED
