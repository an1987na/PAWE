from datetime import date

import pytest
from pawe_api.contracts import (
    Confidence,
    DecisionItem,
    MarketState,
    WeeklyStatus,
    WeekSummary,
)
from pydantic import ValidationError


def _item(code: str, rank: int) -> DecisionItem:
    return DecisionItem(
        stock_code=code,
        stock_name=f"样本{rank}",
        rank=rank,
        confidence=Confidence.MEDIUM,
        summary="确定性样例",
        primary_risk="仅用于测试",
    )


def test_shortage_accepts_actual_published_capacity() -> None:
    summary = WeekSummary(
        week_id=date(2026, 8, 3),
        status=WeeklyStatus.PUBLISHED,
        market_state=MarketState.NORMAL,
        decision_version=1,
        confidence=Confidence.LOW,
        shortage=True,
        shortage_reason="只有三个候选满足全部约束",
        items=[_item("000001", 1), _item("000002", 2), _item("000003", 3)],
    )
    assert len(summary.items) == 3


def test_shortage_flag_must_match_capacity() -> None:
    with pytest.raises(ValidationError, match="shortage must match"):
        WeekSummary(
            week_id=date(2026, 8, 3),
            status=WeeklyStatus.PUBLISHED,
            market_state=MarketState.NORMAL,
            decision_version=1,
            confidence=Confidence.MEDIUM,
            shortage=False,
            items=[_item("000001", 1)],
        )


def test_decision_codes_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="unique stock codes"):
        WeekSummary(
            week_id=date(2026, 8, 3),
            status=WeeklyStatus.PUBLISHED,
            market_state=MarketState.NORMAL,
            decision_version=1,
            confidence=Confidence.LOW,
            shortage=True,
            shortage_reason="样例",
            items=[_item("000001", 1), _item("000001", 2)],
        )
