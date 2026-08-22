import pytest
from pawe_api.experiments.replay_inventory import outcome_set_status


def test_outcome_set_requires_every_published_item() -> None:
    assert outcome_set_status(5, 5) == "research_ready_single_source"
    assert outcome_set_status(5, 4) == "excluded_incomplete_outcome"


def test_actual_published_count_can_be_less_than_five() -> None:
    assert outcome_set_status(3, 3) == "research_ready_single_source"


def test_invalid_ready_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="within published"):
        outcome_set_status(3, 4)
