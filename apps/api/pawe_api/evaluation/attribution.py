from pawe_api.ai.audit import fingerprint
from pawe_api.db import models

TAXONOMY = frozenset(
    {
        "market_state_error",
        "rotation_lag",
        "continuation_overreach",
        "overheat_filter_loose",
        "overheat_filter_strict",
        "stock_selection_error",
        "catalyst_error",
        "confirmation_insufficient",
        "data_anomaly",
        "candidate_coverage_insufficient",
        "anchor_distortion",
        "ai_swap_error",
        "human_override_error",
    }
)


def deterministic_attribution_facts(
    review: models.WeeklyReview,
) -> tuple[dict[str, object], str, bool, str]:
    raw_item_count = review.aggregate.get("item_count")
    item_count = raw_item_count if isinstance(raw_item_count, int) else 0
    facts: dict[str, object] = {
        "week_id": review.week_id.isoformat(),
        "source_type": review.source_type,
        "quality": review.quality,
        "aggregate": review.aggregate,
        "warnings": review.warnings,
        "item_count": item_count,
    }
    warnings = " ".join(review.warnings).upper()
    if facts["item_count"] == 0 or "CANDIDATE" in warnings or "COVERAGE" in warnings:
        taxonomy = "candidate_coverage_insufficient"
    elif (
        review.quality in {"missing", "degraded", "conflicted"}
        or "DATA" in warnings
        or "SOURCE" in warnings
    ):
        taxonomy = "data_anomaly"
    elif "MARKET" in warnings or "STATE" in warnings:
        taxonomy = "market_state_error"
    else:
        taxonomy = "confirmation_insufficient"
    counterfactual_allowed = review.source_type in {
        "rule",
        "published",
        "historical_replay",
    } and item_count > 0
    if not counterfactual_allowed:
        facts["counterfactual_warning"] = "FROZEN_CANDIDATE_DATA_INSUFFICIENT"
    return facts, taxonomy, counterfactual_allowed, fingerprint(facts)
