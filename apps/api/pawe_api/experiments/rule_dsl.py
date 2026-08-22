from dataclasses import dataclass, fields
from numbers import Real
from typing import Any

from pawe_api.contracts import RuleCondition, RuleProposalRequest
from pawe_api.rules.engine import RULE_VERSION
from pawe_api.rules.models import RuleFeatures

REGISTERED_FEATURES = frozenset(field.name for field in fields(RuleFeatures))
FORBIDDEN_FUTURE_FEATURES = frozenset(
    {
        "week_high_return",
        "week_close_return",
        "target_touched",
        "target_touch_date",
        "drawdown_before_touch",
    }
)
PARAMETER_BOUNDS: dict[str, tuple[float, float]] = {
    "price_structure_weight": (0.0, 40.0),
    "sector_strength_weight": (0.0, 40.0),
    "liquidity_weight": (0.0, 30.0),
    "market_fit_weight": (0.0, 30.0),
    "history_weight": (0.0, 20.0),
    "fundamentals_weight": (0.0, 20.0),
    "risk_quality_weight": (0.0, 15.0),
}
PROTECTED_PARAMETERS = frozenset(
    {
        "target_size",
        "max_size",
        "target_return",
        "star_allowed",
        "min_listing_days",
        "min_avg_amount_20d",
        "ai_adjustment_min",
        "ai_adjustment_max",
        "ai_max_replacements",
    }
)


@dataclass(frozen=True, slots=True)
class DslValidationResult:
    valid: bool
    errors: tuple[str, ...]
    referenced_features: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "referenced_features": list(self.referenced_features),
            "validator_version": "rule-dsl-validator-1",
        }


def validate_rule_proposal(request: RuleProposalRequest) -> DslValidationResult:
    errors: list[str] = []
    referenced: set[str] = set()
    if request.base_rule_version != RULE_VERSION:
        errors.append("BASE_RULE_VERSION_MISMATCH")
    if request.rollback_version != RULE_VERSION:
        errors.append("ROLLBACK_VERSION_NOT_ACTIVE_BASELINE")

    node_count = _validate_condition(request.conditions, referenced, errors, depth=1)
    if node_count > 100:
        errors.append("CONDITION_NODE_LIMIT_EXCEEDED")

    required = set(request.required_features)
    for feature in sorted(required):
        if feature in FORBIDDEN_FUTURE_FEATURES:
            errors.append(f"FUTURE_FEATURE_FORBIDDEN:{feature}")
        elif feature not in REGISTERED_FEATURES:
            errors.append(f"UNKNOWN_FEATURE:{feature}")
    missing_declarations = referenced - required
    for feature in sorted(missing_declarations):
        errors.append(f"FEATURE_NOT_DECLARED:{feature}")

    for change in request.changes:
        if change.parameter in PROTECTED_PARAMETERS:
            errors.append(f"PROTECTED_PARAMETER:{change.parameter}")
            continue
        bounds = PARAMETER_BOUNDS.get(change.parameter)
        if bounds is None:
            errors.append(f"UNKNOWN_PARAMETER:{change.parameter}")
            continue
        if not bounds[0] <= change.value <= bounds[1]:
            errors.append(f"PARAMETER_OUT_OF_RANGE:{change.parameter}")

    return DslValidationResult(
        valid=not errors,
        errors=tuple(dict.fromkeys(errors)),
        referenced_features=tuple(sorted(referenced)),
    )


def _validate_condition(
    condition: RuleCondition,
    referenced: set[str],
    errors: list[str],
    *,
    depth: int,
) -> int:
    if depth > 8:
        errors.append("CONDITION_DEPTH_LIMIT_EXCEEDED")
        return 1
    children = condition.all or condition.any
    if children is not None:
        return 1 + sum(
            _validate_condition(child, referenced, errors, depth=depth + 1)
            for child in children
        )
    if condition.negate is not None:
        return 1 + _validate_condition(condition.negate, referenced, errors, depth=depth + 1)

    feature = condition.feature
    operation = condition.op
    if feature is None or operation is None:
        errors.append("INVALID_CONDITION_SHAPE")
        return 1
    referenced.add(feature)
    if feature in FORBIDDEN_FUTURE_FEATURES:
        errors.append(f"FUTURE_FEATURE_FORBIDDEN:{feature}")
    elif feature not in REGISTERED_FEATURES:
        errors.append(f"UNKNOWN_FEATURE:{feature}")
    _validate_comparison_value(operation, condition.value, errors)
    return 1


def _validate_comparison_value(operation: str, value: Any, errors: list[str]) -> None:
    if operation == "in":
        if not isinstance(value, list) or not value or len(value) > 50:
            errors.append("INVALID_IN_VALUE")
        return
    if operation == "between":
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(isinstance(item, Real) and not isinstance(item, bool) for item in value)
            or value[0] > value[1]
        ):
            errors.append("INVALID_BETWEEN_VALUE")
        return
    if isinstance(value, (dict, list)) or value is None:
        errors.append("INVALID_SCALAR_VALUE")
