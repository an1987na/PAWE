from datetime import UTC, date, datetime

import pytest
from pawe_api.db import models
from pawe_api.replay_stage.calculation import StagedReplayCalculationError
from pawe_api.replay_stage.repair import RepairerResult, ReplayDataRepairService
from pawe_worker.main import _calculate_with_repair


@pytest.mark.asyncio
async def test_repair_is_bounded_point_in_time_and_keeps_real_fetch_time() -> None:
    calls: list[tuple[tuple[str, ...], date, date, int]] = []

    async def repairer(codes: tuple[str, ...], start: date, end: date, retries: int) -> None:
        calls.append((codes, start, end, retries))

    attempted_at = datetime(2026, 8, 19, 12, tzinfo=UTC)
    service = ReplayDataRepairService(repairer=repairer, lookback_days=120, retry_count=2)
    result = await service.repair(
        ["600002", "600001", "600002"],
        as_of=date(2026, 8, 14),
        information_cutoff=datetime(2026, 8, 14, 7, tzinfo=UTC),
        attempted_at=attempted_at,
    )

    assert result.status == "completed"
    assert result.attempted_at == attempted_at
    assert result.warnings == (
        "REPLAY_DATA_REPAIR_RETRIEVED_AFTER_SIMULATED_CUTOFF",
        "REPLAY_DATA_REPAIR_QFQ_VINTAGE_UNVERIFIED",
    )
    assert calls == [
        (("600001", "600002"), date(2026, 4, 16), date(2026, 8, 14), 2)
    ]


@pytest.mark.asyncio
async def test_repair_rejects_future_targets_and_large_scopes_without_provider_call() -> None:
    calls = 0

    async def repairer(codes: tuple[str, ...], start: date, end: date, retries: int) -> None:
        del codes, start, end, retries
        nonlocal calls
        calls += 1

    service = ReplayDataRepairService(repairer=repairer, max_codes=2)
    future = await service.repair(
        ["600001"],
        as_of=date(2026, 8, 15),
        information_cutoff=datetime(2026, 8, 14, 7, tzinfo=UTC),
        attempted_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )
    oversized = await service.repair(
        ["600001", "600002", "600003"],
        as_of=date(2026, 8, 14),
        information_cutoff=datetime(2026, 8, 14, 7, tzinfo=UTC),
        attempted_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )

    assert future.status == "rejected"
    assert oversized.status == "rejected"
    assert calls == 0


@pytest.mark.asyncio
async def test_repair_records_partial_provider_coverage_and_default_lookback() -> None:
    calls: list[tuple[date, date]] = []

    async def repairer(
        codes: tuple[str, ...], start: date, end: date, retries: int
    ) -> RepairerResult:
        del codes, retries
        calls.append((start, end))
        return RepairerResult(succeeded_codes=("600001",))

    result = await ReplayDataRepairService(repairer=repairer).repair(
        ["600001", "600002"],
        as_of=date(2026, 8, 14),
        information_cutoff=datetime(2026, 8, 14, 7, tzinfo=UTC),
        attempted_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )

    assert result.status == "partial"
    assert result.failed_codes == ("600002",)
    assert calls == [(date(2026, 3, 7), date(2026, 8, 14))]


@pytest.mark.asyncio
async def test_replay_stage_retries_once_after_repair_and_records_attempt() -> None:
    stage = models.ReplayStageRun(
        id=__import__("uuid").uuid4(),
        replay_run_id=__import__("uuid").uuid4(),
        stage="daily_brief",
        trade_date=date(2026, 8, 14),
        status="running",
        information_cutoff=datetime(2026, 8, 14, 7, tzinfo=UTC),
        actual_run_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
        input_fingerprint="input",
        warnings=[],
        details={},
        created_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )
    calls = 0

    async def repairer(codes: tuple[str, ...], start: date, end: date, retries: int) -> None:
        del codes, start, end, retries

    async def calculate() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise StagedReplayCalculationError(
                "missing",
                coverage={"missing_codes": ["600001"]},
            )
        return "recovered"

    result = await _calculate_with_repair(
        stage,
        datetime(2026, 8, 19, 12, tzinfo=UTC),
        ReplayDataRepairService(repairer=repairer),
        calculate,
    )

    assert result == "recovered"
    assert calls == 2
    assert stage.details["repair_attempts"] == 1
    assert stage.details["data_repair"]["status"] == "completed"
