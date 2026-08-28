from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from pawe_api.contracts import DataQuality


@dataclass(frozen=True, slots=True)
class NormalizedDailyBar:
    stock_key: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    adjustment: str
    source: str
    fetched_at: datetime
    quality: DataQuality
    amount: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ProviderDailySeries:
    stock_key: str
    source: str
    fetched_at: datetime
    bars: tuple[NormalizedDailyBar, ...]
    is_delayed: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReconciledDailySeries:
    stock_key: str
    quality: DataQuality
    bars: tuple[NormalizedDailyBar, ...]
    sources: tuple[str, ...]
    warnings: tuple[str, ...]


def merge_provider_daily_series(
    candidates: Sequence[ProviderDailySeries],
) -> ProviderDailySeries | None:
    """Merge complementary backup coverage without inventing bars.

    The first candidate wins on overlapping dates; callers should order the
    primary backup before a lower-priority fallback.  Every selected bar is
    still an original provider bar, and the synthetic source label preserves
    that more than one backup contributed to the observation.
    """
    if not candidates:
        return None
    stock_keys = {candidate.stock_key for candidate in candidates}
    if len(stock_keys) != 1:
        raise ValueError("daily backup sources refer to different stocks")
    by_date: dict[date, NormalizedDailyBar] = {}
    contributors: list[ProviderDailySeries] = []
    for candidate in candidates:
        _validate_series(candidate)
        before = len(by_date)
        for bar in candidate.bars:
            by_date.setdefault(bar.trade_date, bar)
        if len(by_date) > before:
            contributors.append(candidate)
    first = candidates[0]
    source = "+".join(candidate.source for candidate in contributors)
    warnings = tuple(
        dict.fromkeys(warning for candidate in contributors for warning in candidate.warnings)
    )
    if len(contributors) > 1:
        warnings += ("backup_sources_merged",)
    return ProviderDailySeries(
        stock_key=first.stock_key,
        source=source,
        fetched_at=max(candidate.fetched_at for candidate in contributors),
        bars=tuple(by_date[trade_date] for trade_date in sorted(by_date)),
        warnings=warnings,
    )


def reconcile_daily_series(
    primary: ProviderDailySeries | None,
    backup: ProviderDailySeries | None,
    *,
    price_tolerance: Decimal = Decimal("0.005"),
    volume_tolerance: Decimal = Decimal("0.02"),
) -> ReconciledDailySeries:
    if price_tolerance < 0 or volume_tolerance < 0:
        raise ValueError("reconciliation tolerances cannot be negative")
    if primary is None and backup is None:
        return ReconciledDailySeries(
            stock_key="",
            quality=DataQuality.MISSING,
            bars=(),
            sources=(),
            warnings=("all_daily_sources_missing",),
        )
    if primary is None:
        assert backup is not None
        _validate_series(backup)
        return ReconciledDailySeries(
            stock_key=backup.stock_key,
            quality=DataQuality.DEGRADED,
            bars=_with_quality(backup.bars, DataQuality.DEGRADED),
            sources=(backup.source,),
            warnings=("primary_daily_source_missing",) + backup.warnings,
        )
    _validate_series(primary)
    if backup is None:
        return ReconciledDailySeries(
            stock_key=primary.stock_key,
            quality=DataQuality.SINGLE_SOURCE,
            bars=_with_quality(primary.bars, DataQuality.SINGLE_SOURCE),
            sources=(primary.source,),
            warnings=("backup_daily_source_missing",) + primary.warnings,
        )
    _validate_series(backup)
    if primary.stock_key != backup.stock_key:
        raise ValueError("daily sources refer to different stocks")

    primary_by_date = {bar.trade_date: bar for bar in primary.bars}
    backup_by_date = {bar.trade_date: bar for bar in backup.bars}
    overlap = sorted(primary_by_date.keys() & backup_by_date.keys())
    conflicts = [
        trade_date
        for trade_date in overlap
        if _bar_conflicts(
            primary_by_date[trade_date],
            backup_by_date[trade_date],
            price_tolerance=price_tolerance,
            volume_tolerance=volume_tolerance,
        )
    ]
    sources = (primary.source, backup.source)
    warnings = primary.warnings + backup.warnings
    if conflicts:
        return ReconciledDailySeries(
            stock_key=primary.stock_key,
            quality=DataQuality.CONFLICTED,
            bars=(),
            sources=sources,
            warnings=warnings
            + ("daily_source_conflict:" + ",".join(day.isoformat() for day in conflicts),),
        )
    if not overlap:
        return ReconciledDailySeries(
            stock_key=primary.stock_key,
            quality=DataQuality.CONFLICTED,
            bars=(),
            sources=sources,
            warnings=warnings + ("daily_sources_have_no_overlap",),
        )

    same_dates = primary_by_date.keys() == backup_by_date.keys()
    quality = DataQuality.VERIFIED if same_dates else DataQuality.SINGLE_SOURCE
    if not same_dates:
        warnings += ("backup_daily_coverage_incomplete",)
    if primary.is_delayed or backup.is_delayed:
        quality = DataQuality.DEGRADED
        warnings += ("daily_source_delayed",)
    return ReconciledDailySeries(
        stock_key=primary.stock_key,
        quality=quality,
        bars=_with_quality(primary.bars, quality),
        sources=sources,
        warnings=warnings,
    )


def reconcile_daily_series_with_amount_fallback(
    primary: ProviderDailySeries | None,
    backup: ProviderDailySeries | None,
    *,
    price_tolerance: Decimal = Decimal("0.005"),
    volume_tolerance: Decimal = Decimal("0.02"),
) -> ReconciledDailySeries:
    strict = reconcile_daily_series(
        primary,
        backup,
        price_tolerance=price_tolerance,
        volume_tolerance=volume_tolerance,
    )
    if primary is None or backup is None:
        return strict
    primary_by_date = {bar.trade_date: bar for bar in primary.bars}
    backup_by_date = {bar.trade_date: bar for bar in backup.bars}
    if primary_by_date.keys() < backup_by_date.keys():
        overlap = sorted(primary_by_date.keys() & backup_by_date.keys())
        if not overlap or backup.is_delayed:
            return strict
        pairs = [
            (primary_by_date[trade_date], backup_by_date[trade_date])
            for trade_date in overlap
        ]
        if any(
            primary_bar.adjustment != backup_bar.adjustment
            or _relative_difference(primary_bar.volume, backup_bar.volume) > volume_tolerance
            or backup_bar.amount is None
            for primary_bar, backup_bar in pairs
        ) or any(bar.amount is None for bar in backup_by_date.values()):
            return strict
        if strict.quality is DataQuality.CONFLICTED and backup.source != "sina":
            return strict
        return ReconciledDailySeries(
            stock_key=backup.stock_key,
            quality=DataQuality.DEGRADED,
            bars=_with_quality(backup.bars, DataQuality.DEGRADED),
            sources=(primary.source, backup.source),
            warnings=strict.warnings
            + (
                "primary_daily_coverage_incomplete",
                f"amount_bearing_backup_series_used:{backup.source}",
            ),
        )
    if not backup_by_date.keys() <= primary_by_date.keys():
        return strict
    if primary.is_delayed or backup.is_delayed:
        return strict
    pairs = [
        (primary_by_date[trade_date], backup_by_date[trade_date])
        for trade_date in sorted(backup_by_date)
    ]
    if not pairs:
        return strict
    if any(
        primary_bar.adjustment != backup_bar.adjustment
        or _relative_difference(primary_bar.volume, backup_bar.volume) > volume_tolerance
        or backup_bar.amount is None
        for primary_bar, backup_bar in pairs
    ):
        return strict
    if strict.quality is DataQuality.CONFLICTED and backup.source != "sina":
        return strict
    if strict.quality in {DataQuality.MISSING, DataQuality.DEGRADED}:
        return strict
    combined_amounts = {
        trade_date: backup_bar.amount for trade_date, backup_bar in backup_by_date.items()
    }
    if any(
        combined_amounts.get(trade_date, primary_bar.amount) is None
        for trade_date, primary_bar in primary_by_date.items()
    ):
        return strict
    quality = (
        DataQuality.SINGLE_SOURCE if strict.quality is DataQuality.CONFLICTED else strict.quality
    )
    warnings = strict.warnings + (f"amount_fallback:{backup.source}",)
    if strict.quality is DataQuality.CONFLICTED:
        warnings += ("price_verification_unavailable:qfq_factor_conflict",)
    return ReconciledDailySeries(
        stock_key=primary.stock_key,
        quality=quality,
        bars=tuple(
            NormalizedDailyBar(
                stock_key=primary_bar.stock_key,
                trade_date=primary_bar.trade_date,
                open=primary_bar.open,
                high=primary_bar.high,
                low=primary_bar.low,
                close=primary_bar.close,
                volume=primary_bar.volume,
                amount=combined_amounts.get(trade_date, primary_bar.amount),
                adjustment=primary_bar.adjustment,
                source=primary_bar.source,
                fetched_at=max(
                    primary_bar.fetched_at,
                    backup_by_date.get(trade_date, primary_bar).fetched_at,
                ),
                quality=quality,
            )
            for trade_date, primary_bar in sorted(primary_by_date.items())
        ),
        sources=strict.sources,
        warnings=warnings,
    )


def _validate_series(series: ProviderDailySeries) -> None:
    if not series.bars:
        raise ValueError("provider daily series cannot be empty")
    if any(bar.stock_key != series.stock_key for bar in series.bars):
        raise ValueError("provider daily series contains another stock")
    allowed_sources = set(series.source.split("+"))
    if any(bar.source not in allowed_sources for bar in series.bars):
        raise ValueError("provider daily series contains another source")
    if any(
        left.trade_date >= right.trade_date
        for left, right in zip(series.bars, series.bars[1:], strict=False)
    ):
        raise ValueError("provider daily bars must be strictly ordered")
    if len({bar.adjustment for bar in series.bars}) != 1:
        raise ValueError("provider daily series mixes adjustment modes")


def _bar_conflicts(
    primary: NormalizedDailyBar,
    backup: NormalizedDailyBar,
    *,
    price_tolerance: Decimal,
    volume_tolerance: Decimal,
) -> bool:
    if primary.adjustment != backup.adjustment:
        return True
    price_pairs = (
        (primary.open, backup.open),
        (primary.high, backup.high),
        (primary.low, backup.low),
        (primary.close, backup.close),
    )
    return any(
        _relative_difference(left, right) > price_tolerance for left, right in price_pairs
    ) or (_relative_difference(primary.volume, backup.volume) > volume_tolerance)


def _relative_difference(left: Decimal, right: Decimal) -> Decimal:
    denominator = max(abs(left), abs(right))
    return Decimal(0) if denominator == 0 else abs(left - right) / denominator


def _with_quality(
    bars: tuple[NormalizedDailyBar, ...], quality: DataQuality
) -> tuple[NormalizedDailyBar, ...]:
    return tuple(
        NormalizedDailyBar(
            stock_key=bar.stock_key,
            trade_date=bar.trade_date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            adjustment=bar.adjustment,
            source=bar.source,
            fetched_at=bar.fetched_at,
            quality=quality,
            amount=bar.amount,
        )
        for bar in bars
    )
