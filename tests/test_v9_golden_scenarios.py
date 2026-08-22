import json
from pathlib import Path
from typing import Any

import pytest
from pawe_api.contracts import MarketState
from pawe_api.rules.market_state import MarketStateInput, PoolMetrics, determine_market_state

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "v9" / "market_states.json"


def _cases() -> list[dict[str, Any]]:
    loaded = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["name"]))
def test_v9_market_state_golden_scenarios(case: dict[str, Any]) -> None:
    indices = case["indices"]
    retreat_medians = case["previous_retreat_medians"]
    decision = determine_market_state(
        MarketStateInput(
            previous_state=MarketState(case["previous_state"]),
            shanghai_close_return=indices[0],
            gem_close_return=indices[1],
            star50_close_return=indices[2],
            main_pool=PoolMetrics(*case["main_pool"]),
            reserve_pool=PoolMetrics(*case["reserve_pool"]),
            main_average_without_strongest=case["main_average_without_strongest"],
            strong_reserve_positive_close_ratio=case["strong_reserve_positive_close_ratio"],
            qualifying_recovery_sector_count=case["qualifying_recovery_sector_count"],
            previous_retreat_main_median_close=retreat_medians[0],
            previous_retreat_reserve_median_close=retreat_medians[1],
        )
    )
    assert decision.state is MarketState(case["expected_state"])
    assert decision.flags == tuple(case["expected_flags"])
