"""Explicit executable allowlist, separate from untrusted uploaded metadata."""

from pawe_api.data.snapshot import FrozenSnapshot
from pawe_api.rules.engine import RULE_VERSION, RuleRunResult, run_v9_rules
from pawe_api.rules.market_state import MarketStateInput
from pawe_api.rules.models import RuleFeatures


class UnsupportedRuleVersion(ValueError):
    pass


def run_registered_rules(
    *,
    version: str,
    snapshot: FrozenSnapshot,
    features: list[RuleFeatures],
    market_state_input: MarketStateInput,
    candidate_overheat_ratio: float = 0.0,
) -> RuleRunResult:
    # A database status or an uploaded file cannot add code to this allowlist.
    if version != RULE_VERSION:
        raise UnsupportedRuleVersion("rule version has no verified executable adapter")
    return run_v9_rules(
        snapshot=snapshot,
        features=features,
        market_state_input=market_state_input,
        candidate_overheat_ratio=candidate_overheat_ratio,
    )
