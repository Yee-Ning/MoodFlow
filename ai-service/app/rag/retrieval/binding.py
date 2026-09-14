"""Bind the online retriever to one immutable ready index release."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.rag.indexing.models import IndexBuildManifest
from app.rag.query_understanding.index_profile import load_index_profile
from app.rag.query_understanding.models import IndexProfile

from .config import RetrievalConfig


@dataclass(frozen=True, slots=True)
class RetrievalIndexBinding:
    manifest_path: Path
    manifest: IndexBuildManifest
    profile: IndexProfile


def load_retrieval_index_binding(
    project_root: Path,
    manifest_path: Path,
    config: RetrievalConfig,
) -> RetrievalIndexBinding:
    root = project_root.resolve()
    selected = manifest_path if manifest_path.is_absolute() else root / manifest_path
    selected = selected.resolve()
    try:
        selected.relative_to(root)
    except ValueError as exc:
        raise ValueError("retrieval_index_manifest_path_outside_project") from exc
    try:
        payload = json.loads(selected.read_text(encoding="utf-8"))
        manifest = IndexBuildManifest.model_validate(payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"retrieval_index_manifest_invalid:{selected}") from exc
    profile = load_index_profile(root, selected)
    _validate_manifest_matches_runtime(manifest, config)
    return RetrievalIndexBinding(
        manifest_path=selected,
        manifest=manifest,
        profile=profile,
    )


def _validate_manifest_matches_runtime(
    manifest: IndexBuildManifest,
    config: RetrievalConfig,
) -> None:
    expected = {
        "dense_vector_name": config.dense.vector_name,
        "dense_model": config.dense.model_name,
        "dense_model_revision": config.dense.model_revision,
        "dense_dimension": config.dense.dimension,
        "dense_distance": "cosine",
        "dense_normalized": config.dense.normalize,
        "bm25_vector_name": config.bm25.vector_name,
        "bm25_model": config.bm25.model_name,
        "bm25_options": config.bm25.query_options(),
    }
    actual = {
        "dense_vector_name": manifest.dense_vector_name,
        "dense_model": manifest.dense_model,
        "dense_model_revision": manifest.dense_model_revision,
        "dense_dimension": manifest.dense_dimension,
        "dense_distance": manifest.dense_distance,
        "dense_normalized": manifest.dense_normalized,
        "bm25_vector_name": manifest.bm25_vector_name,
        "bm25_model": manifest.bm25_model,
        "bm25_options": manifest.bm25_options,
    }
    mismatches = [key for key, value in expected.items() if actual[key] != value]
    if mismatches:
        raise ValueError("retrieval_index_runtime_mismatch:" + ",".join(mismatches))
