from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class AIProviderConfig:
    capability: str
    model: str
    enabled: bool
    timeout_seconds: int
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class AIProviderResult:
    provider: str
    model: str
    output: dict[str, object]
    usage: dict[str, object]


class AIProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AIProvider(Protocol):
    async def complete(
        self,
        config: AIProviderConfig,
        prompt: str,
        payload: dict[str, object],
        output_model: type[T],
    ) -> AIProviderResult: ...


def validate_provider_output[T: BaseModel](result: AIProviderResult, output_model: type[T]) -> T:
    try:
        return output_model.model_validate(result.output)
    except Exception as exc:
        raise AIProviderError(
            "INVALID_STRUCTURED_OUTPUT", "AI output did not match the schema"
        ) from exc
