from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix="LLM_",
        extra="ignore",
    )

    api_key: SecretStr
    base_url: AnyHttpUrl
    model: str = Field(min_length=1)
    generation_model: str = Field(default="deepseek-flash", min_length=1)
    timeout_seconds: float = Field(gt=0, le=300)
    max_output_tokens: int = Field(default=1024, ge=128, le=8192)
    spring_boot_base_url: AnyHttpUrl = Field(
        validation_alias="SPRING_BOOT_BASE_URL"
    )
    spring_boot_timeout_seconds: float = Field(
        gt=0,
        le=60,
        validation_alias="SPRING_BOOT_TIMEOUT_SECONDS",
    )
    agent_graph_mode: Literal["legacy", "foundation"] = Field(
        default="foundation",
        validation_alias="AI_AGENT_GRAPH_MODE",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
