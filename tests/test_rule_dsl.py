from pawe_api.contracts import RuleProposalRequest
from pawe_api.experiments.rule_dsl import validate_rule_proposal


def _proposal(**updates: object) -> RuleProposalRequest:
    payload: dict[str, object] = {
        "proposal_id": "exp_rule_2026w33_001",
        "base_rule_version": "v9.0.0",
        "scope": "scoring",
        "hypothesis": "提高产业链成组强度权重可以减少由单一锚点驱动的候选。",
        "conditions": {
            "all": [
                {"feature": "sector_up_ratio_5d", "op": "gte", "value": 0.7},
                {"feature": "single_anchor_crowded", "op": "eq", "value": False},
            ]
        },
        "changes": [{"parameter": "sector_strength_weight", "value": 22}],
        "objective": ["touch_10_rate", "anchor_contribution_share"],
        "required_features": ["sector_up_ratio_5d", "single_anchor_crowded"],
        "expected_effect": "提高横向验证充分的非锚点候选排序。",
        "invalidation_conditions": ["单一锚点贡献占比继续上升"],
        "rollback_version": "v9.0.0",
    }
    payload.update(updates)
    return RuleProposalRequest.model_validate(payload)


def test_valid_rule_dsl_is_static_and_feature_bound() -> None:
    result = validate_rule_proposal(_proposal())
    assert result.valid
    assert result.errors == ()
    assert result.referenced_features == ("sector_up_ratio_5d", "single_anchor_crowded")


def test_rule_dsl_rejects_hard_constraint_changes_and_future_labels() -> None:
    proposal = _proposal(
        conditions={"feature": "target_touched", "op": "eq", "value": True},
        changes=[{"parameter": "max_size", "value": 6}],
        required_features=["target_touched"],
    )
    result = validate_rule_proposal(proposal)
    assert not result.valid
    assert "FUTURE_FEATURE_FORBIDDEN:target_touched" in result.errors
    assert "PROTECTED_PARAMETER:max_size" in result.errors


def test_rule_dsl_rejects_unknown_and_out_of_range_parameters() -> None:
    unknown = validate_rule_proposal(
        _proposal(changes=[{"parameter": "execute_python", "value": 1}])
    )
    out_of_range = validate_rule_proposal(
        _proposal(changes=[{"parameter": "sector_strength_weight", "value": 99}])
    )
    assert "UNKNOWN_PARAMETER:execute_python" in unknown.errors
    assert "PARAMETER_OUT_OF_RANGE:sector_strength_weight" in out_of_range.errors
