import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pawe_api.contracts import MarketState
from pawe_api.data.snapshot import FrozenSnapshot
from pawe_api.rules.eligibility import DEFAULT_HARD_CONSTRAINTS, HardConstraintConfig
from pawe_api.rules.market_state import (
    MarketStateDecision,
    MarketStateInput,
    determine_market_state,
)
from pawe_api.rules.models import RuleFeatures, ScoredCandidate
from pawe_api.rules.portfolio import (
    DEFAULT_PORTFOLIO_CONFIG,
    PortfolioConfig,
    PortfolioResult,
    build_portfolio,
)
from pawe_api.rules.scoring import score_candidate

RULE_VERSION = "v9.0.0"


@dataclass(frozen=True, slots=True)
class RuleRunResult:
    rule_version: str
    snapshot_hash: str
    market_state: MarketState
    candidates: tuple[ScoredCandidate, ...]
    baseline: PortfolioResult
    flags: tuple[str, ...]
    auto_publish_allowed: bool
    fingerprint: str


def run_v9_rules(
    *,
    snapshot: FrozenSnapshot,
    features: list[RuleFeatures],
    market_state_input: MarketStateInput,
    candidate_overheat_ratio: float = 0.0,
    hard_constraints: HardConstraintConfig = DEFAULT_HARD_CONSTRAINTS,
    portfolio_config: PortfolioConfig = DEFAULT_PORTFOLIO_CONFIG,
) -> RuleRunResult:
    _validate_unique_codes(features)
    state_decision = determine_market_state(market_state_input)
    scored = tuple(
        sorted(
            (score_candidate(item, hard_constraints) for item in features),
            key=_candidate_sort_key,
        )
    )
    baseline = build_portfolio(
        list(scored),
        portfolio_config,
        market_state=state_decision.state,
        candidate_overheat_ratio=candidate_overheat_ratio,
    )
    flags = _result_flags(state_decision, baseline)
    auto_publish_allowed = bool(baseline.items) and "STATE_DATA_DEGRADED" not in flags
    fingerprint = _fingerprint(
        snapshot=snapshot,
        state_decision=state_decision,
        candidates=scored,
        baseline=baseline,
        flags=flags,
    )
    return RuleRunResult(
        rule_version=RULE_VERSION,
        snapshot_hash=snapshot.content_hash,
        market_state=state_decision.state,
        candidates=scored,
        baseline=baseline,
        flags=flags,
        auto_publish_allowed=auto_publish_allowed,
        fingerprint=fingerprint,
    )


def _validate_unique_codes(features: list[RuleFeatures]) -> None:
    codes = [item.stock_code for item in features]
    if len(codes) != len(set(codes)):
        raise ValueError("rule run requires unique stock codes")


def _candidate_sort_key(candidate: ScoredCandidate) -> tuple[float, int, int, int, float, str]:
    quality_rank = {
        "verified": 3,
        "single_source": 2,
        "degraded": 1,
        "conflicted": 0,
        "missing": 0,
    }[candidate.features.data_quality.value]
    return (
        -candidate.rule_score,
        -candidate.features.industry_chain_priority,
        -quality_rank,
        -int(candidate.features.previous_close_positive),
        -candidate.features.avg_amount_20d,
        candidate.features.stock_code,
    )


def _result_flags(
    state_decision: MarketStateDecision,
    baseline: PortfolioResult,
) -> tuple[str, ...]:
    flags = list(state_decision.flags)
    if not baseline.items:
        flags.append("NO_ELIGIBLE_CANDIDATE")
    elif baseline.shortage:
        flags.append("CANDIDATE_SHORTAGE")
    if baseline.low_confidence:
        flags.append("LOW_CONFIDENCE")
    return tuple(flags)


def _fingerprint(
    *,
    snapshot: FrozenSnapshot,
    state_decision: MarketStateDecision,
    candidates: tuple[ScoredCandidate, ...],
    baseline: PortfolioResult,
    flags: tuple[str, ...],
) -> str:
    payload: dict[str, Any] = {
        "rule_version": RULE_VERSION,
        "snapshot_hash": snapshot.content_hash,
        "market_state": state_decision.state.value,
        "state_flags": list(state_decision.flags),
        "flags": list(flags),
        "candidates": [
            {
                "stock_code": candidate.features.stock_code,
                "score": candidate.rule_score,
                "bucket": candidate.bucket.value,
                "exclusion_reasons": list(candidate.exclusion_reasons),
                "score_breakdown": candidate.score_breakdown,
            }
            for candidate in candidates
        ],
        "baseline_codes": [item.features.stock_code for item in baseline.items],
        "shortage": baseline.shortage,
        "shortage_reason": baseline.shortage_reason,
        "low_confidence": baseline.low_confidence,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
