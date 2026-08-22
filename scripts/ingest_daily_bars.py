import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
from pawe_api.data.checkpoint import (
    DailyIngestionCheckpoint,
    load_daily_checkpoint,
    save_daily_checkpoint,
)
from pawe_api.data.classification import PRIMARY_CLASSIFICATION_TYPE, PRIMARY_SOURCE
from pawe_api.data.providers import (
    DailyProviderError,
    EastmoneyDailyProvider,
    ProviderPolicy,
    SinaDailyProvider,
    TencentDailyProvider,
)
from pawe_api.data.repository import SqlDataBaselineRepository
from pawe_api.data.series import (
    ProviderDailySeries,
    merge_provider_daily_series,
    reconcile_daily_series_with_amount_fallback,
)
from pawe_api.db import models
from pawe_api.db.session import SessionFactory
from sqlalchemy import func, select


@dataclass(frozen=True, slots=True)
class DailyIngestionResult:
    requested_codes: tuple[str, ...]
    succeeded_codes: tuple[str, ...]
    failed_codes: tuple[str, ...]
    inserted: int
    unchanged: int
    quality_counts: dict[str, int]


async def ingest(
    start: date,
    end: date,
    *,
    codes: tuple[str, ...],
    limit: int | None,
    after_code: str | None,
    checkpoint: DailyIngestionCheckpoint | None,
    checkpoint_path: Path | None,
    v9_available_on: date | None,
    published_by: date | None,
    allow_sina_fallback: bool = True,
    provider_timeout_seconds: float = 10,
    provider_retry_count: int = 2,
) -> DailyIngestionResult:
    if start > end:
        raise ValueError("start cannot exceed end")
    if provider_timeout_seconds <= 0 or provider_retry_count < 0:
        raise ValueError("provider timeout and retry policy are invalid")
    fetched_by = datetime.now(UTC)
    stocks = await _load_stocks(
        codes,
        limit,
        after_code=after_code,
        v9_available_on=v9_available_on,
        published_by=published_by,
        fetched_by=fetched_by,
    )
    policy = ProviderPolicy(
        timeout_seconds=provider_timeout_seconds,
        retry_count=provider_retry_count,
        min_interval_seconds=1,
    )
    inserted = 0
    unchanged = 0
    failed: list[str] = []
    succeeded: list[str] = []
    quality_counts: dict[str, int] = {}
    async with httpx.AsyncClient(follow_redirects=True, trust_env=False) as client:
        primary = TencentDailyProvider(client, policy=policy)
        backup = EastmoneyDailyProvider(client, policy=policy)
        fallback = SinaDailyProvider(
            policy=ProviderPolicy(
                timeout_seconds=20,
                retry_count=0,
                min_interval_seconds=2,
            )
        )
        for stock in stocks:
            error: str | None = None
            try:
                stock_key = ("sh" if stock.exchange == "SSE" else "sz") + stock.code
                results = await asyncio.gather(
                    primary.fetch(stock_key, start, end),
                    backup.fetch(stock_key, start, end),
                    return_exceptions=True,
                )
                series = tuple(
                    item for item in results if isinstance(item, ProviderDailySeries)
                )
                primary_series = next(
                    (item for item in series if item.source == "tencent"), None
                )
                eastmoney_series = next(
                    (item for item in series if item.source == "eastmoney"), None
                )
                backup_series = eastmoney_series
                fallback_error: BaseException | None = None
                primary_dates = (
                    {bar.trade_date for bar in primary_series.bars}
                    if primary_series is not None
                    else set()
                )
                backup_coverage_incomplete = _requires_sina_fallback(
                    primary_series, backup_series
                )
                if (
                    allow_sina_fallback
                    and (backup_series is None or backup_coverage_incomplete)
                ):
                    try:
                        sina_series = await fallback.fetch(stock_key, start, end)
                        series += (sina_series,)
                        backup_series = merge_provider_daily_series(
                            tuple(
                                item
                                for item in (backup_series, sina_series)
                                if item is not None
                            )
                        )
                    except (DailyProviderError, ValueError) as exc:
                        fallback_error = exc
                elif backup_series is not None:
                    backup_series = merge_provider_daily_series((backup_series,))
                final_backup_dates = (
                    {bar.trade_date for bar in backup_series.bars}
                    if backup_series is not None
                    else set()
                )
                if primary_dates - final_backup_dates:
                    error = (
                        "backup_coverage_incomplete:"
                        + ",".join(
                            day.isoformat() for day in sorted(primary_dates - final_backup_dates)
                        )
                    )
                reconciled = reconcile_daily_series_with_amount_fallback(
                    primary_series,
                    backup_series,
                )
                quality_counts[reconciled.quality.value] = (
                    quality_counts.get(reconciled.quality.value, 0) + 1
                )
                if reconciled.quality.value in {"conflicted", "missing"}:
                    error = (
                        f"reconciliation:{reconciled.quality.value}:"
                        + "|".join(reconciled.warnings)
                    )
                if backup_series is None:
                    provider_errors = [
                        item for item in results if isinstance(item, BaseException)
                    ]
                    if fallback_error is not None:
                        provider_errors.append(fallback_error)
                    provider_error = ",".join(
                        _error_reason(item) for item in provider_errors
                    )
                    error = ",".join(part for part in (error, provider_error) if part)
                if series:
                    async with SessionFactory() as session, session.begin():
                        repository = SqlDataBaselineRepository(session)
                        for provider_series in series:
                            result = await repository.persist_provider_daily_series(
                                stock.id, provider_series
                            )
                            inserted += result.inserted
                            unchanged += result.unchanged
            except (ValueError, httpx.HTTPError) as exc:
                error = type(exc).__name__
            if error is not None:
                failed.append(f"{stock.code}:{error}")
            else:
                succeeded.append(stock.code)
            if checkpoint is not None and checkpoint_path is not None:
                checkpoint.mark(stock.code, error=error, updated_at=datetime.now(UTC))
                save_daily_checkpoint(checkpoint_path, checkpoint)
    print(
        f"stocks={len(stocks)} start={start.isoformat()} end={end.isoformat()} "
        f"inserted={inserted} unchanged={unchanged} failed={len(failed)}"
    )
    print(f"quality={dict(sorted(quality_counts.items()))}")
    if failed:
        print("failures=" + ";".join(failed))
    if checkpoint is not None and checkpoint_path is not None:
        remaining = await _remaining_stock_count(
            checkpoint.last_processed_code,
            v9_available_on=v9_available_on,
            published_by=published_by,
            fetched_by=fetched_by,
        )
        print(
            f"checkpoint={checkpoint_path} cursor={checkpoint.last_processed_code} "
            f"remaining={remaining} retry_failures={len(checkpoint.failures)}"
        )
    return DailyIngestionResult(
        requested_codes=tuple(stock.code for stock in stocks),
        succeeded_codes=tuple(sorted(succeeded)),
        failed_codes=tuple(sorted(item.split(":", 1)[0] for item in failed)),
        inserted=inserted,
        unchanged=unchanged,
        quality_counts=dict(quality_counts),
    )


async def _load_stocks(
    codes: tuple[str, ...],
    limit: int | None,
    *,
    after_code: str | None,
    v9_available_on: date | None,
    published_by: date | None,
    fetched_by: datetime,
) -> list[models.Stock]:
    statement = (
        select(models.Stock)
        .where(
            models.Stock.status == "active",
            models.Stock.exchange.in_(("SSE", "SZSE")),
        )
        .order_by(models.Stock.code)
    )
    if v9_available_on is not None:
        assert published_by is not None
        statement = statement.join(
            models.StockClassification,
            models.StockClassification.stock_id == models.Stock.id,
        ).where(
            models.StockClassification.classification_type
            == PRIMARY_CLASSIFICATION_TYPE,
            models.StockClassification.source == PRIMARY_SOURCE,
            models.StockClassification.is_primary.is_(True),
            models.StockClassification.valid_from <= v9_available_on,
            (
                models.StockClassification.valid_to.is_(None)
                | (models.StockClassification.valid_to >= v9_available_on)
            ),
            models.StockClassification.published_at.is_not(None),
            models.StockClassification.published_at <= published_by,
            models.StockClassification.fetched_at <= fetched_by,
            models.Stock.board != "star",
        )
    if codes:
        statement = statement.where(models.Stock.code.in_(codes))
    elif after_code is not None:
        statement = statement.where(models.Stock.code > after_code)
    if limit is not None:
        statement = statement.limit(limit)
    async with SessionFactory() as session:
        return list((await session.scalars(statement)).all())


async def _remaining_stock_count(
    after_code: str | None,
    *,
    v9_available_on: date | None,
    published_by: date | None,
    fetched_by: datetime,
) -> int:
    statement = select(func.count(models.Stock.id)).where(
        models.Stock.status == "active",
        models.Stock.exchange.in_(("SSE", "SZSE")),
    )
    if v9_available_on is not None:
        assert published_by is not None
        statement = statement.join(
            models.StockClassification,
            models.StockClassification.stock_id == models.Stock.id,
        ).where(
            models.StockClassification.classification_type
            == PRIMARY_CLASSIFICATION_TYPE,
            models.StockClassification.source == PRIMARY_SOURCE,
            models.StockClassification.is_primary.is_(True),
            models.StockClassification.valid_from <= v9_available_on,
            (
                models.StockClassification.valid_to.is_(None)
                | (models.StockClassification.valid_to >= v9_available_on)
            ),
            models.StockClassification.published_at.is_not(None),
            models.StockClassification.published_at <= published_by,
            models.StockClassification.fetched_at <= fetched_by,
            models.Stock.board != "star",
        )
    if after_code is not None:
        statement = statement.where(models.Stock.code > after_code)
    async with SessionFactory() as session:
        return int(await session.scalar(statement) or 0)


def _error_reason(result: ProviderDailySeries | BaseException) -> str:
    if isinstance(result, DailyProviderError):
        return f"{result.source}:{result.reason}"
    if isinstance(result, BaseException):
        return type(result).__name__
    return "none"


def _requires_sina_fallback(
    primary: ProviderDailySeries | None,
    backup: ProviderDailySeries | None,
) -> bool:
    """Use the second backup when the first backup misses primary dates."""
    if backup is None:
        return True
    if primary is None:
        return False
    primary_dates = {bar.trade_date for bar in primary.bars}
    backup_dates = {bar.trade_date for bar in backup.bars}
    return bool(primary_dates - backup_dates)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest versioned qfq daily bars for a bounded active-stock batch."
    )
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--after-code")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument(
        "--v9-universe",
        action="store_true",
        help="ingest only stocks with an effective, time-valid PAWE primary domain",
    )
    parser.add_argument("--available-on", type=date.fromisoformat)
    parser.add_argument("--published-by", type=date.fromisoformat)
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")
    if args.after_code is not None and (
        len(args.after_code) != 6 or not args.after_code.isdigit()
    ):
        parser.error("--after-code must contain six digits")
    if args.code and args.retry_failures:
        parser.error("--code and --retry-failures cannot be combined")
    if args.v9_universe and (args.available_on is None or args.published_by is None):
        parser.error("--v9-universe requires --available-on and --published-by")
    if not args.v9_universe and (
        args.available_on is not None or args.published_by is not None
    ):
        parser.error("--available-on/--published-by require --v9-universe")
    start = args.start or (args.end - timedelta(days=120))
    checkpoint_path = args.checkpoint
    if checkpoint_path is None and not args.code:
        checkpoint_path = Path(
            "data/snapshots/daily-bars-"
            f"{'v9-' if args.v9_universe else ''}"
            f"{start.isoformat()}-{args.end.isoformat()}.json"
        )
    checkpoint = (
        load_daily_checkpoint(checkpoint_path, start=start, end=args.end)
        if checkpoint_path is not None
        else None
    )
    codes = tuple(args.code)
    after_code = args.after_code
    if checkpoint is not None:
        if args.retry_failures:
            codes = tuple(sorted(checkpoint.failures))
            after_code = None
            if not codes:
                print(f"checkpoint={checkpoint_path} retry_failures=0 nothing_to_retry")
                return
        elif not codes and after_code is None:
            after_code = checkpoint.last_processed_code
    asyncio.run(
        ingest(
            start,
            args.end,
            codes=codes,
            limit=None if args.all else args.limit,
            after_code=after_code,
            checkpoint=checkpoint,
            checkpoint_path=checkpoint_path,
            v9_available_on=args.available_on,
            published_by=args.published_by,
        )
    )


if __name__ == "__main__":
    main()
