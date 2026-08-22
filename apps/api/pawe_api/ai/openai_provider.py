from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from pawe_api.ai.provider import AIProviderConfig, AIProviderError, AIProviderResult

T = TypeVar("T", bound=BaseModel)


class OpenAIResponsesProvider:
    def __init__(self, api_key: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)

    async def complete(
        self,
        config: AIProviderConfig,
        prompt: str,
        payload: dict[str, object],
        output_model: type[T],
    ) -> AIProviderResult:
        try:
            response = await self.client.responses.create(
                model=config.model,
                input=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": str(payload)},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": output_model.__name__,
                        "strict": True,
                        "schema": output_model.model_json_schema(),
                    }
                },
                max_output_tokens=config.max_output_tokens,
                timeout=config.timeout_seconds,
            )
            raw = response.output_text
            output = output_model.model_validate_json(raw).model_dump(mode="json")
            usage = response.usage.model_dump(mode="json") if response.usage is not None else {}
            return AIProviderResult("openai_responses", config.model, output, usage)
        except Exception as exc:
            raise AIProviderError(
                "OPENAI_REQUEST_FAILED", "OpenAI Responses request failed"
            ) from exc
