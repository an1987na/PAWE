from pawe_api.experiments.legacy_attribution import attribute_conflict, replay_arm


def _metric(
    name: str,
    claimed: float,
    recalculated: float,
    *,
    within_tolerance: bool = False,
) -> dict[str, object]:
    return {
        "metric": name,
        "claimed": claimed,
        "recalculated": recalculated,
        "absolute_delta": abs(claimed - recalculated),
        "within_tolerance": within_tolerance,
    }


def test_close_implied_baselines_are_only_a_possible_shift() -> None:
    metrics = [
        _metric("week_high_return", 0.103, 0.10),
        _metric("close_return", 0.053, 0.05),
        _metric("max_drawdown", -0.047, -0.05),
    ]

    result = attribute_conflict(100.0, metrics)

    assert result["code"] == "possible_baseline_or_adjustment_shift"
    assert result["confidence"] == "medium"
    assert result["cause_proven"] is False


def test_one_failed_metric_is_a_definition_conflict() -> None:
    metrics = [
        _metric("week_high_return", 0.10, 0.10, within_tolerance=True),
        _metric("close_return", 0.05, 0.05, within_tolerance=True),
        _metric("max_drawdown", -0.01, -0.05),
    ]

    result = attribute_conflict(100.0, metrics)

    assert result["code"] == "single_metric_definition_conflict"
    assert result["failed_metrics"] == ["max_drawdown"]


def test_divergent_implied_baselines_are_irregular() -> None:
    metrics = [
        _metric("week_high_return", 0.30, 0.10),
        _metric("close_return", -0.10, 0.05),
        _metric("max_drawdown", -0.01, -0.05),
    ]

    result = attribute_conflict(100.0, metrics)

    assert result["code"] == "multi_metric_irregular_conflict"


def test_replay_arm_preserves_parallel_rule_variants() -> None:
    assert replay_arm("outputs/2026-07-03_周终复盘_新规则.md") == "new_rule"
    assert replay_arm("outputs/2026-07-03_周终复盘_旧规则.md") == "old_rule"
    assert replay_arm("outputs/2025-02-21_周终复盘.md") == "default"
