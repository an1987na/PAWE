import math

from pawe_api.contracts import DataQuality
from pawe_api.rules.eligibility import (
    DEFAULT_HARD_CONSTRAINTS,
    HardConstraintConfig,
    evaluate_eligibility,
)
from pawe_api.rules.models import RuleFeatures, ScoredCandidate, StateFit


def score_candidate(
    features: RuleFeatures,
    config: HardConstraintConfig = DEFAULT_HARD_CONSTRAINTS,
) -> ScoredCandidate:
    bucket, reasons = evaluate_eligibility(features, config)
    breakdown = {
        "price_structure": _price_structure(features),
        "sector_strength": _sector_strength(features),
        "liquidity": _liquidity(features),
        "market_fit": _market_fit(features.state_fit),
        "history": _history(features),
        "fundamentals": _fundamentals(features),
        "risk_quality": _risk_quality(features),
    }
    total = round(sum(breakdown.values()), 4)
    return ScoredCandidate(
        features=features,
        rule_score=total,
        bucket=bucket,
        exclusion_reasons=reasons,
        score_breakdown={key: round(value, 4) for key, value in breakdown.items()},
    )


def _price_structure(features: RuleFeatures) -> float:
    return_20 = _piecewise(
        features.return_20d,
        [(-0.20, 0.3), (0.0, 1.0), (0.35, 1.0), (0.40, 0.0)],
    )
    high_space = _piecewise(
        features.distance_high_20d,
        [(-0.30, 0.4), (-0.20, 1.0), (-0.07, 1.0), (-0.03, 0.3)],
    )
    return_5 = _piecewise(
        features.return_5d,
        [(-0.08, 0.2), (0.0, 0.5), (0.12, 1.0), (0.20, 0.0)],
    )
    stability = 0.0
    if features.return_5d >= features.return_20d / 4:
        stability += 2
    if features.return_20d >= features.return_60d / 3:
        stability += 2
    if features.above_ma20:
        stability += 1
    return 8 * return_20 + 7 * high_space + 5 * return_5 + stability


def _sector_strength(features: RuleFeatures) -> float:
    breadth = 8 * _linear(features.sector_up_ratio_5d, 0.30, 0.70)
    peers = 3.0 if features.sector_positive_peer_count >= 2 else 0.0
    if features.sector_top20_peer_count >= 2:
        peers += 2.0
    volume = 4 * _linear(features.sector_volume_activity_median, 0.8, 1.2)
    diffusion = 2.0 if features.sector_contributor_count >= 2 else 0.0
    if features.adjacent_segment_count >= 2:
        diffusion += 1.0
    return breadth + peers + volume + diffusion


def _liquidity(features: RuleFeatures) -> float:
    amount = 0.0
    if features.avg_amount_20d >= 100_000_000:
        amount = 6 * min(1.0, math.log10(features.avg_amount_20d / 100_000_000))
    activity = features.volume_activity_5d
    if activity <= 0.7:
        activity_score = 0.0
    elif activity <= 1.2:
        activity_score = 6 * _linear(activity, 0.7, 1.2)
    elif activity < 2.5:
        activity_score = 6 * (1 - 0.5 * _linear(activity, 1.2, 2.5))
    else:
        activity_score = 3.0
    stability = 3 * max(0.0, 1 - features.amount_anomaly_days / 20)
    return amount + activity_score + stability


def _market_fit(state_fit: StateFit) -> float:
    return {
        StateFit.FULL: 15.0,
        StateFit.SECONDARY: 10.0,
        StateFit.NEUTRAL: 7.0,
        StateFit.INCOMPATIBLE: 0.0,
    }[state_fit]


def _history(features: RuleFeatures) -> float:
    score = 3.0 if features.previous_close_positive else 0.0
    if features.previous_week_high_return is not None:
        if features.previous_week_high_return >= 0.10:
            score += 3.0
        elif features.previous_week_high_return >= 0.08:
            score += 2.0
    if features.previous_touch_drawdown is not None and features.previous_touch_drawdown >= -0.08:
        score += 2.0
    if features.strong_reserve_promotion:
        score += 2.0
    if features.previous_target_touched and not features.has_new_confirmation:
        score *= 0.4
    return min(10.0, score)


def _fundamentals(features: RuleFeatures) -> float:
    score = 4.0 if features.has_direct_catalyst else 0.0
    if features.financial_not_deteriorating:
        score += 3.0
    if features.independent_evidence_sources >= 2:
        score += 3.0
    return score


def _risk_quality(features: RuleFeatures) -> float:
    score = {
        DataQuality.VERIFIED: 2.0,
        DataQuality.SINGLE_SOURCE: 1.0,
        DataQuality.DEGRADED: 0.0,
        DataQuality.CONFLICTED: 0.0,
        DataQuality.MISSING: 0.0,
    }[features.data_quality]
    if 0.20 <= features.volatility_percentile <= 0.80:
        score += 1.0
    if not features.trading_anomaly:
        score += 1.0
    if not features.single_anchor_crowded:
        score += 1.0
    return score


def _linear(value: float, low: float, high: float) -> float:
    if high <= low:
        raise ValueError("high must be greater than low")
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _piecewise(value: float, points: list[tuple[float, float]]) -> float:
    if value < points[0][0] or value > points[-1][0]:
        return 0.0
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:], strict=False):
        if left_x <= value <= right_x:
            if left_x == right_x:
                return right_y
            ratio = (value - left_x) / (right_x - left_x)
            return left_y + ratio * (right_y - left_y)
    return 0.0
