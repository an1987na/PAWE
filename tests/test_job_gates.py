import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from pawe_api.contracts import DataQuality
from pawe_api.data.classification_repository import StoredPrimaryClassification
from pawe_api.db import models
from pawe_api.jobs.repository import (
    SqlJobApplication,
    _JobResult,
    validate_feature_classifications,
)
from pawe_api.rules.engine import RuleRunResult
from pawe_api.rules.models import Domain
from rule_factory import rule_features

WEEK_ID = date(2026, 8, 3)


def test_completed_weekly_job_uses_public_success_status() -> None:
    result = _JobResult.completed(
        {},
        uuid.uuid4(),
        cast(
            RuleRunResult,
            SimpleNamespace(candidates=(), baseline=SimpleNamespace(items=()), flags=()),
        ),
        reused=False,
    )

    assert result.status == "succeeded"
    assert result.stage == "decision_ready"


@pytest.mark.asyncio
async def test_weekly_job_stops_when_calendar_is_missing() -> None:
    application = SqlJobApplication(None)  # type: ignore[arg-type]

    failure = await application._evaluate_gates(
        WEEK_ID,
        [],
        datetime(2026, 8, 3, 0, 0, tzinfo=UTC),
    )

    assert failure.stage == "calendar_gate"
    assert failure.code == "CALENDAR_MISSING"


@pytest.mark.asyncio
async def test_current_week_catchup_still_reports_missing_calendar() -> None:
    application = SqlJobApplication(None)  # type: ignore[arg-type]

    failure = await application._evaluate_gates(
        WEEK_ID,
        [],
        datetime(2026, 8, 3, 2, 0, tzinfo=UTC),
    )

    assert failure.stage == "calendar_gate"
    assert failure.code == "CALENDAR_MISSING"


@pytest.mark.asyncio
async def test_weekly_job_catches_up_after_first_open_deadline() -> None:
    class MissingSnapshotSession:
        async def scalar(self, _statement):
            return None

    application = SqlJobApplication(MissingSnapshotSession())  # type: ignore[arg-type]
    calendar = [
        models.TradingCalendar(
            trade_date=WEEK_ID + timedelta(days=offset),
            is_open=True,
            previous_open_date=date(2026, 7, 31),
            source="fixture",
            quality="verified",
            fetched_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
            content_hash=str(offset).zfill(64),
        )
        for offset in range(5)
    ]

    failure = await application._evaluate_gates(
        WEEK_ID,
        calendar,
        datetime(2026, 8, 3, 2, 0, tzinfo=UTC),
    )

    assert failure.stage == "snapshot_gate"
    assert failure.code == "SNAPSHOT_MISSING"
    assert failure.details["supplemental_generation"] is True


@pytest.mark.asyncio
async def test_tuesday_is_entry_when_monday_is_closed() -> None:
    class MissingSnapshotSession:
        async def scalar(self, _statement):
            return None

    application = SqlJobApplication(MissingSnapshotSession())  # type: ignore[arg-type]
    calendar = [
        models.TradingCalendar(
            trade_date=WEEK_ID + timedelta(days=offset),
            is_open=offset > 0,
            previous_open_date=date(2026, 7, 31),
            source="fixture",
            quality="verified",
            fetched_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
            content_hash=str(offset).zfill(64),
        )
        for offset in range(5)
    ]

    failure = await application._evaluate_gates(
        WEEK_ID,
        calendar,
        datetime(2026, 8, 4, 2, 0, tzinfo=UTC),
    )

    assert failure.code == "SNAPSHOT_MISSING"
    assert failure.details["evaluation_entry_date"] == "2026-08-04"


@pytest.mark.asyncio
async def test_weekly_job_becomes_replay_after_next_trading_week_starts() -> None:
    application = SqlJobApplication(None)  # type: ignore[arg-type]
    failure = await application._evaluate_gates(
        WEEK_ID,
        [],
        datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
        formal_end=datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
    )
    assert failure.stage == "publication_gate"
    assert failure.code == "FORMAL_WEEK_ENDED"


@pytest.mark.asyncio
async def test_verified_calendar_advances_to_snapshot_gate() -> None:
    class MissingSnapshotSession:
        async def scalar(self, _statement):
            return None

    application = SqlJobApplication(MissingSnapshotSession())  # type: ignore[arg-type]
    calendar = [
        models.TradingCalendar(
            trade_date=WEEK_ID + timedelta(days=offset),
            is_open=True,
            previous_open_date=date(2026, 7, 31),
            source="sse+szse",
            quality="verified",
            fetched_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
            content_hash=str(offset).zfill(64),
        )
        for offset in range(5)
    ]

    failure = await application._evaluate_gates(
        WEEK_ID,
        calendar,
        datetime(2026, 8, 3, 0, 30, tzinfo=UTC),
    )

    assert failure.stage == "snapshot_gate"
    assert failure.code == "SNAPSHOT_MISSING"


def test_feature_classification_gate_rejects_missing_and_mismatched_domains() -> None:
    feature = rule_features(stock_code="000001", primary_sector="ai")
    missing = validate_feature_classifications([feature], {})
    mismatch = validate_feature_classifications(
        [feature],
        {
            "000001": StoredPrimaryClassification(
                stock_id=1,
                stock_code="000001",
                domain=Domain.MAIN,
                sector_code="robotics",
                quality=DataQuality.VERIFIED,
                valid_from=date(2026, 8, 3),
                valid_to=None,
                published_at=date(2026, 7, 31),
                fetched_at=datetime(2026, 8, 2, tzinfo=UTC),
                content_hash="a" * 64,
            )
        },
    )

    assert missing == ("000001:MISSING_EFFECTIVE_PRIMARY",)
    assert mismatch == ("000001:DOMAIN_OR_SECTOR_MISMATCH",)
