"""Data-only rule packages. Uploaded documents never become executable Python."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RuleDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z0-9_-]+\.md$", max_length=80)
    content: str = Field(min_length=1, max_length=150_000)


class RulePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1"] = "1"
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    engine: Literal["pawe-v9", "pick-weekly-v9-candidate", "reference-only"]
    base_version: Literal["v9.0.0"] = "v9.0.0"
    documents: list[RuleDocument] = Field(default_factory=list, max_length=8)
    # No arbitrary expressions, imports, hooks, file paths or URLs are accepted.
    required_features: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("required_features")
    @classmethod
    def validate_feature_names(cls, values: list[str]) -> list[str]:
        if any(not name.isidentifier() or len(name) > 80 for name in values):
            raise ValueError("invalid feature identifier")
        if len(set(values)) != len(values):
            raise ValueError("duplicate required feature")
        return values

    @model_validator(mode="after")
    def validate_documents(self) -> "RulePackage":
        names = [document.name for document in self.documents]
        if len(set(names)) != len(names):
            raise ValueError("duplicate document name")
        if sum(len(d.content.encode("utf-8")) for d in self.documents) > 512_000:
            raise ValueError("documents exceed 512 KB")
        if self.engine != "pawe-v9" and not self.documents:
            raise ValueError("source documents are required")
        return self

    def content_hash(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class PackageValidation(BaseModel):
    status: Literal["compatible", "blocked", "reference_only"]
    executable: bool
    formal_activation_allowed: Literal[False] = False
    blockers: list[str]
    warnings: list[str]
    validator_version: str = "rule-package-validator-1"


def validate_package(package: RulePackage) -> PackageValidation:
    from pawe_api.experiments.rule_dsl import FORBIDDEN_FUTURE_FEATURES, REGISTERED_FEATURES

    blockers = [
        f"FUTURE_FEATURE_FORBIDDEN:{name}"
        if name in FORBIDDEN_FUTURE_FEATURES
        else f"UNSUPPORTED_FEATURE:{name}"
        for name in package.required_features
        if name in FORBIDDEN_FUTURE_FEATURES or name not in REGISTERED_FEATURES
    ]
    if package.engine == "reference-only":
        return PackageValidation(
            status="reference_only",
            executable=False,
            blockers=blockers,
            warnings=["DOCUMENTS_ARE_NOT_EXECUTABLE_RULES"],
        )
    if package.engine == "pick-weekly-v9-candidate":
        blockers.extend(
            [
                "CANDIDATE_FEATURE_ADAPTER_REQUIRED",
                "POLICY_CONFLICT_FIXED_FIVE",
                "POLICY_CONFLICT_OVERHEAT_FALLBACK",
                "POLICY_CONFLICT_PROSPECTIVE_CUTOFF",
                "POLICY_CONFLICT_FULL_FIVE_DAY_WEEK",
                "WALK_FORWARD_AND_LIVE_SHADOW_REQUIRED",
            ]
        )
    elif package.version != "v9.0.0":
        blockers.append("BASELINE_ENGINE_VERSION_MISMATCH")
    return PackageValidation(
        status="blocked" if blockers else "compatible",
        executable=not blockers,
        blockers=blockers,
        warnings=["UPLOAD_DOES_NOT_ACTIVATE", "SOURCE_TEXT_DOES_NOT_OVERRIDE_ENGINE"],
    )
