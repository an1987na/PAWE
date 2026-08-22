import math
import re
from collections import Counter
from dataclasses import replace
from datetime import date
from typing import Any

from pawe_api.contracts import DataQuality, MarketState
from pawe_api.rules.eligibility import evaluate_eligibility
from pawe_api.rules.market_state import MarketStateInput, PoolMetrics
from pawe_api.rules.models import (
    Board,
    CandidateBucket,
    Domain,
    RuleFeatures,
    StateFit,
    StockStatus,
)
from pawe_api.rules.scoring import score_candidate

_STOCK_CODE = re.compile(r"^\d{6}$")


def build_rule_features(
    rows: list[tuple[int, str, str, str, str, date | None, dict[str, Any], DataQuality]],
) -> list[tuple[int, RuleFeatures]]:
    """Build conservative V9 inputs from classified frozen-market records.

    The snapshot currently proves technical, classification and sector-market facts. Fields
    requiring announcements, financial statements or prior published decisions receive no
    points until an evidence pipeline supplies them.
    """
    initial = [
        (stock_id, _base_feature(code, name, board, status, listing_date, payload, quality))
        for stock_id, code, name, board, status, listing_date, payload, quality in rows
    ]
    eligible = [
        feature
        for _, feature in initial
        if evaluate_eligibility(feature)[0] is CandidateBucket.ELIGIBLE
    ]
    positive_by_sector = Counter(
        feature.primary_sector for feature in eligible if feature.return_5d > 0
    )
    with_peers = [
        (
            stock_id,
            replace(
                feature,
                sector_positive_peer_count=positive_by_sector[feature.primary_sector],
                sector_contributor_count=positive_by_sector[feature.primary_sector],
                single_anchor_crowded=positive_by_sector[feature.primary_sector] < 2,
            ),
        )
        for stock_id, feature in initial
    ]
    eligible_with_peers = [
        feature
        for _, feature in with_peers
        if evaluate_eligibility(feature)[0] is CandidateBucket.ELIGIBLE
    ]
    top_count = math.ceil(len(eligible_with_peers) * 0.20)
    top_codes = {
        item.features.stock_code
        for item in sorted(
            (score_candidate(feature) for feature in eligible_with_peers),
            key=lambda item: (-item.rule_score, item.features.stock_code),
        )[:top_count]
    }
    top_by_sector = Counter(
        feature.primary_sector
        for feature in eligible_with_peers
        if feature.stock_code in top_codes
    )
    return [
        (
            stock_id,
            replace(
                feature,
                sector_top20_peer_count=top_by_sector[feature.primary_sector],
            ),
        )
        for stock_id, feature in with_peers
    ]


def build_degraded_market_state_input(previous_state: MarketState) -> MarketStateInput:
    """Represent the absence of a prior evaluated week without inventing performance."""
    unavailable = PoolMetrics(
        average_week_high_return=0.0,
        touch_rate_10=0.0,
        positive_close_ratio=0.0,
        median_close_return=0.0,
        coverage_ratio=0.0,
    )
    return MarketStateInput(
        previous_state=previous_state,
        shanghai_close_return=None,
        gem_close_return=None,
        star50_close_return=None,
        main_pool=unavailable,
        reserve_pool=unavailable,
        main_average_without_strongest=0.0,
        strong_reserve_positive_close_ratio=0.0,
        qualifying_recovery_sector_count=0,
    )


def _base_feature(
    code: str,
    name: str,
    board: str,
    status: str,
    listing_date: date | None,
    payload: dict[str, Any],
    quality: DataQuality,
) -> RuleFeatures:
    technical = _mapping(payload, "technical")
    classification = _mapping(payload, "classification")
    sector = _mapping(payload, "sector_market")
    verification = _mapping(payload, "verification")
    source_bars = _mapping(payload, "source_bars")
    trading_days = max(
        (len(value) for value in source_bars.values() if isinstance(value, list)),
        default=0,
    )
    latest_volume = _latest_volume(source_bars)
    warnings = verification.get("warnings", [])
    warning_values = warnings if isinstance(warnings, list) else []
    technical_as_of = date.fromisoformat(_string(technical, "as_of"))
    listing_days_proven = trading_days
    if listing_date is not None and listing_date > technical_as_of:
        listing_days_proven = 0
    return RuleFeatures(
        stock_code=code,
        stock_name=name,
        board=Board(board),
        status=StockStatus(status),
        primary_domain=Domain(_string(classification, "domain")),
        primary_sector=_string(classification, "sector_code"),
        industry_chain_priority=0,
        near_month_unexhausted=False,
        low_crowding_exploration=False,
        high_elasticity_exploration=False,
        high_heat_direction=False,
        external_industry_strength_rank=None,
        external_industry_sync_count=0,
        global_base_rank=None,
        has_verifiable_external_evidence=False,
        listing_trading_days=listing_days_proven,
        last_trade_suspended=latest_volume is None or latest_volume <= 0,
        has_key_market_data=_has_key_market_data(technical),
        code_valid=_code_matches_board(code, board),
        adjustment_valid=_adjustment_valid(source_bars),
        avg_amount_20d=_number(technical, "avg_amount_20d"),
        return_5d=_number(technical, "return_5d"),
        return_20d=_number(technical, "return_20d"),
        return_60d=_number(technical, "return_60d"),
        distance_high_20d=_number(technical, "distance_high_20d"),
        volume_activity_5d=_number(technical, "volume_activity_5d"),
        volatility_percentile=_number(payload, "volatility_percentile"),
        above_ma20=_boolean(technical, "above_ma20"),
        amount_anomaly_days=_integer(technical, "amount_anomaly_days"),
        sector_up_ratio_5d=_number(sector, "up_ratio_5d"),
        sector_positive_peer_count=0,
        sector_top20_peer_count=0,
        sector_volume_activity_median=_number(sector, "volume_activity_median"),
        sector_contributor_count=0,
        adjacent_segment_count=0,
        state_fit=StateFit.NEUTRAL,
        previous_close_positive=False,
        previous_week_high_return=None,
        previous_touch_drawdown=None,
        strong_reserve_promotion=False,
        previous_target_touched=False,
        has_new_confirmation=False,
        has_direct_catalyst=False,
        financial_not_deteriorating=False,
        independent_evidence_sources=0,
        data_quality=quality,
        trading_anomaly=bool(warning_values),
        single_anchor_crowded=True,
    )


def _has_key_market_data(payload: dict[str, Any]) -> bool:
    keys = (
        "avg_amount_20d",
        "return_5d",
        "return_20d",
        "return_60d",
        "distance_high_20d",
        "volume_activity_5d",
    )
    try:
        return all(math.isfinite(_number(payload, key)) for key in keys)
    except (TypeError, ValueError):
        return False


def _code_matches_board(code: str, board: str) -> bool:
    if not _STOCK_CODE.fullmatch(code):
        return False
    if board == Board.MAIN.value:
        return code.startswith(("000", "001", "002", "003", "600", "601", "603", "605"))
    if board == Board.GEM.value:
        return code.startswith(("300", "301"))
    if board == Board.STAR.value:
        return code.startswith("688")
    return False


def _adjustment_valid(source_bars: dict[str, Any]) -> bool:
    bars = [bar for value in source_bars.values() if isinstance(value, list) for bar in value]
    return bool(bars) and all(
        isinstance(bar, dict) and bar.get("adjustment") == "qfq" for bar in bars
    )


def _latest_volume(source_bars: dict[str, Any]) -> float | None:
    candidates: list[tuple[str, float]] = []
    for value in source_bars.values():
        if not isinstance(value, list):
            continue
        for bar in value:
            if not isinstance(bar, dict):
                continue
            trade_date = bar.get("trade_date")
            volume = bar.get("volume")
            if isinstance(trade_date, str) and isinstance(volume, (str, int, float)):
                candidates.append((trade_date, float(volume)))
    return max(candidates)[1] if candidates else None


def _mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{key} must be numeric")
    return float(value)


def _integer(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _boolean(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value
