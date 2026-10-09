import asyncio
import json
from typing import TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from pawe_api.ai.provider import AIProviderConfig, AIProviderError, AIProviderResult

T = TypeVar("T", bound=BaseModel)


class OpenAIResponsesProvider:
    def __init__(self, api_key: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key, max_retries=1)

    async def complete(
        self,
        config: AIProviderConfig,
        prompt: str,
        payload: dict[str, object],
        output_model: type[T],
    ) -> AIProviderResult:
        try:
            # SDK parsing converts nested Pydantic models to OpenAI's strict schema,
            # including additionalProperties=false and required defaulted fields.
            # The deadline covers retries/backoff as well as the request itself.
            async with self.client, asyncio.timeout(config.timeout_seconds):
                response = await self.client.responses.parse(
                    model=config.model,
                    input=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    text_format=output_model,
                    reasoning=(
                        {"effort": config.reasoning_effort}
                        if config.model.startswith("gpt-6.1-sol")
                        else None
                    ),
                    store=False,
                    max_output_tokens=config.max_output_tokens,
                    timeout=config.timeout_seconds,
                )
            if response.status != "completed":
                raise AIProviderError(
                    "OPENAI_OUTPUT_INCOMPLETE", "OpenAI 未完成输出，请稍后重试或检查输出额度。"
                )
            if any(
                part.type == "refusal"
                for item in response.output
                if item.type == "message"
                for part in item.content
            ):
                raise AIProviderError("OPENAI_REFUSAL", "OpenAI 拒绝了本次请求，未生成业务结果。")
            if response.output_parsed is None:
                raise AIProviderError("OPENAI_EMPTY_OUTPUT", "OpenAI 未返回可用的结构化结果。")
            output = response.output_parsed.model_dump(mode="json")
            usage = response.usage.model_dump(mode="json") if response.usage is not None else {}
            return AIProviderResult("openai_responses", config.model, output, usage)
        except AIProviderError:
            raise
        except (APITimeoutError, TimeoutError) as exc:
            raise AIProviderError("OPENAI_TIMEOUT", "OpenAI 调用超时，请稍后重试。") from exc
        except APIConnectionError as exc:
            raise AIProviderError(
                "OPENAI_CONNECTION_FAILED", "服务器无法连接 OpenAI，请检查网络或代理。"
            ) from exc
        except APIStatusError as exc:
            # Never expose the raw body/message: it can echo keys or input facts.
            if exc.status_code == 401:
                code, message = "OPENAI_AUTH_FAILED", "OpenAI API Key 无效或已撤销，请重新关联。"
            elif exc.status_code == 403:
                code, message = (
                    "OPENAI_PERMISSION_DENIED",
                    "OpenAI 拒绝访问，请检查账户及模型权限。",
                )
            elif exc.status_code == 404:
                code, message = "OPENAI_MODEL_UNAVAILABLE", "指定 OpenAI 模型不存在或账户无权调用。"
            elif exc.status_code == 429:
                if exc.code == "insufficient_quota":
                    code, message = (
                        "OPENAI_QUOTA_EXHAUSTED",
                        "OpenAI API 额度不足，请检查余额及预算。",
                    )
                else:
                    code, message = "OPENAI_RATE_LIMITED", "OpenAI 请求限流，请稍后重试。"
            elif exc.status_code >= 500:
                code, message = "OPENAI_SERVICE_UNAVAILABLE", "OpenAI 服务暂不可用，请稍后重试。"
            elif exc.code in {"invalid_json_schema", "invalid_schema"}:
                code, message = (
                    "OPENAI_SCHEMA_REJECTED",
                    "OpenAI 拒绝了输出契约，请联系管理员检查接口。",
                )
            else:
                code, message = (
                    "OPENAI_INVALID_REQUEST",
                    "OpenAI 请求参数不受支持，请检查模型配置。",
                )
            raise AIProviderError(code, message) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise AIProviderError(
                "INVALID_STRUCTURED_OUTPUT", "AI 输出不完整或不符合契约，未生成业务结果。"
            ) from exc
        except Exception as exc:
            raise AIProviderError(
                "OPENAI_REQUEST_FAILED", "OpenAI 调用失败，未生成业务结果；请查看调用审计。"
            ) from exc
