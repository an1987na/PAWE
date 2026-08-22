from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.data.series import ProviderDailySeries
from pawe_api.db.models import LegacyDocumentStaging, LegacyItemStaging
from pawe_api.evaluation.weekly import WeeklyBar, evaluate_weekly_path
from pawe_api.experiments.legacy import LegacyBucket, LegacyItem
from pawe_api.experiments.verification import MetricVerification, verify_legacy_item_metrics


class DailySeriesProvider(Protocol):
    source: str

    async def fetch(self, stock_key: str, start: date, end: date) -> ProviderDailySeries: ...


@dataclass(frozen=True, slots=True)
class VerificationUpdate:
    status: str
    source: str | None
    legacy_recalculated: dict[str, object] | None
    v9_recalculated: dict[str, object] | None
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BatchVerificationResult:
    selection_date: date
    eligible_count: int
    processed_count: int
    skipped_count: int
    matched_count: int
    baseline_drift_count: int
    conflicted_count: int
    insufficient_count: int


async def verify_selection_week(
    session: AsyncSession,
    provider: DailySeriesProvider,
    selection_date: date,
    *,
    force: bool = False,
) -> BatchVerificationResult:
    selection_refs = (
        await session.scalars(
            select(LegacyDocumentStaging.source_ref).where(
                LegacyDocumentStaging.document_type == "weekly_selection",
                LegacyDocumentStaging.document_date == selection_date,
            )
        )
    ).all()
    selection_names = {Path(source_ref).name for source_ref in selection_refs}
    if not selection_names:
        raise ValueError(f"No staged selection exists for {selection_date}")

    rows = (
        await session.execute(
            select(LegacyItemStaging, LegacyDocumentStaging)
            .join(
                LegacyDocumentStaging,
                LegacyDocumentStaging.id == LegacyItemStaging.document_id,
            )
            .where(
                LegacyDocumentStaging.document_type == "weekly_review",
                LegacyDocumentStaging.linked_source_ref.in_(selection_names),
                LegacyItemStaging.bucket == "main",
                LegacyItemStaging.baseline_price.is_not(None),
            )
            .order_by(LegacyDocumentStaging.source_ref, LegacyItemStaging.rank)
        )
    ).all()

    processed = skipped = matched = baseline_drift = conflicted = insufficient = 0
    for item, document in rows:
        if item.verification_status != "legacy_unverified" and not force:
            skipped += 1
            continue
        if document.document_date is None:
            update = VerificationUpdate(
                "insufficient_data", None, None, None, ("review_date_missing",)
            )
        else:
            try:
                series = await provider.fetch(
                    stock_key(item.stock_code), selection_date, document.document_date
                )
                update = build_verification_update(item, series)
            except (RuntimeError, ValueError) as exc:
                update = VerificationUpdate(
                    "insufficient_data",
                    getattr(provider, "source", None),
                    None,
                    None,
                    (f"daily_fetch_failed:{type(exc).__name__}",),
                )
        item.verification_status = update.status
        item.verification_source = update.source
        item.verified_at = datetime.now(UTC)
        item.legacy_recalculated = update.legacy_recalculated
        item.v9_recalculated = update.v9_recalculated
        item.verification_warnings = list(update.warnings)
        processed += 1
        if update.status == "recalc_single_source":
            matched += 1
        elif update.status == "baseline_drift":
            baseline_drift += 1
        elif update.status == "conflicted":
            conflicted += 1
        else:
            insufficient += 1
    await session.commit()
    return BatchVerificationResult(
        selection_date,
        len(rows),
        processed,
        skipped,
        matched,
        baseline_drift,
        conflicted,
        insufficient,
    )


def build_verification_update(
    item: LegacyItemStaging,
    series: ProviderDailySeries,
) -> VerificationUpdate:
    weekly_bars = [
        WeeklyBar(
            trade_date=bar.trade_date,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
        )
        for bar in series.bars
    ]
    if len(weekly_bars) < 3:
        return VerificationUpdate(
            "insufficient_week",
            series.source,
            None,
            None,
            (f"natural_week_has_only_{len(weekly_bars)}_trading_days",),
        )
    legacy_item = LegacyItem(
        bucket=LegacyBucket.MAIN,
        stock_code=item.stock_code,
        stock_name=item.stock_name,
        baseline_price=float(item.baseline_price) if item.baseline_price is not None else None,
        week_high_return=_optional_float(item.week_high_return),
        close_return=_optional_float(item.close_return),
        max_drawdown=_optional_float(item.max_drawdown),
    )
    legacy_result = verify_legacy_item_metrics(legacy_item, weekly_bars)
    v9_result = evaluate_weekly_path(weekly_bars)
    legacy_payload: dict[str, object] = {
        "baseline_type": "legacy_declared",
        "baseline_price": legacy_item.baseline_price,
        "claim_status": legacy_result.status,
        "metrics": [
            {
                "metric": metric.metric,
                "claimed": metric.claimed,
                "recalculated": metric.recalculated,
                "absolute_delta": metric.absolute_delta,
                "within_tolerance": metric.within_tolerance,
            }
            for metric in legacy_result.metrics
        ],
    }
    baseline_drift = _infer_baseline_drift(
        legacy_item.baseline_price,
        legacy_result.metrics,
    )
    if baseline_drift is not None:
        legacy_payload["baseline_drift"] = baseline_drift
    v9_payload: dict[str, object] = {
        "baseline_type": "first_trading_day_open",
        "entry_trade_date": v9_result.entry_trade_date.isoformat(),
        "entry_price": v9_result.entry_price,
        "week_high_return": v9_result.week_high_return,
        "week_close_return": v9_result.week_close_return,
        "max_drawdown_from_entry": v9_result.max_drawdown_from_entry,
        "max_peak_to_trough_drawdown": v9_result.max_peak_to_trough_drawdown,
        "target_touched": v9_result.target_touched,
        "target_touch_date": (
            v9_result.target_touch_date.isoformat() if v9_result.target_touch_date else None
        ),
    }
    if legacy_result.status == "verified":
        status = "recalc_single_source"
        warnings: tuple[str, ...] = ()
    elif baseline_drift is not None:
        status = "baseline_drift"
        warnings = ("legacy_baseline_drift_detected",)
    else:
        status = legacy_result.status
        warnings = ()
    return VerificationUpdate(status, series.source, legacy_payload, v9_payload, warnings)


def stock_key(code: str) -> str:
    if len(code) != 6 or not code.isdigit():
        raise ValueError("legacy stock code must contain six digits")
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("0", "3")):
        return "sz" + code
    raise ValueError(f"unsupported legacy exchange for {code}")


def _optional_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _infer_baseline_drift(
    declared_baseline: float | None,
    metrics: tuple[MetricVerification, ...],
    *,
    consistency_tolerance: float = 0.0005,
) -> dict[str, object] | None:
    """Infer a historical baseline without treating a drifted claim as verified.

    Legacy percentages were rounded in Markdown, so independently inferred baselines
    may differ by a few basis points. A shared shift across at least two metrics is
    classified separately from a genuine metric conflict.
    """
    if declared_baseline is None or declared_baseline <= 0:
        return None
    implied_by_metric: dict[str, float] = {}
    for metric in metrics:
        claimed = metric.claimed
        recalculated = metric.recalculated
        denominator = 1 + claimed
        if denominator <= 0:
            continue
        observed_price = declared_baseline * (1 + recalculated)
        implied_by_metric[metric.metric] = observed_price / denominator
    if len(implied_by_metric) < 2:
        return None
    implied_values = tuple(implied_by_metric.values())
    implied_baseline = sum(implied_values) / len(implied_values)
    relative_spread = (max(implied_values) - min(implied_values)) / implied_baseline
    if relative_spread > consistency_tolerance:
        return None
    return {
        "classification": "consistent_implied_baseline",
        "implied_baseline_price": implied_baseline,
        "implied_by_metric": implied_by_metric,
        "relative_spread": relative_spread,
        "declared_relative_gap": abs(implied_baseline - declared_baseline)
        / declared_baseline,
        "consistency_tolerance": consistency_tolerance,
    }
