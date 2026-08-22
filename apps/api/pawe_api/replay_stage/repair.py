"""Bounded, point-in-time data repair for staged historical replays.

Replay calculations may be run long after the simulated information boundary.
This module makes that distinction explicit: bars are fetched now (and retain
their real ``fetched_at``), but only bars whose trade date is at or before the
stage boundary may be consumed by the retry.  The repair is deliberately
bounded to a small set of missing symbols so a replay cannot silently turn
into a full-market ingestion job.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pawe_api.data.calendar import SHANGHAI


@dataclass(frozen=True, slots=True)
class RepairerResult:
    succeeded_codes: tuple[str, ...]
    failed_codes: tuple[str, ...] = ()


type Repairer = Callable[
    [tuple[str, ...], date, date, int], Awaitable[RepairerResult | None]
]

REPAIR_MAX_CODES = 400
REPAIR_LOOKBACK_DAYS = 160
REPAIR_WARNING = "REPLAY_DATA_REPAIR_RETRIEVED_AFTER_SIMULATED_CUTOFF"
QFQ_WARNING = "REPLAY_DATA_REPAIR_QFQ_VINTAGE_UNVERIFIED"


@dataclass(frozen=True, slots=True)
class RepairResult:
    status: str
    codes: tuple[str, ...]
    start: date | None
    end: date | None
    attempted_at: datetime
    retry_count: int
    warnings: tuple[str, ...]
    reason: str | None = None
    succeeded_codes: tuple[str, ...] = ()
    failed_codes: tuple[str, ...] = ()

    @property
    def attempted(self) -> bool:
        return self.status in {"completed", "partial", "failed"}

    def as_details(self) -> dict[str, object]:
        return {
            "status": self.status,
            "codes": list(self.codes),
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "attempted_at": self.attempted_at.isoformat(),
            "retry_count": self.retry_count,
            "warnings": list(self.warnings),
            "succeeded_codes": list(self.succeeded_codes),
            "failed_codes": list(self.failed_codes),
            "reason": self.reason,
        }


class ReplayDataRepairService:
    """Perform at most one bounded repair attempt for a replay stage."""

    def __init__(
        self,
        *,
        repairer: Repairer | None = None,
        max_codes: int = REPAIR_MAX_CODES,
        lookback_days: int = REPAIR_LOOKBACK_DAYS,
        retry_count: int = 2,
    ) -> None:
        if max_codes <= 0 or lookback_days <= 0 or retry_count < 0:
            raise ValueError("replay repair policy is invalid")
        self._repairer = repairer or _default_repairer
        self._max_codes = max_codes
        self._lookback_days = lookback_days
        self._retry_count = retry_count

    async def repair(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        information_cutoff: datetime,
        attempted_at: datetime,
    ) -> RepairResult:
        if attempted_at.tzinfo is None or attempted_at.utcoffset() is None:
            raise ValueError("attempted_at must be timezone-aware")
        if information_cutoff.tzinfo is None or information_cutoff.utcoffset() is None:
            raise ValueError("information_cutoff must be timezone-aware")
        normalized = tuple(sorted({code for code in codes if code}))
        now = attempted_at.astimezone(SHANGHAI)
        cutoff_day = information_cutoff.astimezone(SHANGHAI).date()
        if as_of > cutoff_day:
            return RepairResult(
                "rejected",
                normalized,
                None,
                None,
                attempted_at,
                self._retry_count,
                ("REPLAY_DATA_REPAIR_FUTURE_TARGET_REJECTED",),
                "repair target is after the simulated information cutoff",
            )
        if not normalized:
            return RepairResult(
                "not_needed",
                (),
                None,
                None,
                attempted_at,
                self._retry_count,
                (),
            )
        if len(normalized) > self._max_codes:
            return RepairResult(
                "rejected",
                normalized,
                None,
                as_of,
                attempted_at,
                self._retry_count,
                ("REPLAY_DATA_REPAIR_SCOPE_EXCEEDED",),
                f"{len(normalized)} missing codes exceeds limit {self._max_codes}",
            )
        start = as_of - timedelta(days=self._lookback_days)
        warnings: tuple[str, ...] = (REPAIR_WARNING, QFQ_WARNING)
        try:
            provider_result = await self._repairer(
                normalized, start, as_of, self._retry_count
            )
        except Exception as exc:
            return RepairResult(
                "failed",
                normalized,
                start,
                as_of,
                now.astimezone(attempted_at.tzinfo),
                self._retry_count,
                warnings + (f"REPLAY_DATA_REPAIR_FAILED:{type(exc).__name__}",),
                "provider repair failed",
            )
        succeeded = (
            tuple(sorted(set(provider_result.succeeded_codes)))
            if provider_result is not None
            else normalized
        )
        failed = (
            tuple(sorted(set(provider_result.failed_codes)))
            if provider_result is not None
            else ()
        )
        if provider_result is not None:
            unreported = set(normalized) - set(succeeded) - set(failed)
            failed = tuple(sorted((*failed, *unreported)))
        status = "completed" if not failed else "partial" if succeeded else "failed"
        if failed:
            warnings += ("REPLAY_DATA_REPAIR_PARTIAL",)
        return RepairResult(
            status,
            normalized,
            start,
            as_of,
            now.astimezone(attempted_at.tzinfo),
            self._retry_count,
            warnings,
            succeeded_codes=succeeded,
            failed_codes=failed,
        )


async def _default_repairer(
    codes: tuple[str, ...],
    start: date,
    end: date,
    retry_count: int,
) -> RepairerResult:
    """Use the existing primary/backup/fallback ingestion path.

    The import is lazy to keep the API process free of a network side effect;
    only the worker invokes this service.  ``ingest`` persists provider rows
    idempotently and retains each provider's actual fetch time.
    """

    from scripts.ingest_daily_bars import ingest

    result = await ingest(
        start,
        end,
        codes=codes,
        limit=None,
        after_code=None,
        checkpoint=None,
        checkpoint_path=None,
        v9_available_on=None,
        published_by=None,
        allow_sina_fallback=True,
        provider_timeout_seconds=8,
        provider_retry_count=retry_count,
    )
    if result is None:
        raise RuntimeError("daily ingestion did not return a repair result")
    return RepairerResult(result.succeeded_codes, result.failed_codes)
