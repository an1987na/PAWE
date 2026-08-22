from dataclasses import dataclass

from pawe_api.contracts import MarketState
from pawe_api.rules.models import CandidateBucket, Domain, ScoredCandidate


@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    target_size: int = 5
    minimum_main_for_three_or_more: int = 3
    maximum_supplementary_and_external: int = 2
    maximum_external: int = 1
    maximum_per_sector: int = 2
    low_confidence_fifth_score: float = 55.0


DEFAULT_PORTFOLIO_CONFIG = PortfolioConfig()


@dataclass(frozen=True, slots=True)
class PortfolioResult:
    items: tuple[ScoredCandidate, ...]
    shortage: bool
    shortage_reason: str | None
    low_confidence: bool


def build_portfolio(
    candidates: list[ScoredCandidate],
    config: PortfolioConfig = DEFAULT_PORTFOLIO_CONFIG,
    *,
    market_state: MarketState = MarketState.NORMAL,
    candidate_overheat_ratio: float = 0.0,
) -> PortfolioResult:
    if not 0 <= candidate_overheat_ratio <= 1:
        raise ValueError("candidate_overheat_ratio must be between zero and one")
    eligible = [
        candidate for candidate in candidates if candidate.bucket is CandidateBucket.ELIGIBLE
    ]
    ranked = sorted(
        eligible,
        key=lambda candidate: (
            -candidate.rule_score,
            -candidate.features.industry_chain_priority,
            -_quality_rank(candidate),
            -int(candidate.features.previous_close_positive),
            -candidate.features.avg_amount_20d,
            candidate.features.stock_code,
        ),
    )

    for size in range(config.target_size, 0, -1):
        selected = _select_size(
            ranked,
            size,
            config,
            market_state=market_state,
            require_unexhausted=candidate_overheat_ratio > 0.50,
        )
        if len(selected) == size:
            shortage = size < config.target_size
            fifth_score = selected[-1].rule_score if size == config.target_size else None
            evidence_gap = any(_has_critical_evidence_gap(item) for item in selected)
            return PortfolioResult(
                items=tuple(selected),
                shortage=shortage,
                shortage_reason=(
                    f"only {size} candidates form a portfolio satisfying all constraints"
                    if shortage
                    else None
                ),
                low_confidence=shortage
                or (fifth_score is not None and fifth_score < config.low_confidence_fifth_score)
                or evidence_gap,
            )

    return PortfolioResult(
        items=(),
        shortage=True,
        shortage_reason="no eligible candidate satisfies all constraints",
        low_confidence=True,
    )


def _select_size(
    ranked: list[ScoredCandidate],
    size: int,
    config: PortfolioConfig,
    *,
    market_state: MarketState,
    require_unexhausted: bool,
) -> list[ScoredCandidate]:
    required_main = config.minimum_main_for_three_or_more if size >= 3 else size
    maximum_non_main = min(config.maximum_supplementary_and_external, size - required_main)
    selected: list[ScoredCandidate] = []
    sector_counts: dict[str, int] = {}
    non_main_count = 0
    external_count = 0
    promotion_count = 0
    low_crowding_exploration_count = 0
    high_elasticity_count = 0
    high_heat_count = 0
    unexhausted_count = 0
    breadth_recovery = market_state is MarketState.BREADTH_RECOVERY
    retreat_constraints = market_state in {
        MarketState.SYSTEMIC_RETREAT,
        MarketState.RECOVERY_FAILED,
    }
    required_unexhausted = min(2, size) if require_unexhausted else 0

    for candidate in ranked:
        domain = candidate.features.primary_domain
        sector = candidate.features.primary_sector
        if sector_counts.get(sector, 0) >= config.maximum_per_sector:
            continue
        if domain is not Domain.MAIN and non_main_count >= maximum_non_main:
            continue
        if domain is Domain.EXTERNAL and external_count >= config.maximum_external:
            continue
        if (
            breadth_recovery
            and candidate.features.strong_reserve_promotion
            and promotion_count >= 2
        ):
            continue
        if (
            retreat_constraints
            and candidate.features.high_elasticity_exploration
            and high_elasticity_count >= 1
        ):
            continue
        if retreat_constraints and candidate.features.high_heat_direction and high_heat_count >= 2:
            continue

        promotion_after = promotion_count + int(candidate.features.strong_reserve_promotion)
        exploration_after = low_crowding_exploration_count + int(
            candidate.features.low_crowding_exploration
        )
        unexhausted_after = unexhausted_count + int(candidate.features.near_month_unexhausted)
        slots_after = size - len(selected) - 1
        if breadth_recovery:
            required_categories_after = max(0, 2 - promotion_after) + max(0, 1 - exploration_after)
            if required_categories_after > slots_after:
                continue
        if max(0, required_unexhausted - unexhausted_after) > slots_after:
            continue

        selected.append(candidate)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if domain is not Domain.MAIN:
            non_main_count += 1
        if domain is Domain.EXTERNAL:
            external_count += 1
        promotion_count = promotion_after
        low_crowding_exploration_count = exploration_after
        high_elasticity_count += int(candidate.features.high_elasticity_exploration)
        high_heat_count += int(candidate.features.high_heat_direction)
        unexhausted_count = unexhausted_after
        if len(selected) == size:
            break

    main_count = sum(item.features.primary_domain is Domain.MAIN for item in selected)
    state_valid = not breadth_recovery or (
        promotion_count == 2 and low_crowding_exploration_count >= 1
    )
    unexhausted_valid = unexhausted_count >= required_unexhausted
    return (
        selected
        if len(selected) == size
        and main_count >= required_main
        and state_valid
        and unexhausted_valid
        else []
    )


def _quality_rank(candidate: ScoredCandidate) -> int:
    return {
        "verified": 3,
        "single_source": 2,
        "degraded": 1,
        "conflicted": 0,
        "missing": 0,
    }[candidate.features.data_quality.value]


def _has_critical_evidence_gap(candidate: ScoredCandidate) -> bool:
    features = candidate.features
    return (
        not features.has_direct_catalyst
        or not features.financial_not_deteriorating
        or features.independent_evidence_sources < 2
    )
