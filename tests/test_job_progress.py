import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from pawe_api.contracts import ManualOutputJobRequest, WeeklySelectionJobRequest
from pawe_api.db import models
from pawe_api.decisions.repository import _selection_reasons
from pawe_api.jobs.repository import (
    SqlJobApplication,
    _append_progress,
    _progress_details,
    _response,
)


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _ScalarSequenceSession:
    def __init__(self, *values: object) -> None:
        self.values = iter(values)
        self.added = False

    def begin(self) -> _Transaction:
        return _Transaction()

    async def scalar(self, _query: object) -> Any:
        return next(self.values)

    def add(self, _value: object) -> None:
        self.added = True


def test_job_progress_keeps_ordered_audit_events() -> None:
    queued_at = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    details = _progress_details(0, "queued", queued_at, "queued")
    details = _append_progress(
        details,
        40,
        "snapshot_gate",
        datetime(2026, 8, 9, 12, 0, 1, tzinfo=UTC),
        "snapshot",
    )

    assert details["progress_percent"] == 40
    events = details["events"]
    assert isinstance(events, list)
    assert [event["stage"] for event in events] == ["queued", "snapshot_gate"]


def test_selection_reasons_are_derived_from_frozen_candidate_audit() -> None:
    candidate = models.Candidate(
        id=uuid.uuid4(),
        week_id=date(2026, 8, 10),
        snapshot_id=uuid.uuid4(),
        stock_id=1,
        rule_score=Decimal("77.5"),
        rank=1,
        bucket="eligible",
        exclusion_reasons=[],
        score_breakdown={"price_structure": 20.0},
    )
    feature = models.WeeklyFeature(
        id=uuid.uuid4(),
        snapshot_id=candidate.snapshot_id,
        stock_id=1,
        feature_version="v9-feature-1",
        payload={
            "primary_sector": "robotics",
            "return_20d": 0.12,
            "above_ma20": True,
            "sector_up_ratio_5d": 0.60,
            "sector_positive_peer_count": 12,
            "avg_amount_20d": 800_000_000.0,
            "data_quality": "verified",
        },
        content_hash="a" * 64,
        created_at=datetime(2026, 8, 9, tzinfo=UTC),
    )

    reasons = _selection_reasons((candidate, feature))

    assert reasons[0] == "V9 硬约束通过，规则总分 77.5。"
    assert any("机器人" in reason for reason in reasons)
    assert reasons[-1] == "行情与特征数据已通过双源验证。"


def test_job_response_preserves_manual_output_type() -> None:
    created_at = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    job = models.Job(
        id=uuid.uuid4(),
        job_type="daily_brief",
        week_id=date(2026, 8, 10),
        status="queued",
        stage="queued",
        idempotency_key="daily-output-001",
        created_by_user_id=None,
        error_code=None,
        error_message=None,
        details=_progress_details(0, "queued", created_at, "queued"),
        created_at=created_at,
        started_at=None,
        finished_at=None,
    )

    assert _response(job).job_type == "daily_brief"


@pytest.mark.asyncio
async def test_completed_weekly_selection_is_not_enqueued_again() -> None:
    completed = _job("weekly_selection")
    session = _ScalarSequenceSession(None, completed)

    response = await SqlJobApplication(session).enqueue_weekly_selection(
        WeeklySelectionJobRequest(
            week_id=date(2026, 8, 10),
            idempotency_key="new-weekly-request",
        ),
        uuid.uuid4(),
    )

    assert response.id == str(completed.id)
    assert session.added is False


@pytest.mark.asyncio
async def test_completed_daily_brief_is_not_enqueued_again() -> None:
    completed = _job("daily_brief")
    session = _ScalarSequenceSession(None, uuid.uuid4(), completed)

    response = await SqlJobApplication(session).enqueue_output_job(
        ManualOutputJobRequest(
            job_type="daily_brief",
            week_id=date(2026, 8, 10),
            trade_date=date(2026, 8, 11),
            idempotency_key="new-daily-request",
        ),
        uuid.uuid4(),
    )

    assert response.id == str(completed.id)
    assert session.added is False


def _job(job_type: str) -> models.Job:
    created_at = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    return models.Job(
        id=uuid.uuid4(),
        job_type=job_type,
        week_id=date(2026, 8, 10),
        status="succeeded",
        stage="daily_brief_ready" if job_type == "daily_brief" else "decision_ready",
        idempotency_key=f"completed-{job_type}",
        created_by_user_id=None,
        error_code=None,
        error_message=None,
        details=_progress_details(100, "completed", created_at, "completed")
        | ({"trade_date": "2026-08-11"} if job_type == "daily_brief" else {}),
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at,
    )
