from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PAWE_",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+asyncpg://pawe:pawe-local-only@localhost:5432/pawe"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-sol"
    ai_credential_encryption_key: str | None = None
    ai_enabled: bool = False
    ai_weekly_selection_enabled: bool = False
    ai_weekly_selection_model: str | None = None
    ai_weekly_selection_timeout_seconds: int = Field(default=20, ge=1, le=120)
    ai_weekly_selection_max_output_tokens: int = Field(default=1200, ge=128, le=8000)
    ai_weekly_review_enabled: bool = False
    ai_weekly_review_model: str | None = None
    ai_weekly_review_timeout_seconds: int = Field(default=20, ge=1, le=120)
    ai_weekly_review_max_output_tokens: int = Field(default=1000, ge=128, le=8000)
    ai_error_attribution_enabled: bool = False
    ai_error_attribution_model: str | None = None
    ai_error_attribution_timeout_seconds: int = Field(default=20, ge=1, le=120)
    ai_error_attribution_max_output_tokens: int = Field(default=1000, ge=128, le=8000)
    ai_rule_evolution_enabled: bool = False
    ai_rule_evolution_model: str | None = None
    ai_rule_evolution_timeout_seconds: int = Field(default=20, ge=1, le=120)
    ai_rule_evolution_max_output_tokens: int = Field(default=1200, ge=128, le=8000)
    experiment_activation_enabled: bool = False
    weekly_preopen_hour: int = Field(default=8, ge=0, le=23)
    weekly_preopen_minute: int = Field(default=30, ge=0, le=59)
    weekly_data_refresh_hour: int = Field(default=18, ge=0, le=23)
    weekly_data_refresh_minute: int = Field(default=0, ge=0, le=59)
    daily_brief_hour: int = Field(default=15, ge=0, le=23)
    daily_brief_minute: int = Field(default=30, ge=0, le=59)
    job_poll_seconds: int = Field(default=2, ge=1, le=60)
    session_ttl_hours: int = Field(default=12, ge=1, le=168)
    session_cookie_secure: bool = False
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str | None = None
    allowed_web_origins: str = (
        "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173"
    )

    @property
    def web_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_web_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
