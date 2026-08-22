import hashlib
import json
from typing import Any


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def safe_context(value: dict[str, object]) -> dict[str, object]:
    """Keep audit context to identifiers and counts; never persist prompts or secrets."""
    return {
        key: value[key]
        for key in value
        if key.endswith("_id") or key.endswith("_count") or key in {"week_id", "stage", "status"}
    }
