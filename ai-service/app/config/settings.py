from functools import lru_cache
import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values, load_dotenv
from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_runtime_environment() -> None:
    """Make non-LLM persistence settings in .env available to runtime components."""
    load_dotenv(ENV_FILE, override=False)


def jev_requested() -> bool:
    """Cheap gate before full Settings validation; never mutates process environment."""
    raw = os.environ.get("JEV_ENABLED")
    if raw is None:
        raw = dotenv_values(ENV_FILE).get("JEV_ENABLED")
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


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
    typesafe_api_key: SecretStr | None = Field(default=None, validation_alias="TYPESAFE_API_KEY")
    jev_enabled: bool = Field(default=False, validation_alias="JEV_ENABLED")
    jev_shadow_mode: bool = Field(default=True, validation_alias="JEV_SHADOW_MODE")
    jev_router_enabled: bool = Field(default=False, validation_alias="JEV_ROUTER_ENABLED")
    jev_router_v4_shadow_enabled: bool = Field(
        default=False, validation_alias="JEV_ROUTER_V4_SHADOW_ENABLED"
    )
    jev_memory_filter_enabled: bool = Field(default=False, validation_alias="JEV_MEMORY_FILTER_ENABLED")
    jev_context_select_enabled: bool = Field(default=False, validation_alias="JEV_CONTEXT_SELECT_ENABLED")
    jev_provider: Literal["typesafe"] = Field(default="typesafe", validation_alias="JEV_PROVIDER")
    jev_model: str = Field(default="jev-latest", min_length=1, validation_alias="JEV_MODEL")
    jev_timeout_seconds: float = Field(default=5, gt=0, le=60, validation_alias="JEV_TIMEOUT_SECONDS")
    jev_router_confidence_threshold: float = Field(default=0.8, ge=0, le=1, validation_alias="JEV_ROUTER_CONFIDENCE_THRESHOLD")
    jev_memory_filter_threshold: float = Field(default=0.5, ge=0, le=1, validation_alias="JEV_MEMORY_FILTER_THRESHOLD")
    jev_context_select_threshold: float = Field(default=0.5, ge=0, le=1, validation_alias="JEV_CONTEXT_SELECT_THRESHOLD")


@lru_cache
def get_settings() -> Settings:
    return Settings()
