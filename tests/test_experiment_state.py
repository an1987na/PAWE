import pytest
from pawe_api.contracts import ExperimentFoldResult
from pawe_api.experiments.state import ExperimentStateError, require_transition
from pydantic import ValidationError


def test_experiment_lifecycle_allows_only_declared_next_state() -> None:
    require_transition("schema_validated", "replay_queued")
    require_transition("replay_running", "replay_passed")
    require_transition("shadow_running", "awaiting_approval")
    require_transition("approved", "activated")
    require_transition("activated", "rolled_back")


def test_experiment_lifecycle_rejects_skips_and_terminal_reentry() -> None:
    with pytest.raises(ExperimentStateError, match="invalid experiment transition"):
        require_transition("schema_validated", "activated")
    with pytest.raises(ExperimentStateError, match="terminal experiment state"):
        require_transition("replay_rejected", "replay_queued")


def test_walk_forward_fold_requires_strict_time_isolation() -> None:
    fold = ExperimentFoldResult(
        fold_index=1,
        train_start="2025-01-01",
        train_end="2025-12-31",
        selection_start="2026-01-01",
        selection_end="2026-02-28",
        validation_start="2026-03-01",
        validation_end="2026-06-30",
        snapshot_ids=["snapshot-1"],
        sample_count=20,
        capacity_distribution={"5": 18, "3": 2},
        metrics={"touch_10_rate": 0.4},
        integrity_status="complete",
    )
    assert fold.capacity_distribution["5"] == 18

    with pytest.raises(ValidationError, match="ordered and non-overlapping"):
        ExperimentFoldResult(
            fold_index=1,
            train_start="2025-01-01",
            train_end="2026-03-31",
            selection_start="2026-01-01",
            selection_end="2026-02-28",
            validation_start="2026-03-01",
            validation_end="2026-06-30",
            snapshot_ids=["snapshot-1"],
            sample_count=20,
            capacity_distribution={"5": 20},
            metrics={},
            integrity_status="complete",
        )
