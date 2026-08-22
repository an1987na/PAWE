from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pawe_api.contracts import DataQuality, MarketState
from pawe_api.data.snapshot import SnapshotRecord, freeze_snapshot
from pawe_api.rules.engine import RULE_VERSION, run_v9_rules
from pawe_api.rules.market_state import MarketStateInput, PoolMetrics
from pawe_api.rules.models import StockStatus
from rule_factory import rule_features


def _snapshot():
    cutoff = datetime(2026, 8, 2, 8, tzinfo=UTC)
    return freeze_snapshot(
        [
            SnapshotRecord(
                source="fixture",
                as_of=cutoff,
                fetched_at=cutoff,
                quality=DataQuality.VERIFIED,
                payload={"fixture": "normal_week"},
            )
        ],
        cutoff=cutoff,
        locked_at=cutoff,
    )


def _state_input(*, coverage: float = 1.0) -> MarketStateInput:
    pool = PoolMetrics(0.08, 0.30, 0.60, 0.02, coverage)
    return MarketStateInput(
        previous_state=MarketState.NORMAL,
        shanghai_close_return=0.01,
        gem_close_return=0.02,
        star50_close_return=0.03,
        main_pool=pool,
        reserve_pool=pool,
        main_average_without_strongest=0.07,
        strong_reserve_positive_close_ratio=0.60,
        qualifying_recovery_sector_count=2,
    )


def _features(count: int = 5):
    return [
        rule_features(
            stock_code=f"00000{index}",
            stock_name=f"样本{index}",
            primary_sector=f"sector{index}",
            return_5d=0.06 - index / 1000,
        )
        for index in range(1, count + 1)
    ]


def test_rule_run_is_auditable_and_stable_across_input_order() -> None:
    inputs = _features()
    first = run_v9_rules(
        snapshot=_snapshot(),
        features=inputs,
        market_state_input=_state_input(),
    )
    second = run_v9_rules(
        snapshot=_snapshot(),
        features=list(reversed(inputs)),
        market_state_input=_state_input(),
    )
    assert first.rule_version == RULE_VERSION
    assert len(first.baseline.items) == 5
    assert first.auto_publish_allowed is True
    assert first.flags == ()
    assert first.fingerprint == second.fingerprint
    assert [item.features.stock_code for item in first.candidates] == [
        item.features.stock_code for item in second.candidates
    ]


def test_rule_run_blocks_auto_publish_when_state_data_is_degraded() -> None:
    result = run_v9_rules(
        snapshot=_snapshot(),
        features=_features(),
        market_state_input=_state_input(coverage=0.79),
    )
    assert result.market_state is MarketState.NORMAL
    assert "STATE_DATA_DEGRADED" in result.flags
    assert result.auto_publish_allowed is False


def test_rule_run_reports_actual_shortage_and_empty_pool() -> None:
    shortage = run_v9_rules(
        snapshot=_snapshot(),
        features=_features(3),
        market_state_input=_state_input(),
    )
    assert len(shortage.baseline.items) == 3
    assert shortage.flags == ("CANDIDATE_SHORTAGE", "LOW_CONFIDENCE")

    excluded = [replace(item, status=StockStatus.ST) for item in _features(2)]
    empty = run_v9_rules(
        snapshot=_snapshot(),
        features=excluded,
        market_state_input=_state_input(),
    )
    assert empty.baseline.items == ()
    assert empty.flags == ("NO_ELIGIBLE_CANDIDATE", "LOW_CONFIDENCE")
    assert empty.auto_publish_allowed is False


def test_rule_run_rejects_duplicate_codes() -> None:
    duplicate = _features(2)
    duplicate[1] = replace(duplicate[1], stock_code=duplicate[0].stock_code)
    with pytest.raises(ValueError, match="unique stock codes"):
        run_v9_rules(
            snapshot=_snapshot(),
            features=duplicate,
            market_state_input=_state_input(),
        )
