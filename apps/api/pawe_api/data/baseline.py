import hashlib
import json
from dataclasses import asdict
from typing import Any

from pawe_api.contracts import DataQuality, MarketState
from pawe_api.rules.market_state import MarketStateInput, PoolMetrics
from pawe_api.rules.models import Board, Domain, RuleFeatures, StateFit, StockStatus

FEATURE_SCHEMA_VERSION = "v9-feature-1"
STATE_INPUT_SCHEMA_VERSION = "v9-state-input-1"


def canonical_payload_hash(payload: dict[str, object]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def serialize_rule_features(features: RuleFeatures) -> dict[str, object]:
    return _json_mapping(asdict(features))


def deserialize_rule_features(payload: dict[str, object]) -> RuleFeatures:
    values = dict(payload)
    values["board"] = Board(_string(values, "board"))
    values["status"] = StockStatus(_string(values, "status"))
    values["primary_domain"] = Domain(_string(values, "primary_domain"))
    values["state_fit"] = StateFit(_string(values, "state_fit"))
    values["data_quality"] = DataQuality(_string(values, "data_quality"))
    return RuleFeatures(**values)  # type: ignore[arg-type]


def serialize_market_state_input(inputs: MarketStateInput) -> dict[str, object]:
    return _json_mapping(asdict(inputs))


def deserialize_market_state_input(payload: dict[str, object]) -> MarketStateInput:
    values = dict(payload)
    values["previous_state"] = MarketState(_string(values, "previous_state"))
    values["main_pool"] = PoolMetrics(**_mapping(values, "main_pool"))
    values["reserve_pool"] = PoolMetrics(**_mapping(values, "reserve_pool"))
    return MarketStateInput(**values)  # type: ignore[arg-type]


def _json_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_payload(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _json_mapping(value: dict[str, Any]) -> dict[str, object]:
    return {str(key): _json_payload(item) for key, item in value.items()}


def _string(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _mapping(payload: dict[str, object], field: str) -> dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value
