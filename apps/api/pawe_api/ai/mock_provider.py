from typing import TypeVar

from pydantic import BaseModel

from pawe_api.ai.contracts import (
    ErrorAttributionOutput,
    RuleEvolutionOutput,
    WeeklyReviewOutput,
    WeeklySelectionOutput,
)
from pawe_api.ai.provider import AIProviderConfig, AIProviderResult

T = TypeVar("T", bound=BaseModel)


class DeterministicMockProvider:
    async def complete(
        self,
        config: AIProviderConfig,
        prompt: str,
        payload: dict[str, object],
        output_model: type[T],
    ) -> AIProviderResult:
        del prompt
        if output_model is WeeklySelectionOutput:
            rows = payload.get("candidates", [])
            if not isinstance(rows, list):
                rows = []
            analyses = [
                {
                    "stock_code": row["stock_code"],
                    "adjustment": 0,
                    "evidence_ids": row.get("evidence_ids", []),
                    "reason": "确定性 Mock 仅复述服务端候选，不改变排序。",
                }
                for row in rows
                if isinstance(row, dict)
            ]
            output: dict[str, object] = {"analyses": analyses[:5]}
        elif output_model is WeeklyReviewOutput:
            output = {"summary": "确定性指标保持不变；AI 仅生成结构化观察。", "abnormalities": []}
        elif output_model is ErrorAttributionOutput:
            output = {
                "taxonomy": "confirmation_insufficient",
                "confidence": "low",
                "hypothesis": "当前确定性事实不足以证明单一归因，需人工核验。",
                "counterfactual_allowed": False,
            }
        elif output_model is RuleEvolutionOutput:
            output = {
                "proposal_id": "ai-proposal-shadow",
                "hypothesis": "样本不足，仅保留受限规则提案草案供人工讨论。",
                "parameter": "price_structure_weight",
                "value": 1.0,
                "required_features": ["return_5d"],
                "objective": ["touch_10_rate"],
                "invalidation_conditions": ["未来验证失败"],
            }
        else:
            raise ValueError("unsupported mock output model")
        return AIProviderResult(
            "mock", config.model, output, {"prompt_tokens": 0, "completion_tokens": 0}
        )
