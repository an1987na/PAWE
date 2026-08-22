from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.db.models import LegacyDocumentStaging, LegacyItemStaging


@dataclass(frozen=True, slots=True)
class AttributionSummary:
    outcome_ready_count: int
    baseline_drift_count: int
    possible_baseline_shift_count: int
    single_metric_conflict_count: int
    irregular_conflict_count: int
    excluded_other_count: int


def attribute_conflict(
    declared_baseline: float,
    metrics: list[dict[str, object]],
    *,
    possible_shift_tolerance: float = 0.0025,
) -> dict[str, object]:
    if declared_baseline <= 0:
        raise ValueError("declared baseline must be positive")
    failed_metrics = [
        str(metric["metric"])
        for metric in metrics
        if metric.get("within_tolerance") is False
    ]
    deltas = [_number(metric["absolute_delta"]) for metric in metrics]
    details: dict[str, object] = {
        "failed_metrics": failed_metrics,
        "max_absolute_delta": max(deltas, default=0.0),
        "cause_proven": False,
    }
    if len(failed_metrics) == 1:
        return {
            "code": "single_metric_definition_conflict",
            "confidence": "high",
            **details,
        }

    implied = {
        str(metric["metric"]): declared_baseline
        * (1 + _number(metric["recalculated"]))
        / (1 + _number(metric["claimed"]))
        for metric in metrics
        if 1 + _number(metric["claimed"]) > 0
    }
    if len(implied) >= 2:
        values = tuple(implied.values())
        implied_average = sum(values) / len(values)
        relative_spread = (max(values) - min(values)) / implied_average
        details.update(
            {
                "implied_baseline_price": implied_average,
                "implied_by_metric": implied,
                "implied_relative_spread": relative_spread,
                "declared_relative_gap": abs(implied_average - declared_baseline)
                / declared_baseline,
                "possible_shift_tolerance": possible_shift_tolerance,
            }
        )
        if relative_spread <= possible_shift_tolerance:
            return {
                "code": "possible_baseline_or_adjustment_shift",
                "confidence": "medium",
                **details,
            }
    return {
        "code": "multi_metric_irregular_conflict",
        "confidence": "high",
        **details,
    }


async def classify_legacy_outcomes(session: AsyncSession) -> AttributionSummary:
    rows = (
        await session.execute(
            select(LegacyItemStaging, LegacyDocumentStaging)
            .join(
                LegacyDocumentStaging,
                LegacyDocumentStaging.id == LegacyItemStaging.document_id,
            )
            .where(
                LegacyItemStaging.bucket == "main",
                LegacyItemStaging.baseline_price.is_not(None),
                LegacyItemStaging.legacy_recalculated.is_not(None),
            )
        )
    ).all()
    counts = {
        "outcome_ready": 0,
        "baseline_drift": 0,
        "possible_shift": 0,
        "single_metric": 0,
        "irregular": 0,
        "excluded_other": 0,
    }
    for item, document in rows:
        item.replay_arm = replay_arm(document.source_ref)
        item.conflict_attribution = None
        if item.verification_status == "recalc_single_source":
            item.replay_eligibility = "outcome_ready_single_source"
            counts["outcome_ready"] += 1
        elif item.verification_status == "baseline_drift":
            item.replay_eligibility = "excluded_baseline_drift"
            counts["baseline_drift"] += 1
        elif item.verification_status == "conflicted":
            item.replay_eligibility = "excluded_conflict"
            legacy_payload = item.legacy_recalculated or {}
            metrics = legacy_payload.get("metrics")
            if not isinstance(metrics, list) or item.baseline_price is None:
                counts["excluded_other"] += 1
                continue
            item.conflict_attribution = attribute_conflict(
                float(item.baseline_price),
                metrics,
            )
            code = item.conflict_attribution["code"]
            if code == "possible_baseline_or_adjustment_shift":
                counts["possible_shift"] += 1
            elif code == "single_metric_definition_conflict":
                counts["single_metric"] += 1
            else:
                counts["irregular"] += 1
        else:
            item.replay_eligibility = "excluded_other"
            counts["excluded_other"] += 1
    await session.commit()
    return AttributionSummary(
        counts["outcome_ready"],
        counts["baseline_drift"],
        counts["possible_shift"],
        counts["single_metric"],
        counts["irregular"],
        counts["excluded_other"],
    )


def replay_arm(source_ref: str) -> str:
    stem = Path(source_ref).stem
    if "新规则" in stem:
        return "new_rule"
    if "旧规则" in stem:
        return "old_rule"
    return "default"


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("legacy metric must be numeric")
    return float(value)
