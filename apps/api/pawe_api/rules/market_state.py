from dataclasses import dataclass

from pawe_api.contracts import MarketState


@dataclass(frozen=True, slots=True)
class PoolMetrics:
    average_week_high_return: float
    touch_rate_10: float
    positive_close_ratio: float
    median_close_return: float
    coverage_ratio: float = 1.0


@dataclass(frozen=True, slots=True)
class MarketStateInput:
    previous_state: MarketState
    shanghai_close_return: float | None
    gem_close_return: float | None
    star50_close_return: float | None
    main_pool: PoolMetrics
    reserve_pool: PoolMetrics
    main_average_without_strongest: float
    strong_reserve_positive_close_ratio: float
    qualifying_recovery_sector_count: int
    previous_retreat_main_median_close: float | None = None
    previous_retreat_reserve_median_close: float | None = None


@dataclass(frozen=True, slots=True)
class MarketStateDecision:
    state: MarketState
    flags: tuple[str, ...] = ()


def determine_market_state(inputs: MarketStateInput) -> MarketStateDecision:
    indices = (inputs.shanghai_close_return, inputs.gem_close_return, inputs.star50_close_return)
    if (
        any(value is None for value in indices)
        or min(inputs.main_pool.coverage_ratio, inputs.reserve_pool.coverage_ratio) < 0.80
    ):
        return MarketStateDecision(inputs.previous_state, ("STATE_DATA_DEGRADED",))

    shanghai, gem, star50 = indices
    assert shanghai is not None and gem is not None and star50 is not None
    systemic_retreat = (
        shanghai < 0
        and gem < 0
        and star50 < 0
        and min(gem, star50) <= -0.08
        and inputs.main_pool.positive_close_ratio < 0.50
        and inputs.reserve_pool.positive_close_ratio < 0.50
    )
    if systemic_retreat:
        return MarketStateDecision(MarketState.SYSTEMIC_RETREAT)

    recovery_confirmed = _recovery_confirmed(inputs)
    if inputs.previous_state in {MarketState.BREADTH_RECOVERY, MarketState.RECOVERY_CONFIRMED}:
        if inputs.previous_state is MarketState.BREADTH_RECOVERY and recovery_confirmed:
            return MarketStateDecision(MarketState.RECOVERY_CONFIRMED)
        if not recovery_confirmed:
            return MarketStateDecision(MarketState.RECOVERY_FAILED)

    if (
        inputs.previous_state in {MarketState.SYSTEMIC_RETREAT, MarketState.RECOVERY_FAILED}
        and inputs.main_pool.average_week_high_return < 0.10
        and inputs.reserve_pool.average_week_high_return >= 0.08
        and inputs.qualifying_recovery_sector_count >= 3
        and inputs.strong_reserve_positive_close_ratio > 0.50
    ):
        return MarketStateDecision(MarketState.BREADTH_RECOVERY)

    if (
        inputs.main_pool.average_week_high_return >= 0.10
        and inputs.main_pool.touch_rate_10 <= 0.40
        and inputs.main_average_without_strongest < 0.10
    ):
        return MarketStateDecision(MarketState.ANCHOR_DISTORTED)

    return MarketStateDecision(MarketState.NORMAL)


def _recovery_confirmed(inputs: MarketStateInput) -> bool:
    if (
        inputs.previous_retreat_main_median_close is None
        or inputs.previous_retreat_reserve_median_close is None
    ):
        return False
    return (
        inputs.main_pool.average_week_high_return >= 0.10
        and inputs.reserve_pool.average_week_high_return >= 0.10
        and inputs.main_pool.touch_rate_10 >= 0.40
        and inputs.reserve_pool.touch_rate_10 >= 0.40
        and inputs.main_pool.median_close_return > 0
        and inputs.reserve_pool.median_close_return > 0
        and inputs.main_pool.median_close_return - inputs.previous_retreat_main_median_close >= 0.02
        and inputs.reserve_pool.median_close_return - inputs.previous_retreat_reserve_median_close
        >= 0.02
    )
