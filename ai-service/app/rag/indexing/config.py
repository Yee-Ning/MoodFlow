"""Configuration for the final offline dense and BM25 indexing stage."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_INDEXING_CONFIG_PATH = Path(__file__).with_name("config") / "v1.yaml"


class PayloadIndexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    field: str = Field(min_length=1, max_length=200)
    data_type: str = Field(
        alias="schema",
        serialization_alias="schema",
        pattern=r"^(keyword|integer|bool|datetime|uuid)$",
    )


class DenseEmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    vector_name: str = Field(default="dense", min_length=1, max_length=100)
    model_name: str = Field(default="Qwen/Qwen3-Embedding-0.6B", min_length=1)
    model_revision: str = Field(min_length=7, max_length=100)
    dimension: int = Field(default=1024, ge=32, le=65_536)
    distance: str = Field(default="cosine", pattern=r"^cosine$")
    max_length: int = Field(default=8192, ge=32, le=32_768)
    normalize: bool = True
    device: str = Field(default="auto", min_length=1, max_length=50)
    local_files_only: bool = False

    def fingerprint_payload(self) -> dict[str, object]:
        """Return only settings that can change the generated vector."""

        return {
            "model_name": self.model_name,
            "model_revision": self.model_revision,
            "dimension": self.dimension,
            "max_length": self.max_length,
            "normalize": self.normalize,
        }


class Bm25IndexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    vector_name: str = Field(default="bm25", min_length=1, max_length=100)
    model_name: str = Field(default="qdrant/bm25", pattern=r"^qdrant/bm25$")
    tokenizer: str = Field(default="multilingual", pattern=r"^multilingual$")
    k: float = Field(default=1.2, gt=0, le=10)
    b: float = Field(default=0.75, ge=0, le=1)
    average_length: float = Field(default=256, gt=0)
    lowercase: bool = True
    disable_stemming: bool = True
    disable_stopwords: bool = True
    idf_modifier: bool = True

    def document_options(self) -> dict[str, object]:
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

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "model_name": self.model_name,
            "options": self.document_options(),
            "idf_modifier": self.idf_modifier,
        }


class QdrantIndexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(default="http://localhost:6333", min_length=1, max_length=2_000)
    api_key_env: str = Field(default="QDRANT_API_KEY", min_length=1, max_length=100)
    collection_name: str = Field(
        default="music_knowledge_v1", pattern=r"^[A-Za-z0-9_-]+$"
    )
    append_release_hash: bool = True
    minimum_server_version: str = Field(default="1.19.0", pattern=r"^\d+\.\d+\.\d+$")
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    prefer_grpc: bool = False
    trust_env: bool = True
    hnsw_m: int = Field(default=16, ge=4, le=128)
    hnsw_ef_construct: int = Field(default=200, ge=4, le=2_048)
    upsert_batch_size: int = Field(default=64, ge=1, le=1_000)
    prune_stale: bool = False
    payload_indexes: tuple[PayloadIndexConfig, ...] = ()

    def resolved_collection_name(
        self,
        release_id: str,
        *,
        indexing_version: str,
        schema_version: str,
        projection_version: str,
    ) -> str:
        if not self.append_release_hash:
            return self.collection_name
        release_hash = release_id.rsplit("-", 1)[-1]
        if not re.fullmatch(r"[0-9a-f]{8,64}", release_hash):
            raise ValueError("knowledge_release_hash_suffix_invalid")
        name = "_".join(
            (
                self.collection_name,
                release_hash,
                indexing_version,
                schema_version,
                projection_version,
            )
        )
        if len(name) > 200:
            raise ValueError("qdrant_collection_name_too_long")
        return name


class IndexingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    indexing_version: str = Field(default="v1", pattern=r"^v\d+$")
    schema_version: str = Field(default="v2", pattern=r"^v\d+$")
    projection_version: str = Field(default="v3", pattern=r"^v\d+$")
    embedding_batch_size: int = Field(default=8, ge=1, le=256)
    retry_count: int = Field(default=2, ge=0, le=5)
    index_partial_knowledge: bool = True
    dense: DenseEmbeddingConfig
    bm25: Bm25IndexConfig = Field(default_factory=Bm25IndexConfig)
    qdrant: QdrantIndexConfig = Field(default_factory=QdrantIndexConfig)

    @model_validator(mode="after")
    def vector_names_must_be_distinct(self) -> "IndexingConfig":
        if self.dense.vector_name == self.bm25.vector_name:
            raise ValueError("dense and BM25 vector names must be distinct")
        fields = [item.field for item in self.qdrant.payload_indexes]
        if len(fields) != len(set(fields)):
            raise ValueError("payload index fields must be unique")
        return self

    def for_release(self, release_id: str) -> "IndexingConfig":
        collection_name = self.qdrant.resolved_collection_name(
            release_id,
            indexing_version=self.indexing_version,
            schema_version=self.schema_version,
            projection_version=self.projection_version,
        )
        return self.model_copy(
            update={
                "qdrant": self.qdrant.model_copy(
                    update={
                        "collection_name": collection_name,
                        "append_release_hash": False,
                    }
                )
            }
        )


def load_indexing_config(path: Path | None = None) -> IndexingConfig:
    selected = (path or DEFAULT_INDEXING_CONFIG_PATH).resolve()
    try:
        payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("indexing config root must be an object")
        return IndexingConfig.model_validate(payload)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"indexing_config_invalid:{selected}") from exc
