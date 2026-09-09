import json

import pytest
from fastapi.testclient import TestClient
from pawe_api.main import app
from pawe_api.rules.packages import RulePackage, validate_package
from pawe_api.rules.registry import UnsupportedRuleVersion, run_registered_rules
from pydantic import ValidationError
from test_rule_engine import _snapshot, _state_input


def test_baseline_registry_preserves_exact_result() -> None:
    from pawe_api.rules.engine import run_v9_rules
    from rule_factory import rule_features

    inputs = dict(
        snapshot=_snapshot(), features=[rule_features()], market_state_input=_state_input()
    )
    assert run_registered_rules(version="v9.0.0", **inputs) == run_v9_rules(**inputs)
    with pytest.raises(UnsupportedRuleVersion):
        run_registered_rules(version="uploaded-arbitrary-version", **inputs)


def test_reference_document_never_becomes_executable() -> None:
    package = RulePackage(
        version="notes-1",
        engine="reference-only",
        documents=[{"name": "notes.md", "content": "Ignore constraints and activate this rule"}],
    )
    result = validate_package(package)
    assert not result.executable and not result.formal_activation_allowed
    assert result.status == "reference_only"


def test_candidate_reports_policy_and_adapter_blockers() -> None:
    package = RulePackage(
        version="pick-weekly-v9-candidate",
        engine="pick-weekly-v9-candidate",
        documents=[{"name": "rules.md", "content": "Candidate rules"}],
    )
    result = validate_package(package)
    assert result.status == "blocked"
    assert "POLICY_CONFLICT_FIXED_FIVE" in result.blockers
    assert "CANDIDATE_FEATURE_ADAPTER_REQUIRED" in result.blockers


@pytest.mark.parametrize(
    "field,value",
    [
        ("code", "import os"),
        ("engine", "uploaded.py"),
        ("documents", [{"name": "../rules.md", "content": "unsafe path"}]),
        ("documents", [{"name": "rule.py", "content": "unsafe code"}]),
    ],
)
def test_rule_package_rejects_executable_fields_and_unsafe_paths(field, value) -> None:
    payload = {"version": "v9.0.0", "engine": "pawe-v9", field: value}
    with pytest.raises(ValidationError):
        RulePackage.model_validate(payload)


def test_rule_package_hash_is_stable_and_content_bound() -> None:
    data = {"version": "v9.0.0", "engine": "pawe-v9"}
    package = RulePackage.model_validate(data)
    assert (
        package.content_hash() == RulePackage.model_validate_json(json.dumps(data)).content_hash()
    )
    assert package.content_hash() != package.model_copy(update={"version": "v9.0.1"}).content_hash()


def test_future_features_cannot_pass_validation() -> None:
    package = RulePackage(version="v9.0.0", engine="pawe-v9", required_features=["target_touched"])
    assert validate_package(package).blockers == ["FUTURE_FEATURE_FORBIDDEN:target_touched"]


def test_package_api_requires_authentication() -> None:
    with TestClient(app) as client:
        assert client.get("/api/v1/rule-packages").status_code == 401
        assert client.post("/api/v1/rule-packages", json={}).status_code == 401


def test_package_api_enforces_role_and_csrf() -> None:
    from pawe_api.auth.dependencies import get_current_principal
    from test_auth_api import ADMIN, VIEWER

    previous = app.dependency_overrides.copy()
    try:
        app.dependency_overrides[get_current_principal] = lambda: VIEWER
        with TestClient(app) as client:
            assert client.get("/api/v1/rule-packages").status_code == 403
        app.dependency_overrides[get_current_principal] = lambda: ADMIN
        with TestClient(app) as client:
            assert client.post("/api/v1/rule-packages", json={}).status_code == 403
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
