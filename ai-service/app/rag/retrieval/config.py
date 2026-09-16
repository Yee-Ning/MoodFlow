"""Versioned online retrieval policy."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_RETRIEVAL_CONFIG_PATH = Path(__file__).with_name("config") / "v1.yaml"


class DenseQueryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    vector_name: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=300)
    model_revision: str = Field(min_length=7, max_length=100)
    dimension: int = Field(ge=32, le=65_536)
    max_length: int = Field(ge=32, le=32_768)
    normalize: bool = True
    device: str = Field(default="auto", min_length=1, max_length=50)
    local_files_only: bool = False
    batch_size: int = Field(default=8, ge=1, le=64)
    query_instruction: str = Field(min_length=10, max_length=1_000)


class Bm25QueryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    vector_name: str = Field(min_length=1, max_length=100)
    model_name: str = Field(pattern=r"^qdrant/bm25$")
    tokenizer: str = Field(pattern=r"^multilingual$")
    k: float = Field(gt=0, le=10)
    b: float = Field(ge=0, le=1)
    average_length: float = Field(gt=0)
    lowercase: bool = True
    disable_stemming: bool = True
    disable_stopwords: bool = True

    def query_options(self) -> dict[str, object]:
        options: dict[str, object] = {
            "tokenizer": self.tokenizer,
            "k": self.k,
            "b": self.b,
            "avg_len": self.average_length,
            "lowercase": self.lowercase,
        }
        if self.disable_stemming:
            options["stemmer"] = {"type": "none"}
        if self.disable_stopwords:
            options["stopwords"] = {}
        return options


class QdrantRetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1, max_length=2_000)
    api_key_env: str = Field(min_length=1, max_length=100)
    timeout_seconds: float = Field(gt=0, le=60)
    prefer_grpc: bool = False
    trust_env: bool = True
    minimum_server_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    hnsw_ef: int = Field(default=128, ge=16, le=2_048)


class FusionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rrf_k: int = Field(default=60, ge=1, le=1_000)
    dense_weight: float = Field(default=1, gt=0, le=10)
    lexical_weight: float = Field(default=1, gt=0, le=10)
    per_leg_top_k: int = Field(default=80, ge=1, le=1_000)
    overfetch_multiplier: int = Field(default=2, ge=1, le=5)
    maximum_legs: int = Field(default=6, ge=1, le=20)


class SelectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_candidates: int = Field(default=100, ge=1, le=500)
    default_document_cap: int = Field(default=2, ge=1, le=20)
    broad_document_cap: int = Field(default=4, ge=1, le=20)
    source_cap: int = Field(default=5, ge=1, le=50)
    exploratory_entity_fallback_below: int = Field(default=12, ge=0, le=40)
    worldline_document_limit: int = Field(default=8, ge=6, le=10)
    worldline_token_budget: int = Field(default=6_000, ge=500, le=30_000)


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieval_version: str = Field(pattern=r"^v\d+$")
    dense: DenseQueryConfig
    bm25: Bm25QueryConfig
    qdrant: QdrantRetrievalConfig
    fusion: FusionConfig = Field(default_factory=FusionConfig)
    selection: SelectionConfig = Field(default_factory=SelectionConfig)

    @model_validator(mode="after")
    def validate_config(self) -> "RetrievalConfig":
        if self.dense.vector_name == self.bm25.vector_name:
            raise ValueError("dense and BM25 vector names must be distinct")
        if self.selection.broad_document_cap < self.selection.default_document_cap:
            raise ValueError("broad document cap cannot be smaller than default")
        return self


@lru_cache
def load_retrieval_config(
    path: Path = DEFAULT_RETRIEVAL_CONFIG_PATH,
) -> RetrievalConfig:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"retrieval_config_invalid:{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"retrieval_config_invalid:{path}")
    return RetrievalConfig.model_validate(payload)
