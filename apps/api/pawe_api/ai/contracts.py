from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CandidateAnalysis(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$")
    adjustment: int = Field(ge=-10, le=10)
    evidence_ids: list[str] = Field(max_length=20)
    reason: str = Field(min_length=8, max_length=320)


class WeeklySelectionOutput(BaseModel):
    analyses: list[CandidateAnalysis] = Field(max_length=5)

    @model_validator(mode="after")
    def unique_codes(self) -> "WeeklySelectionOutput":
        codes = [item.stock_code for item in self.analyses]
        if len(codes) != len(set(codes)):
            raise ValueError("candidate codes must be unique")
        return self


class WeeklyReviewOutput(BaseModel):
    summary: str = Field(min_length=8, max_length=500)
    abnormalities: list[str] = Field(max_length=8)


class ErrorAttributionOutput(BaseModel):
    taxonomy: Literal[
        "market_state_error",
        "rotation_lag",
        "continuation_overreach",
        "overheat_filter_loose",
        "overheat_filter_strict",
        "stock_selection_error",
        "catalyst_error",
        "confirmation_insufficient",
        "data_anomaly",
        "candidate_coverage_insufficient",
        "anchor_distortion",
        "ai_swap_error",
        "human_override_error",
    ]
    confidence: Literal["low", "medium", "high"]
    hypothesis: str = Field(min_length=12, max_length=800)
    counterfactual_allowed: bool = False


class RuleEvolutionOutput(BaseModel):
    proposal_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{7,63}$")
    hypothesis: str = Field(min_length=20, max_length=1000)
    parameter: str = Field(min_length=1, max_length=64)
    value: float
    required_features: list[str] = Field(min_length=1, max_length=50)
    objective: list[Literal["touch_10_rate", "close_retention", "probability_calibration"]] = Field(
        min_length=1, max_length=3
    )
    invalidation_conditions: list[str] = Field(min_length=1, max_length=20)
