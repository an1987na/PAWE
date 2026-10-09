import json
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI
from pawe_api.ai import openai_provider
from pawe_api.ai.contracts import (
    ErrorAttributionOutput,
    RuleEvolutionOutput,
    WeeklyReviewOutput,
    WeeklySelectionOutput,
)
from pawe_api.ai.provider import AIProviderConfig, AIProviderError
from pydantic import BaseModel

MODEL = "gpt-6.1-sol"
SECRET = "sk-test-must-not-leak-in-errors"
CONFIG = AIProviderConfig("test", MODEL, True, 3, 4000)


def bind_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    def client(**kwargs: Any) -> AsyncOpenAI:
        assert kwargs["max_retries"] == 1
        return AsyncOpenAI(
            api_key=kwargs["api_key"],
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(openai_provider, "AsyncOpenAI", client)


def response_body(output: list[dict[str, object]], status: str = "completed") -> dict[str, object]:
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "model": MODEL,
        "status": status,
        "output": output,
        "usage": None,
    }


def assert_strict(schema: Any) -> None:
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
        for value in schema.values():
            assert_strict(value)
    elif isinstance(schema, list):
        for value in schema:
            assert_strict(value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output_model", "data"),
    [
        (
            WeeklySelectionOutput,
            {
                "analyses": [
                    {
                        "stock_code": "600000",
                        "adjustment": 0,
                        "evidence_ids": ["e1"],
                        "reason": "仅有冻结候选证据，不调整规则结果。",
                    }
                ]
            },
        ),
        (
            WeeklyReviewOutput,
            {"summary": "确定性指标保持不变，当前仅作复盘观察。", "abnormalities": []},
        ),
        (
            ErrorAttributionOutput,
            {
                "taxonomy": "confirmation_insufficient",
                "confidence": "low",
                "hypothesis": "当前事实不能证明因果关系，需人工核验。",
                "counterfactual_allowed": False,
            },
        ),
        (
            RuleEvolutionOutput,
            {
                "proposal_id": "ai-test-001",
                "hypothesis": "只形成待验证的评分假设，不改变已发布规则。",
                "parameter": "price_structure_weight",
                "value": 1.0,
                "required_features": ["return_5d"],
                "objective": ["touch_10_rate"],
                "invalidation_conditions": ["时序验证失败"],
            },
        ),
    ],
)
async def test_all_capabilities_send_strict_wire_schema_and_json(
    monkeypatch: pytest.MonkeyPatch, output_model: type[BaseModel], data: dict[str, object]
) -> None:
    payload = {"中文": "quoted ' input", "available": True, "missing": None}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == MODEL
        assert body["store"] is False
        assert body["reasoning"]["effort"] == "low"
        assert json.loads(body["input"][1]["content"]) == payload
        assert_strict(body["text"]["format"]["schema"])
        return httpx.Response(
            200,
            json=response_body(
                [
                    {
                        "type": "message",
                        "id": "msg_test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": json.dumps(data), "annotations": []}
                        ],
                    }
                ]
            ),
        )

    bind_transport(monkeypatch, handler)
    provider = openai_provider.OpenAIResponsesProvider(SECRET)
    result = await provider.complete(CONFIG, "test", payload, output_model)
    assert result.output == output_model.model_validate(data).model_dump(mode="json")
    assert result.provider == "openai_responses"
    assert provider.client.is_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (401, "invalid_api_key", "OPENAI_AUTH_FAILED"),
        (403, "permission_denied", "OPENAI_PERMISSION_DENIED"),
        (404, "model_not_found", "OPENAI_MODEL_UNAVAILABLE"),
        (429, "insufficient_quota", "OPENAI_QUOTA_EXHAUSTED"),
        (429, "rate_limit_exceeded", "OPENAI_RATE_LIMITED"),
        (400, "invalid_json_schema", "OPENAI_SCHEMA_REJECTED"),
        (400, "unsupported_parameter", "OPENAI_INVALID_REQUEST"),
        (503, "server_error", "OPENAI_SERVICE_UNAVAILABLE"),
    ],
)
async def test_safe_actionable_api_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str, expected: str
) -> None:
    bind_transport(
        monkeypatch,
        lambda _: httpx.Response(
            status, json={"error": {"message": f"secret {SECRET}; private payload", "code": code}}
        ),
    )
    with pytest.raises(AIProviderError) as error:
        await openai_provider.OpenAIResponsesProvider(SECRET).complete(
            CONFIG, "test", {}, WeeklyReviewOutput
        )
    assert error.value.code == expected
    assert SECRET not in str(error.value)
    assert "private payload" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (response_body([], "incomplete"), "OPENAI_OUTPUT_INCOMPLETE"),
        (response_body([]), "OPENAI_EMPTY_OUTPUT"),
        (
            response_body(
                [
                    {
                        "type": "message",
                        "id": "msg_test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": SECRET}],
                    }
                ]
            ),
            "OPENAI_REFUSAL",
        ),
        (
            response_body(
                [
                    {
                        "type": "message",
                        "id": "msg_test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": '{"summary":', "annotations": []}
                        ],
                    }
                ]
            ),
            "INVALID_STRUCTURED_OUTPUT",
        ),
    ],
)
async def test_unusable_responses_never_become_business_success(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, object], expected: str
) -> None:
    bind_transport(monkeypatch, lambda _: httpx.Response(200, json=body))
    with pytest.raises(AIProviderError) as error:
        await openai_provider.OpenAIResponsesProvider(SECRET).complete(
            CONFIG, "test", {}, WeeklyReviewOutput
        )
    assert error.value.code == expected
    assert SECRET not in str(error.value)
