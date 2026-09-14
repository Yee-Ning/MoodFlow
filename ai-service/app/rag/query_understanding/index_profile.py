"""Bind online search plans to one verified offline index release."""

from __future__ import annotations

import json
from pathlib import Path

from app.rag.indexing.models import IndexBuildManifest
from app.rag.query_understanding.models import IndexProfile


def _within(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("query_index_manifest_path_outside_project") from exc
    return resolved


def _load_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"query_index_manifest_invalid:{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"query_index_manifest_invalid:{path}")
    return payload


def load_index_profile(project_root: Path, manifest_path: Path) -> IndexProfile:
    root = project_root.resolve()
    selected = manifest_path
    if not selected.is_absolute():
        selected = root / selected
    selected = _within(root, selected)
    try:
        manifest = IndexBuildManifest.model_validate(_load_object(selected))
    except ValueError as exc:
        raise ValueError(f"query_index_manifest_invalid:{selected}") from exc
    if not manifest.ready_for_retrieval:
        raise ValueError("query_index_manifest_not_ready")

    release_path = _within(root, root / manifest.source_release_manifest_path)
    release = _load_object(release_path)
    if release.get("release_id") != manifest.source_release_id:
        raise ValueError("query_index_release_id_mismatch")
    components = release.get("component_versions")
    if not isinstance(components, dict):
        raise ValueError("query_index_release_components_missing")
    registry_version = components.get("entity_registry")
    if not isinstance(registry_version, str):
        raise ValueError("query_index_registry_version_missing")
    return IndexProfile(
        collection_name=manifest.collection_name,
        source_release_id=manifest.source_release_id,
        index_schema_version=manifest.schema_version,
        projection_version=manifest.projection_version,
        registry_version=registry_version,
    )
